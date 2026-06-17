# finalReview

## レビュー 1 巡目

**判定: GO**

### 観点 0: 上位整合性

指摘なし。

request.md(「変更対象がなければ選択肢を無効に」)→ checkedRequest.md(判定は backend カタログ/スキーマから空集合かどうかで決める、ハードコードリスト不可)→ Plan.md(`visible_fields()` に委譲する `has_settings` 純関数を `gui/logic/` に置く)→ 実装。一貫しており、要求の取りこぼし・スコープ外の混入・解釈の歪みは無い。非目標(スキーマ変更、ツールチップ、設定ボタン以外)にも踏み込んでいない。上流ドキュメント間の矛盾も無い。

### 観点 1: 設計整合(コード現実)

- **[軽] `gui/logic/__init__.py` の依存契約と `settings_button.py` の実際の依存**:
  `gui/logic/__init__.py` は「依存は common 配下の純粋モジュールと標準ライブラリのみ」と宣言しているが、`settings_button.py` は `voice_translator.gui.layer_settings_schema` に依存する(deferred import)。`layer_settings_schema` は宣言的データ中心のモジュールで customtkinter を直接 import しないため実害は無いが、パッケージの stated contract との乖離がある。対応案: `__init__.py` の docstring に「`layer_settings_schema` 等の GUI 内の純宣言モジュールへの依存は許容」を追記する。
  - worker回答:

上記以外は Plan.md どおりの実装。`has_settings` は `visible_fields()` への薄いラッパとして純関数に切り出されており、単一責任・既存資産の再利用とも整合。`_settings_btns` 辞書の追加と `_backend_rows` との関係(同一 widget への二重参照)も明示的にコメントされている。

### 観点 2: テストの十分性と健全性

- **[中] `_apply_tts_none_visual` が Output 行の設定ボタンを `_interactive_state()` で上書きする潜在的問題のテスト欠落**:
  `_apply_tts_none_visual` 内の Output 行処理(settings_panel.py 357-362行)で、TTS が「(なし)」でないとき Output 行の `CTkButton` を `self._interactive_state()` = "normal" に設定する。これは `_apply_absorbed_visuals` 内で `_sync_all_settings_btn_states()` → `_apply_tts_none_visual()` の順に呼ばれるため、`_sync_all_settings_btn_states` が OUTPUT 設定ボタンに設定した状態を `_apply_tts_none_visual` が上書きする。**現時点では OUTPUT soundcard は `has_settings == True` なので顕在化しないが、将来 Output に設定項目ゼロの backend が追加された場合にボタンが誤って normal になる**。対応案: 以下のいずれか(worker 判断):
  (a) `_apply_tts_none_visual` の Output 行の `CTkButton` 処理を TTS 行と同様に「`is_none` のときだけ disabled にする」形に変更する(TTS 行で既にやった修正と同じパターン)
  (b) 現状は顕在化しないため `pendList.md` に記録して次回の Output backend 追加時に対処する
  - worker回答:

- **[軽] `_sync_all_settings_btn_states` 単体のテスト欠落**: `_sync_all_settings_btn_states` は running ロック解除と `_apply_absorbed_visuals` から呼ばれるが、直接的なテストが無い。既存の running lock テスト(`test_stop_reenables_and_reapplies_overrides`)は通っているが、running ロック解除後に「空設定 backend の設定ボタンが disabled のまま」であることを明示的に検証するテストは無い。ただし `_apply_absorbed_visuals` 経由で間接的にカバーされており、テスト爆発を避ける判断として許容範囲。
  - worker回答:

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
