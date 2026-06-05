# feature/ui-sections-split — テスト項目

## small (自動)

| ファイル | 観点 |
|---|---|
| `tests/test_settings_panel_sections.py::TestSectionConstruction` | 3 セクションが構築される / ヘッダのタイトルが正しい |
| `tests/test_settings_panel_sections.py::TestInitialOpenState` | 既定全開 / ConfigStore の閉じ状態が反映される |
| `tests/test_settings_panel_sections.py::TestPersistOnToggle` | close で True / open で False が保存される / 書き込み失敗が UI を壊さない |
| `tests/test_settings_panel_sections.py::TestNoLegacyKey` | 旧 `ui.collapsed.settings_panel` キーは新方式で参照されない |
| `tests/test_settings_panel_lang.py` (既存) | 既存の入力/出力言語連動が変わらず通る |
| `tests/test_collapsible_section.py` (既存) | CollapsibleSection 単体の挙動は不変 |

## 手動

| 観点 | 手順 |
|---|---|
| 3 セクションが見える | アプリ起動 → SettingsPanel 内に「バックエンド」「デバイス」「翻訳」の 3 ヘッダ |
| 個別開閉 | 1 セクションだけ閉じる / 開く ができる(他に影響しない) |
| 開閉永続化 | アプリ再起動後に開閉状態が復元される |
| 共通行 | 「ログ出力先」「設定を保存 / 再読込 / デバイス再列挙」は常に見える(セクション外) |

## 回帰確認

- `py -m uv run pytest -q --ignore=tests/test_google_cloud_tts_large.py` で small が緑(別件の collection error あり)。
- 既存 small 全体 879 件パス(本作業ブランチで確認済み)。
