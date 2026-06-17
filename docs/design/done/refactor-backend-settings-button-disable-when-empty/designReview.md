# designReview

## レビュー 1 巡目

**判定: GO**

### 観点 0: 上位整合性

指摘なし。

Plan.md は request.md(「変更対象がなければ選択肢を無効に」)と checkedRequest.md の目標・スコープ・非目標にすべて沿っている。request / checkedRequest 自体にも矛盾はない。checkedRequest が示す「判定は backend カタログ/スキーマから空集合かどうかで決める」方針は Plan の `visible_fields()` 利用で正しく具体化されている。

### 観点 1: 役割 / 単一責任

指摘なし。

`gui/logic/settings_button.py` に判定を純関数として切り出し、Panel は呼ぶだけという分離は CLAUDE.md UI 規約「判断は logic、widget は塗るだけ」に合致。`Class.md` への追記も計画に含まれている。

### 観点 2: 既存資産の再利用

指摘なし。

既存の `visible_fields()` をそのまま委譲元に使い、新規のスキーマ API を追加しない判断は適切。gui/logic パッケージの既存パターン(ready_state, auth_display 等)に倣ったファイル構成で一貫している。

### 観点 3: 配布方針

指摘なし。device / GPU 関連の変更を含まないため発動条件外。

### 観点 4: スコープ

指摘なし。

変更対象は「設定ボタンの enabled/disabled 制御」に限定され、非目標(スキーマ変更、ツールチップ等)に踏み込んでいない。

### 観点 5: テスト容易性

- **[軽] `_sync_all_settings_btn_states` と `_apply_tts_none_visual` の呼び出し順序への依存が暗黙的。** Plan は `_apply_absorbed_visuals` 末尾(= `_apply_tts_none_visual` の後)に `_sync_all_settings_btn_states` を置くが、`_on_backend_change` の TTS 分岐で `_apply_tts_none_visual` が再度呼ばれるとボタン状態が一瞬 "normal" に戻り、その後 `_sync_settings_btn_state(layer, internal)` で再修正される。現状の Plan でも最終状態は正しくなるが、順序依存が暗黙的であるため、Panel smoke テストに**「TTS を (なし) から mms に変更したとき設定ボタンが disabled のまま」のケース**を 1 件追加しておくと安全網になる。実装判断で吸収できる範囲。
  - 実装者回答: smoke テスト `TestSettingsBtnOnBackendChange::test_tts_none_to_mms_keeps_disabled` を追加して順序依存リスクを確認した。実装時に判明した追加の設計変更点: `_apply_tts_none_visual` の TTS 行設定ボタン処理で「TTS=(なし)でないとき `_interactive_state()` に戻す」部分が `has_settings` で設定した disabled を上書きする問題があった。`_apply_tts_none_visual` を「TTS=(なし)のときだけ disabled にする」に変更し、それ以外のときは設定ボタンに触れないようにした。`_apply_absorbed_visuals` 末尾では `_sync_all_settings_btn_states` → `_apply_tts_none_visual` の順にすることで「has_settings ベースで設定 → TTS=(なし)で Output を上書き」という明示的な適用順序にした。

