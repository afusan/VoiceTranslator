# feature/ui-sections-split — 計画

メタ計画 [feature-runtime-flex-and-input](../feature-runtime-flex-and-input/Plan.md) の **Phase 1**。
SettingsPanel 全体を 1 つの CollapsibleSection で包んでいた構造を解体し、
内部を「バックエンド / デバイス / 翻訳」の **3 つの独立セクション** に分割する。

---

## 1. 目的

ドッグフーディングで「設定全体を畳むと細かい調整がしにくい」「特定セクションだけ畳みたい」という
ニーズが浮上した。例: バックエンドは固定で使い、デバイス調整だけしたい場合に
バックエンド行を畳めると視界が広くなる。

---

## 2. スコープ

### in
- `SettingsPanel` 内部を 3 セクションに分割(`CollapsibleSection` で各々ラップ)。
- 各セクションの開閉状態を ConfigStore キー別に永続化:
  - `ui.collapsed.backends`
  - `ui.collapsed.devices`
  - `ui.collapsed.languages`
  - いずれも値は `True=閉じてる / False=開` の bool。default=False。
- `MainWindow` から SettingsPanel 全体を `CollapsibleSection` で包む処理を撤去。
- 共通行(ログ出力先 / 保存ボタン群)は 3 セクションの外、SettingsPanel 下部に残す。
- 既存テスト(`test_settings_panel_lang.py`)が引き続き通ること。
- 新規テスト(`tests/test_settings_panel_sections.py`)を追加。

### out
- ControlPanel 側のステータステキスト折り畳み(`ui.collapsed.status_text`)は触らない(既存のまま)。
- 旧 `ui.collapsed.settings_panel` キーの **マイグレーション処理は実装しない**(後方互換不要 / 後始末は ConfigStore に値として残るだけで実害なし)。

---

## 3. 変更ファイル

| ファイル | 変更内容 |
|---|---|
| `src/voice_translator/gui/settings_panel.py` | `_build_widgets` を 3 セクション + 共通行構築に分割。`_initial_open_state` / `_persist_collapsed` ヘルパを追加。grid 構造はセクション内で row=0 リセット |
| `src/voice_translator/gui/main_window.py` | SettingsPanel 全体を CollapsibleSection で包む処理を撤去。`_on_settings_toggle` / `_CFG_COLLAPSED_SETTINGS` を削除 |
| `tests/test_settings_panel_sections.py` | 新規。セクション構築 / 初期状態 / toggle 永続化 / 旧キー無視 をカバー |

---

## 4. 設計上のポイント

### 4-1. セクションの境界

- **バックエンド**: 6 レイヤ(Capture/VAD/ASR/Translator/TTS/Output)を一括で持つ。
  各行は `label / dropdown / status / 設定ボタン` の 4 列構成。
- **デバイス**: 入力デバイス / 出力デバイスのみ。将来 P5(入力 backend のデバイス単位分解)
  で「入力 backend プルダウン」が追加されたとき、本セクションを拡張する想定。
- **翻訳**: 入力言語 (src) / 出力言語 (tgt) のみ。

### 4-2. 共通行の扱い

ログ出力先と保存ボタン群は「どのセクションにも属さない設定操作」なので、
セクション**外**に置く。畳んでも見えるのが意図(設定の保存ボタンを「畳んでて押せない」状況を避ける)。

### 4-3. 永続化フォーマット

`is_collapsed: bool` で持つ(True=畳まれてる)。理由: default を False(=開) に保つことで、
旧設定ファイルから移行したユーザも 3 セクション開で起動できる。

`_initial_open_state(key)` で 初期状態を読み出し、`_persist_collapsed(key, is_open)` で
書き込む。読み書きの失敗は黙殺(UI 操作の最中に ConfigStore 例外で詰まらせない)。

### 4-4. 既存テストとの互換

`test_settings_panel_lang.py` は MagicMock で `_src_dropdown` / `_tgt_dropdown` 等を
shim するので、内部レイアウトが grid → section 化されても通る。
内部メソッド(`_refresh_input_language_choices` 等)のシグネチャと振る舞いは変えない。

---

## 5. 確認手順(手動 / 開発者向け)

1. `py -m uv run pytest tests/test_settings_panel_sections.py tests/test_settings_panel_lang.py tests/test_collapsible_section.py` で small が緑。
2. `py -m voice_translator` で起動し、設定パネル内に「バックエンド / デバイス / 翻訳」の 3 セクションが見える。各々の ▼/▶ で個別開閉できる。
3. アプリを再起動して、閉じたセクションが閉じたまま復元されること。
