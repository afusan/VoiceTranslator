# feature/ui-i18n-foundation 作業計画

起票: 2026-06-16 / 方式決定: 案1(自前 dict + `tr()`)

## 目的
UI 表示文言の **国際化(i18n)の土台**を作る。実態調査(`tmp/i18n_audit.md`)で、
文言は約 185+ 件あり全て日本語ベタ書き・ロケール切替機構が無いことが分かった。
本ブランチでは「キー化 → 辞書引き」の仕組みを最小コストで導入し、将来の多言語化の
差し込み口を用意する。**語彙は ja のみ**で、実際の翻訳追加・実行中切替は後続に回す。

## 方式の選定理由(案1: 自前 dict + tr())
実装方式 4 案(自前 dict / gettext / JSON / observable binding)を比較した
(`tmp/i18n_options.md`)。本プロジェクトは「文言を固定文字列・golden テストで守る」方針で、
文言がコード内に見えることが前提。案1 は追加依存ゼロ・既存テスト方針と完全整合・最小実装で、
土台 ja のみのスコープに最適。将来 `tr()` という単一窓口を gettext の `_()` に寄せる移行も
1 点で済むため、多言語本格化時の選択肢も残る。

## スコープ(このブランチでやること)
1. **メッセージ辞書とアクセス API**(`gui/logic/messages.py` 新規)
   - ja 文言を Python dict で集約。
   - `tr(key, **kwargs) -> str`: キーで文言を引き、`str.format` で引数を差す
     (動的文言は `tr("control_panel.latency", value=..., n=...)` の形)。
   - `current_locale() -> str`: ロケール解決の**単一窓口**。今は `"ja"` 固定で返すだけ。
     将来の切替機構はここに差し込む(本ブランチでは切替 UI・再描画イベントは作らない)。
   - 未知キー / 欠落引数は早期に気づける形にする(黙って空文字にしない)。
2. **キー命名規約**: `<area>.<element>` を基本とする。
   - 例: `control_panel.start_button` / `dialog.consent.title` /
     `layer_settings.capture.captured_queue_max_bytes.label`。
   - schema 由来のキーは `keys` tuple から機械的に導出できる規約にする。
3. **logic 層の文言を `tr()` 経由に置換**(本ブランチの適用範囲はここまで)
   - 対象: `ready_state` / `restart_messages` / `language_choices` / `auth_display` /
     `accel_summary` / `status_summary` / `backend_display`。
   - これらは既に「純関数が文言を返す」形なので、返す文字列を `tr()` 経由にするだけ。
     呼び出し側(widget)の改修は不要。

## やらないこと(後続ブランチへ)
- **en 等の他言語辞書の追加**(土台のみ。語彙は ja)。
- **実行中のロケール切替 UI と再描画イベント**(`current_locale()` の差し込み口だけ用意)。
- **schema(`layer_settings_schema.py` 60+ 件)の置換** → 後続 Phase 2。
- **各 widget 直書き(control_panel / settings_panel / 各ダイアログ ~95 件)の置換**
  → 後続 Phase 3。
- gettext / JSON / observable binding への移行(案2〜4)。

## 段階導入の全体像(参考・本ブランチは Phase 1 のみ)
- **Phase 1(本ブランチ)**: 土台 + logic 層置換。
- Phase 2: `layer_settings_schema` のラベル/help をキー化。
- Phase 3: 各 widget の直書き文言をキー化(f-string はテンプレート+引数へ)。
- Phase 4: en 辞書追加 + ロケール切替 UI + 再描画イベント(`add_<event>_listener` に乗せる)。

## 移行性メモ
- `tr()` を単一窓口に保つことで、将来 gettext(案2)へ移るときの変更点が 1 か所に収まる。
- 文言ソースは **messages.py の 1 か所**に寄せる(logic 関数が直書きしない)。二重管理を防ぐ。

## 設計上の留意点(調査 A 由来)
- 動的組み立て(f-string)文言は `tr(key, **kwargs)` のテンプレート方式に統一する。
- `status_summary` のセクション見出しは golden テストで固定 → キー化に合わせてテストも更新。
- 既存の英語混在文言("Cancel" / "OK" / "Missing Credentials" / "Not Verified")も
  ja 辞書のキーとして登録する(値は現状の英語のまま。多言語化時に整理)。
