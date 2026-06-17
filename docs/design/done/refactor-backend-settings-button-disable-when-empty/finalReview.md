# finalReview

## レビュー 1 巡目

**判定: GO**

### 観点 0: 上位整合性

指摘なし。

request.md(「変更対象がなければ選択肢を無効に」)→ checkedRequest.md(判定は backend カタログ/スキーマから空集合かどうかで決める、ハードコードリスト不可)→ Plan.md(`visible_fields()` に委譲する `has_settings` 純関数を `gui/logic/` に置く)→ 実装。一貫しており、要求の取りこぼし・スコープ外の混入・解釈の歪みは無い。非目標(スキーマ変更、ツールチップ、設定ボタン以外)にも踏み込んでいない。上流ドキュメント間の矛盾も無い。

### 観点 1: 設計整合(コード現実)

- **[軽] `gui/logic/__init__.py` の依存契約と `settings_button.py` の実際の依存**:
  `gui/logic/__init__.py` は「依存は common 配下の純粋モジュールと標準ライブラリのみ」と宣言しているが、`settings_button.py` は `voice_translator.gui.layer_settings_schema` に依存する(deferred import)。`layer_settings_schema` は宣言的データ中心のモジュールで customtkinter を直接 import しないため実害は無いが、パッケージの stated contract との乖離がある。対応案: `__init__.py` の docstring に「`layer_settings_schema` 等の GUI 内の純宣言モジュールへの依存は許容」を追記する。
  - worker回答: `__init__.py` の docstring に「GUI 内の純宣言モジュール(layer_settings_schema 等)への依存は許容する(deferred import で循環参照を回避)」を追記した。

上記以外は Plan.md どおりの実装。`has_settings` は `visible_fields()` への薄いラッパとして純関数に切り出されており、単一責任・既存資産の再利用とも整合。`_settings_btns` 辞書の追加と `_backend_rows` との関係(同一 widget への二重参照)も明示的にコメントされている。

### 観点 2: テストの十分性と健全性

- **[中] `_apply_tts_none_visual` が Output 行の設定ボタンを `_interactive_state()` で上書きする潜在的問題のテスト欠落**:
  `_apply_tts_none_visual` 内の Output 行処理(settings_panel.py 357-362行)で、TTS が「(なし)」でないとき Output 行の `CTkButton` を `self._interactive_state()` = "normal" に設定する。これは `_apply_absorbed_visuals` 内で `_sync_all_settings_btn_states()` → `_apply_tts_none_visual()` の順に呼ばれるため、`_sync_all_settings_btn_states` が OUTPUT 設定ボタンに設定した状態を `_apply_tts_none_visual` が上書きする。**現時点では OUTPUT soundcard は `has_settings == True` なので顕在化しないが、将来 Output に設定項目ゼロの backend が追加された場合にボタンが誤って normal になる**。対応案: 以下のいずれか(worker 判断):
  (a) `_apply_tts_none_visual` の Output 行の `CTkButton` 処理を TTS 行と同様に「`is_none` のときだけ disabled にする」形に変更する(TTS 行で既にやった修正と同じパターン)
  (b) 現状は顕在化しないため `pendList.md` に記録して次回の Output backend 追加時に対処する
  - worker回答: (a) を採用。`_apply_tts_none_visual` の Output 行 CTkButton 処理を TTS 行と同様に「`is_none` 時のみ `disabled` にし、`is_none=False` 時は触れない」形に変更した。コメントにも「`_sync_all_settings_btn_states` が管理している、将来の設定項目ゼロ backend でも上書きしない」旨を明記。テスト `test_output_settings_btn_not_overwritten_by_apply_tts_none_visual` を追加。`settings_panel` モジュール内の `has_settings` を `monkeypatch` で OUTPUT が `False` を返すよう差し替え、TTS (なし) → sapi への切替後も Output 設定ボタンが `disabled` のままであることを検証(1398 passed)。

- **[軽] `_sync_all_settings_btn_states` 単体のテスト欠落**: `_sync_all_settings_btn_states` は running ロック解除と `_apply_absorbed_visuals` から呼ばれるが、直接的なテストが無い。既存の running lock テスト(`test_stop_reenables_and_reapplies_overrides`)は通っているが、running ロック解除後に「空設定 backend の設定ボタンが disabled のまま」であることを明示的に検証するテストは無い。ただし `_apply_absorbed_visuals` 経由で間接的にカバーされており、テスト爆発を避ける判断として許容範囲。
  - worker回答: 現状の間接カバレッジで許容範囲とする判断を維持する。`_apply_absorbed_visuals` 経由の既存テストで十分にカバーされており、`_sync_all_settings_btn_states` 単体を直接叩く新テストを加えると重複が生まれる。

### 観点 3: 発動条件外(スキップ)

backend 追加・device 関連コード変更を含まない。

### 観点 4: セキュリティ

指摘なし。認証情報の取り扱いや外部入力に関する変更は含まない。

### 観点 5: プロジェクト規約遵守(CLAUDE.md 系)

指摘なし。

- 「モック対策」の防御 try/except は本番コードに追加されていない(widget 破棄後の `configure` 例外は既存パターンと同一で CLAUDE.md 許容範囲)。
- i18n 規約: 文言追加なし。`tr()` の新規呼び出しなし。CJK 直書きなし(`test_i18n.py` 全 pass 確認済み)。
- 購読: 新規購読の追加なし。`_settings_btns` は widget 参照であり Subscription ではないため `destroy()` での解除は不要。
- コメント方針: 追加コメントは WHAT ではなく WHY(順序依存の理由)を説明しており適切。

### 観点 6: 役割表明とドキュメント反映

指摘なし。

- `settings_button.py` の docstring 冒頭に「設定ボタンの enabled/disabled 判定を行う純関数」と役割が明記されている。
- `Class.md` の `gui/logic` パッケージ内訳に `settings_button.py` が追記されている。
- `Class.md` の `auto_load` 関連の記述も同時に清掃されている(直前タスクからの残り。触ったファイルで経緯を削る規約に沿う)。

### 観点 7: スコープ・複雑性

指摘なし。

変更は「設定ボタンの enabled/disabled 制御」に完全に限定されている。過剰な抽象化は無い(`has_settings` は `visible_fields` への 1 行委譲の薄いラッパ)。Plan.md の非目標(スキーマ変更、ツールチップ、設定ボタン以外)を踏み越えていない。`_apply_tts_none_visual` の修正(TTS 行の設定ボタン処理を `is_none` 限定に変更)は designReview で合意された設計変更であり、スコープ内。

## レビュー 2 巡目

**判定: GO**

1 巡目の指摘 3 件に対する worker の応答と実装変更を検証した結果、全件解決済みと判定する。

### 観点 1: [軽] `gui/logic/__init__.py` 依存契約の乖離 → **解決**

worker は `__init__.py` の docstring に「GUI 内の純宣言モジュール(layer_settings_schema 等)への依存は許容する(deferred import で循環参照を回避)」を追記した。実コードを確認し、記述が正確であること(layer_settings_schema は widget を import しない宣言的データ定義であり、deferred import で使用している)を検証した。stated contract と実態の乖離は解消された。

### 観点 2: [中] `_apply_tts_none_visual` Output 行 CTkButton 上書き → **解決**

worker は対応案 (a) を採用し、Output 行の `CTkButton` 処理を TTS 行と対称化した。修正内容を確認:

- `settings_panel.py` 366-369 行: `isinstance(w, ctk.CTkButton)` の分岐で `is_none` 時のみ `disabled` に設定し、`is_none=False` 時は触れない。TTS 行(385-390 行)と完全に対称。
- 修正前は `isinstance(w, (ctk.CTkOptionMenu, ctk.CTkButton))` でまとめて `is_none` でないとき `self._interactive_state()` = "normal" を設定しており、`_sync_all_settings_btn_states` が設定した disabled を上書きする可能性があった。修正後は `CTkOptionMenu` と `CTkButton` を分離し、`CTkButton` は is_none 限定に限定した。
- コメントも「`_sync_all_settings_btn_states` が管理している、将来の設定項目ゼロ backend でも上書きしない」旨が記載されており、意図が明確。

テスト `test_output_settings_btn_not_overwritten_by_apply_tts_none_visual` を確認:
- `monkeypatch` で `has_settings` を OUTPUT 限定で `False` を返すよう差し替え、将来の「設定項目ゼロの Output backend」をシミュレート。
- TTS を `none` → `sapi` に変更後、Output 設定ボタンが `disabled` のままであることを検証。
- モックの使い方は I/F 契約を破っておらず(元の `has_settings` に委譲し OUTPUT のみ偽装)、テストの健全性は問題ない。

### 観点 2: [軽] `_sync_all_settings_btn_states` 単体テスト欠落 → **解決(許容)**

worker は「間接カバレッジで十分」と判断。この判断は妥当。`_apply_absorbed_visuals` 経由の既存テスト、今回追加された `test_output_settings_btn_not_overwritten_by_apply_tts_none_visual`(TTS 変更 → `_sync_all_settings_btn_states` 経由で Output btn 状態を検証)、および既存の running lock テスト(`test_stop_reenables_and_reapplies_overrides`)によって実質的にカバーされている。単体テスト追加はテスト重複を増やすだけで益が薄い。

### 全テスト実行結果

1399 passed, 6 skipped(環境依存の既知 skip)。新規追加テスト 5 件(うち review-fix で 1 件追加)を含め全 pass。
