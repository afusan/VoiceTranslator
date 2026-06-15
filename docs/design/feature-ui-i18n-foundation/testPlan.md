# feature/ui-i18n-foundation テスト項目

すべて small(モック不要・I/O なし)。既存の固定文字列/golden テストは温存し、
「`tr()` 経由でも同一文言が出る」ことで担保する。

## 1. messages / tr() 単体(新規 `tests/test_messages_i18n.py`)
- `tr(key)` が登録済みキーに対し正しい文言を返す。
- `tr(key, **kwargs)` がテンプレートに引数を差し込む(`str.format`)。
- **未知キー**を渡すと例外(or 明示的なエラー表現)になる — 黙って空文字を返さない。
- **引数不足**(テンプレートに必要な kwarg 欠落)で例外になる。
- `current_locale()` が `"ja"` を返す(土台段階の固定値)。

## 2. キー網羅(辞書の健全性)
- ja 辞書に重複キーが無い。
- (可能なら)コード中で `tr("...")` に渡しているリテラルキーが辞書に存在する
  ことを検査する仕組み — 最低限、置換した logic 関数が使う全キーが辞書にあることを
  テストで確認する。

## 3. logic 層の置換後リグレッション(既存テストの温存 + 更新)
置換対象 logic の既存テストが、`tr()` 経由でも従来と**同一文言**を返すことを確認する。
- `ready_state`: トグル/ステータス/ロード/テストボタンの各文言。
- `restart_messages`: restart 開始/失敗バナー。
- `language_choices`: src/tgt fallback・TTS 非対応警告。
- `auth_display`: 認証ステータス文言。
- `accel_summary`: 「演算: GPU/CPU…」表示。
- `status_summary`: レイヤ状態行・セクション見出し(**golden 更新**:出力が変わらないことを確認)。
- `backend_display`: TTS/CAPTURE 表示・skipped 表示。

## 4. 回帰確認(コマンド)
```bash
py -m uv run pytest          # small 全件 green
```
- 既存の固定文字列テストが落ちないこと(文言が一字一句変わっていないこと)が合格条件。
