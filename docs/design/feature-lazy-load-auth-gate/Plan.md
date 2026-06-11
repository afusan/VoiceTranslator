# feature/lazy-load-auth-gate 作業計画

起票: 2026-06-11 / 親ブランチ: feature/composite-cloud-backends(stacked)

## 背景

ドッグフーディングで「バックエンド変更 → ロード中に別バックエンドを選ぶと UI が固まる」
事象が報告された。調査の結果(tmp/report6.md):

- フリーズの直接原因: UI スレッドが `set_setting` の evict で `_load_lock` を取りにいく一方、
  `vt_reload_<layer>` スレッドが**モデル構築の間ずっと**同ロックを保持している。
- 「変更即ロード」は導入当時(全レイヤ LOADED でないと開始不可)の前提に依存していたが、
  Phase B(開始ボタン常時押下可・押下時ロード)でその前提は消滅している。
- 認証 UX に既知の穴: `Missing Credentials` 表示はインスタンス構築後にしか出ない/
  鍵あり未検証は `Loaded`(緑)と表示されるのに Start で gate に弾かれる/
  ready_state の開始ボタン無効化はインスタンス状態しか見ていない。

## 決定事項(ユーザ確認済み 2026-06-11)

1. 変更即ロードは**完全廃止**(温めたい backend は auto_load / ↻ ロード)。
2. 認証ステータスの表示文言は**既存 ModelStatus に合わせて英語**
   (`Missing Credentials` は既存値を流用 / 未検証は `Not Verified`)。
3. ブランチは 1 本(本ブランチ)。途中で master へは入れない。

## Phase 構成

### Phase 1: 変更即ロードの廃止(★1)
- `set_setting("backends", layer, name)` の反応系を「evict + INIT emit」のみにする。
  `vt_reload_<layer>` スレッドは廃止。text_only の TTS/Output 特例分岐も不要になる。
- 実ロード経路は既存 3 本に集約: 開始ボタン押下 / ↻ ロード / 起動時 auto_load。
- 詳細ダイアログ保存(evict のみ・2026-05-30 決定)と同じ規則になり、設計が一本化される。
- テスト: 「backend 変更で再ロードが走る」を検証していたテストを
  「evict + INIT のみ、ロードは走らない」の検証に書き換える。

### Phase 2: 認証状態の静的表示 + 開始ガード(★2 ★3)
- `AuthState` 列挙を `common/types.py` に追加:
  `NOT_REQUIRED / MISSING / UNVERIFIED / VERIFIED`。
- `CredentialsService.get_auth_state(layer, backend_name)` で静的計算
  (registry の `requires_credentials`+`credential_spec` / store の保存鍵 / verified フラグ。
  **インスタンス不要**)。AppController に互換窓 `get_auth_state(layer)`(選択中 backend)。
- 表示(gui/logic 純関数 + 固定文字列テスト):
  - `gui/logic/auth_display.py`: AuthState → 行ステータス上書き
    (`MISSING` → `Missing Credentials` 赤 / `UNVERIFIED` → `Not Verified` 琥珀 /
    それ以外 → None = 通常表示)。
  - `palette.py`: `ModelStatus.MISSING_CREDENTIALS` に赤を定義(現状 fallback グレー)。
- SettingsPanel: `_apply_status` で auth 上書きを参照(吸収/なし表示が最優先のまま)。
- ready_state: 入力に auth 状態を追加。優先順位
  `auth MISSING(または instance MISSING_CREDENTIALS)> auth UNVERIFIED > DOWNLOADING > …`。
  ボタン文言(日本語のまま): 既存「認証情報未設定」+ 新規「認証未検証」。
- 配線: 認証成功/失効時に UI へ伝わるイベントが現状無い →
  `verify_and_save_credentials` 成功時と `invalidate_verification` で
  `settings` イベント(keys=("credentials", backend))を emit し、
  SettingsPanel / ControlPanel が再計算する。
- 認証成功後の後処理(Phase F1 の reload)は「evict + INIT」に簡素化
  (lazy 方針に統一。次の Start / ↻ ロードで新しい認証情報のインスタンスが入る)。
- 押下時の `_check_missing_credentials_gate` は**最後の防波堤としてそのまま残す**。

### Phase 3: ロードエンジンのロック再構成(案 B)
- 原則: **UI スレッドは `_load_lock` で長時間待たない** /
  **ロックを保持したままモデル構築をしない**。
- 実装:
  - モデル構築(`self._create`)をロック外に出す。ロックは `_backends` /
    `_backend_subscriptions` / in-flight 集合 / 世代カウンタの短い読み書きのみ。
  - `_layer_generations: dict[LayerKind, int]`: evict のたびに +1。
    構築完了時に世代が変わっていたら結果を捨て、**最新の選択をロードし直す**
    (last-write-wins。構築は中断できないため完走 → 破棄)。
  - `_inflight: set[LayerKind]` + Condition: 同一レイヤの二重構築を防止。
    待つのは loader スレッド同士のみ(UI スレッドはロード API を呼ばない)。
- Phase 1 適用後の残フリーズ経路(開始 / ↻ ロード中のバックエンド変更)もこれで解消。
- 留意: Start のロード中に backend を変更した場合、Start は新しい選択でロードし直して
  起動する(認証 gate は Start 押下時点の選択で判定済み。未検証 backend に切り替わった
  場合は実行時エラー経路で処理される — 許容するエッジケースとして記録)。

## 恒常ドキュメント更新
- manual.md / Class.md: 「バックエンド変更で自動ロード」の記述を現挙動
  (変更は選択のみ・ロードは開始/↻ロード/auto_load)に更新。認証表示の説明を追加。

## Phase 4: ドッグフーディング追加要望(2026-06-11 起票)

1. **動作中のバックエンド変更ロック**: 動作中に選択を変えても動作には反映されず
   「何で動いているのか」が表示と食い違うため、動作中は全バックエンド行の
   プルダウン / 設定ボタンを disable する。
   - AppController に `running` イベント(bool)を追加(起動完了 / 停止で emit)。
     Panel 間同期は直接参照でなくイベント購読(UI 規約)。
   - SettingsPanel が購読し `_apply_running_lock_visual` で一括 disable / 復元。
     復元後は吸収 / TTS=(なし) の disable を再適用。編成復帰・TTS=(なし) 解除の
     経路も `_interactive_state()` 経由で動作中は normal に戻さない。
   - devices / languages は動作中変更に対応済み(自動 restart / 即時反映)なので対象外。
2. **設定再読込の差分更新**: `load_settings` は全レイヤ破棄をやめ、
   「選択 backend 名 + その backend の backends_config」が変わったレイヤだけ
   evict + INIT。変わっていないレイヤはロード済みインスタンスと状態表示を維持。
3. **(調査)「設定を保存」で入力プロセスが無効化される件**: 原因特定済み・修正は未実施。
   `save_settings` → `_strip_volatile_inputs_before_save` が「PID を永続化しない」
   (A-7 方針)を実現するために **実メモリの `devices.input` を空文字に書き換えて
   から保存している**。ファイルに残さないのは意図どおりだが、実行中セッションの
   選択まで消えるのは副作用(しかも静かな書き換えで settings イベントも出ない)。
   修正案: 保存時はメモリを触らず「書き出すデータのコピー」から PID を除外する
   (ConfigStore.save に書き出し前変換を渡す等)。ユーザ確認後に対応する。
