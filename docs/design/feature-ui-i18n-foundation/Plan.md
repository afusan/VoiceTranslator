# feature/ui-i18n-foundation 作業計画

起票: 2026-06-16 / 方式決定: 案1(自前 dict + `tr()`)

## 目的
UI 表示文言の **国際化(i18n)の土台**を作る。実態調査(`tmp/i18n_audit.md`)で、
文言は約 185+ 件あり全て日本語ベタ書き・ロケール切替機構が無いことが分かった。
本ブランチでは「キー化 → 辞書引き」の仕組みを最小コストで導入し、将来の多言語化の
差し込み口を用意する。**語彙は ja のみ**で、実際の翻訳追加・実行中切替は後続に回す。

**最終的な対応目標言語**: 日本語(ja)/ 英語(en)/ 中国語(zh)/ スペイン語(es)。
本ブランチは土台のみで ja だけを実装するが、キー設計・`current_locale()` の窓口は
この 4 言語を見据えた拡張可能な形にする(辞書ファイルを足すだけで言語が増える構造)。
留意: zh は CJK でフォント差はあるが RTL は無く、es は語尾/複数形の差し分けがあるため、
キーは前述のとおり文脈単位で持つ(文字列単位にまとめない)。

## 言語切替の要件方針(2026-06-16 決定)
当初要件に無かった「起動後の言語切替」を検討し、次に決定した:
- **当面は起動時決定**。`current_locale()` は起動時に設定値(将来は OS ロケール併用)から
  1 回解決し、**実行中は不変**とする(その場切替 UI・再描画イベントは作らない)。
- ただし**土台は「将来の即時切替に耐える形」を保つ**: 文言は必ず表示する瞬間(関数内)で
  `tr()` を引き、モジュールレベルで定数に焼かない。これにより即時切替が要件化したら
  「`current_locale()` を可変化 + 再描画イベント」を足すだけで拡張できる。
- この方針が [レビュー指摘](レビュー指摘.md) 重大1(モジュールレベル `tr()` 評価の解消)を
  「修正する」根拠になる(理由 = 将来耐性 + logic 層内の一貫性。即時切替のためではない)。
- 即時切替の本格実装(再描画イベント + 多言語辞書)は **Phase 4** に置く。要件化されなければ
  起動時決定のまま据え置いてよい。

## 方式の選定理由(案1: 自前 dict + tr())
実装方式 4 案(自前 dict / gettext / JSON / observable binding)を比較した
(`tmp/i18n_options.md`)。本プロジェクトは「文言を固定文字列・golden テストで守る」方針で、
文言がコード内に見えることが前提。案1 は追加依存ゼロ・既存テスト方針と完全整合・最小実装で、
土台 ja のみのスコープに最適。将来 `tr()` という単一窓口を gettext の `_()` に寄せる移行も
1 点で済むため、多言語本格化時の選択肢も残る。

## スコープ(このブランチでやること)
1. **メッセージ辞書とアクセス API**(`gui/i18n.py` 新規)
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
   - **キーの粒度は「文脈単位」**: 同一の日本語文言でも、出る場所/意味が異なれば
     **別キー**にする(例: 「クリア」= `control_panel.clear_events` と
     `control_panel.clear_translations`)。文字列単位でまとめると、別言語で訳し分けが
     必要になったとき破綻するため。**例外**として、真に同一概念の再利用(共通の
     Cancel / OK 等)のみ `common.cancel` / `common.ok` の共通キーに集約してよい。
   - **キーはリテラルで渡す**(`tr("control_panel.start_button")`)。`tr(f"...{x}")` の
     ような動的キー生成は禁止。理由は下記「キー健全性検査」を静的解析で成立させるため。
     どうしても動的に選ぶ場合は候補キーを定数で明示列挙する。
3. **logic 層の文言を `tr()` 経由に置換**(本ブランチの適用範囲はここまで)
   - 対象: `ready_state` / `restart_messages` / `language_choices` /
     `accel_summary` / `status_summary` / `backend_display`。
   - これらは既に「純関数が文言を返す」形なので、返す文字列を `tr()` 経由にするだけ。
     呼び出し側(widget)の改修は不要。**表示する瞬間(関数内)で `tr()` を呼ぶ**
     (モジュールレベルで定数に焼かない。言語切替方針 参照)。
   - 例外 `auth_display`: 認証ステータス("Missing Credentials" / "Not Verified")は
     ModelStatus 表示(英語 enum value)と揃えるミラーで**翻訳対象ではない**ため、カタログに
     入れず源(`ModelStatus.*.value` / 表記を揃える独立文言)を直接参照する(中4 の線引き)。
4. **キー健全性検査**(`tests/test_i18n.py` の small テストとして実装)
   - ソースを AST 解析して `tr("...")` を全抽出し、ja 辞書と突合する。検出する不整合:
     (a) 欠落キー(使うが辞書に無い)/ (b) 死にキー(辞書にあるが未使用)/
     (c) 動的キー(リテラルでない第一引数)。
   - さらに: (d) **トップレベル `tr()` 評価の禁止**(言語切替に追従させるため定数化させない)/
     (e) **gui/logic の CJK 直書き残存検出**(置換漏れ検出。許可リストは内部 sentinel のみ)/
     (f) **テンプレ引数の充足**(各 `tr` 呼び出しの kwargs が placeholder を満たす)。
   - 文言が増えるほど目視突合は破綻するため、自動検査を最初から土台に含める。

## やらないこと(後続ブランチへ)
- **en / zh / es 辞書の追加**(土台のみ。語彙は ja。辞書を足すだけで増やせる構造にはする)。
- **実行中のロケール切替 UI と再描画イベント**(`current_locale()` の差し込み口だけ用意)。
- **schema(`layer_settings_schema.py` 60+ 件)の置換** → 後続 Phase 2。
- **各 widget 直書き(control_panel / settings_panel / 各ダイアログ ~95 件)の置換**
  → 後続 Phase 3。
- gettext / JSON / observable binding への移行(案2〜4)。

## 段階導入の全体像(全フェーズ同一ブランチで積み上げ、マージは全完了後)
- **Phase 1(完了)**: 土台 + logic 層置換。
- **Phase 2(完了)**: `layer_settings_schema` のラベル/help をキー化。`SettingField` は
  `label_key` / `help_key` を持ち、`LayerSettingsDialog` が表示時に `tr()` で解決
  (トップレベル `tr()` 禁止と両立)。健全性検査を拡張(`label_key=`/`help_key=` リテラルを
  キー登録源として扱い、`tr(field.label_key)` の動的解決のみ許可。CJK 残存検査を
  `layer_settings_schema.py` にも拡大)。
- **Phase 3(完了)**: 全 widget(main_window 含む 7 ファイル)の直書き文言をキー化。
  f-string はテンプレート+引数へ。`common.*`(Cancel/OK)・`layer.*`(レイヤ表示名、
  LayerSettingsDialog/SettingsPanel 共有)・`dialog.*` / `control_panel.*` / `settings_panel.*`
  を追加。CJK 残存検査を **gui/ 全体**へ拡大(ログ/例外メッセージは AST で除外、許可リストは
  内部 sentinel + programmer 向け例外のみ)。モジュールレベル定数(_LAYER_DISPLAY 等)は
  if/elif の literal tr() ヘルパに、デフォルト引数の文言は None + 本体解決に変更
  (トップレベル tr() 禁止と両立)。
- Phase 4: en / zh / es 辞書追加 + ロケール切替 UI + 再描画イベント
  (`add_<event>_listener` に乗せる)。カタログ間整合検査を追加。

## 移行性メモ
- `tr()` を単一窓口に保つことで、将来 gettext(案2)へ移るときの変更点が 1 か所に収まる。
- 文言ソースは **`gui/i18n.py` の 1 か所**に寄せる(logic 関数が直書きしない)。二重管理を防ぐ。
  ※ 配置/名前は中1 を受け、`common/messages.py`(PipelineMessage)との同名衝突を避けるため
  `gui/i18n.py` とした(文言カタログは「データ」で logic の純関数とは責務が異なるため gui 直下)。

## 設計上の留意点(調査 A 由来)
- 動的組み立て(f-string)文言は `tr(key, **kwargs)` のテンプレート方式に統一する。
- `status_summary` のセクション見出しは golden テストで固定 → キー化に合わせてテストも更新。
- **カタログに入れるのは「翻訳対象の文言」のみ**(中4 の線引き)。enum value のミラー
  ("Missing Credentials" = `ModelStatus.MISSING_CREDENTIALS.value`、"Not Verified" は
  それと表記を揃える独立文言)はカタログに入れず源を直接参照する(`auth_display`)。
  これにより「ja だけ訳して enum value とズレる」二重管理を避ける。一方 widget 共通の
  "Cancel" / "OK" は翻訳対象なので Phase 3 で `common.*` として登録する。

## マージ前チェック(作業完了後・マージ前に必ず実施)
本ブランチをマージする前に、恒常ドキュメントへの反映漏れが無いか確認する:
- **`docs/design/Class.md`**: 新規 `i18n` モジュール(`tr` / `current_locale`)の
  役割をクラス/モジュール一覧に追記したか。
- **`docs/design/Architecture.html` §9**(GUI 内部構成と UI 実装規約): 文言は `gui/i18n.py` に
  集約し `tr()` 経由で引く、という規約を追記したか(「判断は logic、widget は塗るだけ」の
  延長として「文言は i18n、logic/widget は tr() で引く」)。
- **`CLAUDE.md` の UI 実装規約**: 文言の置き場が messages.py + tr() に変わったことを
  反映すべきか判断(土台のみなので最小限。後続フェーズで widget 置換が済んでから本格反映でも可)。
- **`docs/manual.md`**: 本ブランチでは言語切替 UI を作らないため更新不要(Phase 4 で追記)。
- 反映が済んだら、この節のチェック結果を一言コミットに残す。
