# feature/runtime-flex-and-input — 計画

ドッグフーディングで挙がった「動作中の柔軟性」「出力構成の選択」「入力 backend の細分化」をまとめて扱う。
段階的にブランチを切ってマージしていく前提の **メタ計画**。実装ブランチはここから派生する。

---

## 1. 要望整理

| ID | 要望 | 要点 |
|----|-----|-----|
| **R1** | UI を「バックエンド/デバイス/翻訳」で個別に畳めるようにする | 既存の `CollapsibleSection`(SettingsPanel 全体を畳む)を 3 つに分割する |
| **R2** | 入出力デバイス・言語を **動作中** に変更可能にする | 言語は容易・デバイスは慎重(無停止 swap は別件) |
| **R3** | 出力バックエンドなしモード(翻訳テキストで終了)を追加 | TTS / Output を動かさず、レジャは Translator 完了時に解放 |
| **R4** | 入力 backend を「デバイス種類単位」に細分化(将来 ProcTap 連携の土台) | per-process キャプチャ等を別 backend として並べられる構造に |

※ `R2` の「バックエンドの変更は考えていない」は明示済み(本計画でも対象外)。

---

## 2. Phase 分割と推奨順序

依存と難易度を踏まえ、**Phase 1 → 2 → 3 → 4 → 5** の順で進めるのが最短。
各 Phase は独立ブランチで切る前提(マージ単位を細かく保つ)。

| # | ブランチ案 | スコープ | 難易度 | 状態(2026-06-05 時点) |
|---|---|---|---|---|
| **P1** | `feature/ui-sections-split` | R1(UI 折り畳み 3 分割) | 小 | ✅ 完了・master マージ済(`d2f2b72`) |
| **P2** | `feature/dynamic-languages` | R2 のうち **言語の動的変更** | 小 | ✅ 実装・テスト完了(`e10e6a7`)。master マージ待ち |
| **P3** | `feature/text-only-output` → `refactor/text-only-via-tts-none` | R3(出力 backend なしモード)。**最終形は TTS=(なし) 選択値から派生** | 中 | ✅ 実装・テスト完了(`69fea7c`)。master マージ待ち |
| **P4** | `feature/dynamic-devices` | R2 のうち **入出力デバイスの動的変更**(停止→再開方式) | 中 | ✅ 実装完了(`96cf01b`)。AppController.restart_pipeline_async 追加 + SettingsPanel が動作中切替時に発火 |
| **P5** | `feature/capture-backend-split` | R4(入力 backend のデバイス単位への分解、ProcTap 連携の土台) | 中〜大 | ✅ 実装完了(`9dad6b0`)。master マージ待ち |
| **P6-1** | `feature/capture-kind` | ProcTap 取り込み 段階 1(`CaptureKind` 概念導入 + Python 3.12 化) | 小 | ✅ 実装完了。pendList 上で段階 2/3 を起票 |
| **P6-2** | `feature/proctap-backend` | ProcTap 取り込み 段階 2(`ProcTapCaptureBackend` 本体 + リサンプル) | 中 | ✅ 実装完了。small 16 件 + large 1 件 pass |
| **P6-3** | `feature/proctap-process-list` | ProcTap 取り込み 段階 3(`pycaw` 連携でプロセス列挙 + 試聴メータダイアログ) | 中 | ✅ 実装完了。`process_enumerator` 新規 / `ProcessSelectDialog` 新規 / SettingsPanel に「プロセス選択…」ボタン / ControlPanel 未選択時 disable / A-7 で PID 非永続化。`docs/design/feature-proctap-process-list/` 参照 |
| **P6-3-fix** | `refactor/process-peak-worker` | P6-3 のスレッド処理整理(GUI スレッドでの COM モード競合解消) | 小〜中 | ✅ 実装完了(`2d0d707`)。永続 COM ワーカースレッド `_PeakWorker` 1 個に集約、5fps 内部 poll で peak を atomic 保持、GUI は atomic 読みだけ。スレッド生成 30/sec → 0、CoInitialize 30/sec → 1/process |

順序の理由:
- **P1 を先に**: 触る範囲が狭く副作用がない。ドッグフーディング体験が一気に上がる。後続の UI 改修と競合しにくい形に整えてから動的変更系へ。
- **P2 → P3**: 言語の動的変更は Coordinator 内 `_src_lang`/`_tgt_lang` への注入だけで完結。P3(出力なしモード)は Coordinator のスレッド構成に踏み込むので、軽い動的変更で「Coordinator に外から値を流す」パターンを先に確立しておく。
- **P3 → P4**: P3 で Coordinator/AppController/SettingsPanel/ControlPanel に対する分岐パターンを通っているので、P4 の「動作中 → 停止 → 再開」処理が乗せやすい。
- **P5 を最後に**: 構造改修(backend を複数並べる + UI 二段化)が一番大きい。先に他 Phase を入れて UI が安定してから手をつける方が安全。ProcTap 本体は別ブランチ。

---

## 3. 各 Phase の詳細

### P1: UI セクション分割(R1)

**目的**: SettingsPanel を以下 3 セクションに分け、各々の `CollapsibleSection` で個別に開閉可能にする。

| セクション | 含む項目 |
|---|---|
| **バックエンド** | 6 レイヤの backend プルダウン + ステータス + 設定ボタン |
| **デバイス** | 入力デバイス / 出力デバイス |
| **翻訳** | 入力言語 (src) / 出力言語 (tgt) |

**実装方針**:
- `SettingsPanel` 内に 3 つの `CollapsibleSection` を縦に並べる。既存の `_build_widgets` を 3 メソッドに分割。
- 開閉状態の永続化キー: `ui.collapsed.{backends, devices, languages}`(MainWindow の `ui.collapsed.settings_panel` は廃止)。
- 既存セパレータ(レイヤ実装グループとデバイス選択の間の罫線)は削除(セクション境界で代替)。
- ログ出力先 / 設定保存ボタンは「バックエンド」「翻訳」のどちらにも属さないので、3 セクションの下に **非折り畳みの共通行** として残す。

**触るファイル**: `src/voice_translator/gui/settings_panel.py` / `src/voice_translator/gui/main_window.py`。

**テスト**: small で 3 セクションの開閉状態が ConfigStore に保存/復元されることを確認(モック ConfigStore で済む)。

---

### P2: 言語の動的変更(R2 — 言語)

**目的**: 動作中に SettingsPanel で入力/出力言語を変えたら、**次の発話から** 新言語が適用される。

**実装方針**:
- `PipelineCoordinator.set_languages(src: str | None, tgt: str | None)` を追加。内部の `self._src_lang` / `self._tgt_lang` を atomic に差し替える(両 worker は次の payload 構築時に最新値を読む)。
- `AppController.set_setting("languages", "src" | "tgt", value)` で動作中 (`is_running`) なら Coordinator に転送。
- **進行中の発話**: 既にキューに入っている `RawPayload.src_lang_hint` は古い値のまま流れる(各発話の hint は capture 時点で確定する設計)。これは仕様として明示する。
- `tgt_lang` は Translator/TTS で参照されるが Coordinator が毎回 `self._tgt_lang` を読む構造なので、差し替えれば次の発話から効く。

**触るファイル**: `pipeline.py` / `app_controller.py` / `settings_panel.py`(`_on_src_lang_changed`/`_on_tgt_lang_changed` に live 反映を追加)。

**テスト**: middle で「動作中に `set_setting('languages','tgt','en')` → 次発話の TranslatedPayload が `tgt_lang='en'`」を確認。

---

### P3: 出力バックエンドなしモード(R3)— ✅ 完了

**最終形(refactor/text-only-via-tts-none 反映後)**:
- `pipeline.output_mode` の独立 ConfigStore キーは**持たない**。
- 出力モードは `backends.tts` の値から派生する: `"none"`(UI「(なし)」)→ text_only、それ以外 → audio。
- SettingsPanel の TTS プルダウン末尾に「(なし)」を追加、選択時に Output 行をグレーアウト。

**目的**: 「翻訳テキストの表示で完了」モードを追加。TTS は実行されず、レジャは Translator 完了時点で解放される。

**設計判断**: 「Noop TTS/Output backend を入れる」案ではなく、**Coordinator のモード切替**で対応する。理由:
- Noop backend だと TTS スレッド/Output スレッドは「動いて即返す」状態で残り、`on_text_ready` のタイミングも従来の TTS 完了時のまま(分かりにくい)。
- モード切替なら TTS/Output スレッドを起動せず、Translator 完了 = テキスト確定 = 履歴出力 と素直に対応する。

**実装方針**:
- `pipeline.output_mode = "audio" | "text_only"`(既定 `audio`)を ConfigStore に追加。
- `PipelineCoordinator` のコンストラクタに `output_mode` を渡す。`text_only` のとき:
  - **起動**: Input / ASR / Translator スレッドのみ起動(TTS / Output は起動しない)。
  - **Translator 完了直後**: `_translator_loop` 末尾で `on_text_ready(ledger.peek(seq_id))` を発火 → `ledger.pop(seq_id)`(バッファ即解放)。`translated_queue` には push しない。
  - **`on_utterance_done`**: text_only では呼ばない(`on_text_ready` で履歴 + jsonl 書き出しを兼ねる方針に変える)。
- TTS / Output レイヤの backend ロードは `output_mode=text_only` のとき **skip**(`AppController.load_models` / `load_auto_load_layers_async` で対応レイヤを除外)。
- SettingsPanel のバックエンドセクションで `output_mode=text_only` を切替可能にする(チェックボックス or 「出力モード」プルダウン)。`text_only` のとき TTS/Output 行は「(なし)」表示でグレーアウト。
- ControlPanel: 「全 LOADED」判定から TTS/Output を除外。レイテンシ計算は `t_translate` までで打ち切り。
- jsonl ログは `_handle_text_ready` 側でも書けるよう、`TranslationLogger.write_record` の入力(record dict)を `audio` モードと揃える。

**触るファイル**: `pipeline.py` / `app_controller.py` / `settings_panel.py` / `control_panel.py` / `config_store` 既定値(あれば) / Class.md / Architecture.html を更新。

**テスト**:
- small: `text_only` モードで Coordinator が TTS/Output スレッドを起動しないこと、Translator 完了で `on_text_ready` が呼ばれ ledger が空になること
- middle: 縦通し(audio 用 fixture を流して text のみ出ること)
- 既存 audio モードの挙動回帰

---

### P4: 入出力デバイスの動的変更(R2 — デバイス)

**目的**: 動作中に SettingsPanel で入出力デバイスを変えたとき、**ユーザに気づかれない程度の中断**で新デバイスに切り替わる。

**実装方針(現実解)**:
- **「動作中に変更 → 自動 restart(stop → start)」を採用**。capture/output だけを swap する無停止方式は実装複雑度が跳ねるので別件(pendList)。
→無停止は非サポートでOK.また発話中に切り替えた場合の挙動については、出力の場合、停止orそのバッファの再生後、次のバッファから出力先を切り替えるイメージであってるか？（想定と難易度が変わるか？）
- `AppController.restart_pipeline_async()` を追加(stop_pipeline → start_pipeline_async)。
→どういうことをやるイメージ？
- SettingsPanel の `_on_capture_changed` / `_on_output_changed` で `is_running` 中なら restart を発火。
- UX: NotificationBanner に「入力(出力)デバイスを切り替えました(再開中…)」を表示。
- 既存の「`set_setting("backends",...)` で当該レイヤ再ロード」と同じパターン。差は Coordinator も止める/再開する点。

**触るファイル**: `app_controller.py` / `settings_panel.py` / `control_panel.py`(restart 中の UI 表示)。

**テスト**: middle で「動作中 → デバイス変更 → 自動 restart 完了」を fake capture/output で確認。

---

### P5: 入力 backend のデバイス単位への分解(R4)

**目的**: 現状「`SoundcardCaptureBackend` 1 つだけ」だった構造を、「複数の入力 backend が並ぶ → ユーザが backend を選び、その下のソースを選ぶ」二段構造に拡張する。ProcTap(per-process キャプチャ)の取り込み土台を作る。

**現状の問題**:
- 入力 backend は 1 つで、`source_id` で全てを表現していた。
- ProcTap は別プロセス/別実装で、`SoundcardCaptureBackend` には収まらない(WASAPI Process Loopback + ProcTap モジュール経由)。

**実装方針**:
- 構造は **既存のレイヤ機構のまま**(`BackendRegistry.register(LayerKind.CAPTURE, name, ...)`)。`SoundcardCaptureBackend` の他に `ProcTapCaptureBackend` を将来並べられるように整える。
- SettingsPanel の「入力デバイス」を **2 段プルダウン** に変える:
  - 上段: 入力 backend(`soundcard` / 将来 `proctap`)
  - 下段: 当該 backend の `list_sources()` 結果
- ConfigStore キー:
  - 既存 `backends.capture` をそのまま使う(backend 名)
  - 既存 `devices.input` をそのまま使う(source_id)
  - ただし source_id の解釈は「現在選ばれている capture backend に限定」される
- backend 切替時のソース一覧 refresh は `SettingsPanel._populate_devices_into_dropdowns` 側で対応。
- ProcTap 連携(`ProcTapCaptureBackend` の実装)は **本 Phase の対象外**。pendList で別ブランチ。

**触るファイル**: `settings_panel.py`(2 段化) / `app_controller.py`(list_capture_sources を選択中 backend ベースに) / 必要なら `backend_registry.py` / Class.md / Architecture.html 更新。

**テスト**: small で「capture backend 切替 → list_sources が新 backend のものに切り替わる」を確認。

---

## 4. pendList への追加提案

P4・P5 の発展系として、本ブランチ群では着手しない項目を起票しておく。

### 提案 A: 入出力デバイスの **無停止 swap**
- 内容: P4 では stop→start の自動 restart に倒すが、無停止 swap(Input/Output スレッドはそのまま、capture/output backend だけ差し替え)も検討余地あり。
- 見送り理由: `SoundcardCaptureBackend` は内部に `sc.recorder` の context manager を握っているため、動作中 swap には Input スレッド側で「次フレーム取得前に再 enter する」flag 駆動が必要。設計負荷に対して体感差(stop→start でも 1〜2 秒)が小さい段階で先送り。
- 着手トリガ: P4 完了後、stop→start の体感ラグが運用上 NG と判明したら。
→動作を見てから判断するが、運用上は許容の見込み。

### 提案 B: ProcTap backend 実装(per-process キャプチャ)
- 内容: `C:\work\claudeWork\ProcTap` を使う `ProcTapCaptureBackend` の実装。WASAPI Process Loopback ベース。
- 見送り理由: ProcTap モジュール側の API・ビルド成果物の形(DLL/exe/サイドカー)が定まってから連携設計したい。P5 で「並べられる構造」は整える。
- 着手トリガ: ProcTap の連携 I/F(ctypes/サイドカー stdio etc.)が確定したら。
- 関連: pendList の「[2026-05-26 / 改 2026-05-29] 入力処理レイヤーの改善案」エントリと統合する。

### 提案 C: 言語の動的変更における「進行中発話」の扱い
- 内容: P2 では「次の発話から反映」とし、すでにキューに入っている発話は古い言語のまま流れる仕様。これでユーザ体験が NG ならキャンセル/再投入機構を検討。
- 見送り理由: 体験悪化の頻度が読めない。実機で見てから判断したい。
- 着手トリガ: 「言語を変えたつもりが古い言語の翻訳が出てくる」の苦情が出たら。
→設定変更後にバックエンドが処理するデータが変わっていれば、実行中の翻訳やTTSパイプラインはすでにあるものはそのまま処理してかまわない認識だがｍそれでも問題があるか？
---

## 5. 共通方針

- **ブランチ運用**: 5 Phase それぞれを `feature/<name>` ブランチで切り、ユーザレビュー後 `--no-ff` マージ。
- **マージ済みフォルダ**: 各 Phase ブランチ完了で `docs/design/<branch-name>/` を `docs/design/done/<branch-name>/` へ移動。本メタ計画(`feature-runtime-flex-and-input/`)は **全 Phase 完了まで進行中フォルダに留め置く**。
- **設計ドキュメント更新**: P3 / P5 は `Architecture.html` / `Class.md` への反映が必要(挙動 + 構造に影響)。P1 / P2 / P4 は本 Plan + 各 Phase の Plan.md で十分。
- **CLAUDE.md「コードパス 1 本」原則**: P3 の text_only モードは「if mode == text_only」分岐を Coordinator 内で持つことになる。スレッド構成自体が違うので分岐 1 か所で吸収する(=役割が違うので分岐は妥当、Strategy 化までは過剰)。

---

## 6. 次のアクション

### 完了済み(2026-06-05)
- ✅ P1 (`feature/ui-sections-split`): UI 設定パネル 3 セクション分割 → master マージ済
- ✅ P2 (`feature/dynamic-languages`): 動作中の翻訳言語切替 → master マージ待ち
- ✅ P3 (`feature/text-only-output` + `refactor/text-only-via-tts-none`):
  TTS=(なし) で text_only モード → master マージ待ち

### これから
1. P2 / P3 を master にマージ(ユーザ承認待ち)。
2. P4 (`feature/dynamic-devices`): 動作中の入出力デバイス変更(停止→再開方式)。
   実装着手前に、本 Plan に追記されたユーザ質問への回答を整理してから始める:
   - 出力デバイス切替時の挙動: 「停止→再開」で即切替(再生中バッファは捨てる)
   - `AppController.restart_pipeline_async()`: `stop_pipeline()` を同期で呼んだあと、
     Loader スレッドで `start_pipeline_async()` を続ける単純なラッパー
3. P5 (`feature/capture-backend-split`): 入力 backend のデバイス単位への分解。
   ProcTap モジュール側の I/F が見えてきてから本格着手。
