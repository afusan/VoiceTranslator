# feature/capture-backend-split — テスト項目

## small (自動 / `tests/test_capture_backend_split.py`)

### `TestRefreshCaptureSourcesDropdown`

| メソッド | 観点 |
|---|---|
| `test_lists_current_backend_sources` | 現 backend の `list_sources()` 結果が dropdown に並ぶ |
| `test_keeps_existing_source_id` | 既存 `devices.input` 値が新一覧に含まれていれば選択維持(set_setting しない) |
| `test_falls_back_when_existing_id_missing` | 旧 source_id が新一覧に無ければ先頭にフォールバック + `devices.input` 更新 |
| `test_handles_empty_sources` | ソース 0 件で「(入力デバイスなし)」表示、`_capture_id_map` 空 |
| `test_handles_exception` | `list_capture_sources` が例外で「(取得失敗: ...)」表示、UI が壊れない |

### `TestRefreshIsIndependent`

| メソッド | 観点 |
|---|---|
| `test_capture_refresh_does_not_touch_output` | capture refresh で `list_output_devices` 未呼出 |
| `test_output_refresh_does_not_touch_capture` | output refresh で `list_capture_sources` 未呼出 |

### `TestOnBackendChangeCaptureRefresh`

| メソッド | 観点 |
|---|---|
| `test_capture_backend_change_refreshes_sources` | `_on_backend_change(CAPTURE, "proctap")` で新 backend のソースに切替 + 旧 source_id は新先頭にフォールバック |
| `test_non_capture_backend_change_does_not_refresh_capture` | ASR 切替では capture refresh 走らない |

## 回帰

- `py -m uv run pytest -q --ignore=tests/test_google_cloud_tts_large.py` 全体 952 件 pass(本ブランチで確認済み)。
- 既存 `test_settings_panel_lang.py` / `test_settings_panel_sections.py` /
  `test_settings_panel_tts_none.py` / `test_dynamic_devices.py` も変更なしで通る。

## 手動(現状 soundcard のみ)

| 観点 | 手順 |
|---|---|
| 単 backend 環境 | soundcard のみで起動 → 通常通り Mic / [LB] 群が並び動作する(回帰なし) |
| 取得失敗時 | デバイス列挙が失敗するシナリオ(レアケース) → 「(取得失敗: ...)」が dropdown に出る、UI クラッシュなし |

## 手動(将来 ProcTap 実装後)

| 観点 | 手順 |
|---|---|
| backend プルダウン | 「音声取得」プルダウンに `soundcard` と `proctap` が並ぶ |
| 切替時の連動 | `proctap` を選ぶと「入力デバイス」プルダウンの中身がプロセス一覧に切り替わる |
| 既存値保持 / fallback | 元の source_id が新一覧に無いと先頭にフォールバック(警告は出さない、ConfigStore 更新) |
