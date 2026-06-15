# feature/ui-i18n-foundation テスト項目

すべて small(モック不要・I/O なし)。既存の固定文字列/golden テストは温存し、
「`tr()` 経由でも同一文言が出る」ことで担保する。実装は `tests/test_i18n.py`。

## 1. tr() / current_locale() 単体(`tests/test_i18n.py`)
- `tr(key)` が登録済みキーに対し正しい文言を返す。
- `tr(key, **kwargs)` がテンプレートに引数を差し込む(`str.format`)。
- **未知キー**を渡すと例外になる — 黙って空文字を返さない。
- **引数不足**(テンプレートに必要な kwarg 欠落)で例外になる。
- `current_locale()` が `"ja"` を返す(既定値)。
- `set_locale()` の往復(`set_locale("ja")` 反映)/ 未対応ロケールで `KeyError`。
- `available_locales()` が ja を含む / `locale_display_name("ja")=="日本語"`・未知はコード素通し。

## 2. キー健全性検査(AST ベース、計 6 種)
ソースを AST 解析して `tr("...")` を全抽出し、ja 辞書と突合する。
- (a) **欠落キー** = 0(コードで使うが辞書に無い)。
- (b) **死にキー** = 0(辞書にあるが未使用)。
- (c) **動的キー** = 0(`tr(f"...")` や変数キー。混入時は検査が成立しないため弾く)。
- (d) **トップレベル `tr()` 評価の禁止**(モジュール直下・代入右辺・クラス body での `tr()` を弾く。
  言語切替に追従させるため定数に焼かない)。
- (e) **CJK 直書き残存検出**(置換漏れ検出。対象 = gui/logic + `layer_settings_schema.py`。
  docstring/式文は除外、内部 sentinel / programmer 向け例外メッセージのみ許可リスト)。
- (f) **テンプレ引数の充足**(各 `tr("key", ...)` の kwargs が当該テンプレートの placeholder を満たす)。
- schema 駆動対応: `label_key=` / `help_key=` のリテラルをキー登録源として扱い、
  `tr(field.label_key)` 形の動的解決のみ許可(他の動的キーは引き続き禁止)。
- ja 辞書に重複キーが無い(dict リテラルの後勝ちを AST で読み直して検出)。

## 3. logic 層の置換後リグレッション(既存テストの温存 + 更新)
置換対象 logic の既存テストが、`tr()` 経由でも従来と**同一文言**を返すことを確認する。
- `ready_state`: トグル/ステータス/ロード/テストボタンの各文言。
- `restart_messages`: restart 開始/失敗バナー(`restart.*` キー経由)。
- `language_choices`: src/tgt fallback・TTS 非対応警告。
- `accel_summary`: 「演算: GPU/CPU…」表示。
- `status_summary`: レイヤ状態行・セクション見出し(**golden**:出力が変わらないことを確認)。
- `backend_display`: TTS(`tts_none_display()`)/ CAPTURE 表示・skipped 表示(`skipped_status_text()`)。
- `auth_display`: 認証ステータスは **i18n カタログ対象外**(翻訳しない enum ミラー)。
  `AUTH_MISSING_TEXT == ModelStatus.MISSING_CREDENTIALS.value` の一致を維持していること。

## 4. 回帰確認(コマンド)
```bash
py -m uv run pytest          # small 全件 green
```
- 既存の固定文字列テストが落ちないこと(文言が一字一句変わっていないこと)が合格条件。

## 5. 後続フェーズへの申し送り(検査の死角)
- **CJK 残存検査の対象は `gui/logic/` + `layer_settings_schema.py`**(Phase 2 で schema を追加)。
  widget(`gui/` 直下: control_panel / settings_panel / 各 dialog)はまだ対象外。Phase 3(widget の
  置換)で検査範囲を `gui/` 直下へ拡大し、未置換ファイルは許可リストで段階的に 0 へ近づける(提案 B)。
- **カタログ間整合検査(全ロケールのキー集合一致)は未実装**。ja 単一の現状では検出力ゼロのため、
  Phase 4(en/zh/es 辞書追加)で同時に導入する。
