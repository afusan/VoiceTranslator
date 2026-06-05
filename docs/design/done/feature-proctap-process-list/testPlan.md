# feature/proctap-process-list — テスト項目

## small(自動)

### `tests/test_process_enumerator.py`(新規)

| テスト | 観点 |
|---|---|
| `test_returns_capture_source_list` | 戻り値が `list[CaptureSource]`、`kind == PROCESS` |
| `test_filters_inactive_sessions` | `AudioSessionState.Inactive` のセッションは除外 |
| `test_dedupe_same_pid` | 同 PID 内に複数 session があっても 1 件に集約 |
| `test_display_name_format` | `"chrome.exe (1234)"` 形式 |
| `test_psutil_name_failure_falls_back_to_unknown` | `psutil.Process.name()` が PermissionError → `"unknown"` |
| `test_returns_empty_when_no_sessions` | pycaw が空リストを返すケース |
| `test_pycaw_isolated_for_mock` | pycaw 呼び出しが 1 関数に隔離されており monkeypatch で完全置換できる |

### `tests/test_process_select_dialog.py`(新規)

| テスト | 観点 |
|---|---|
| `test_initial_enumerate_on_open` | ダイアログ生成時に 1 回 enumerate される |
| `test_refresh_button_calls_enumerate` | ↻ 更新ボタンで再 enumerate |
| `test_audition_toggle_starts_polling` | ▶ 試聴開始で peak poll ループ起動 |
| `test_audition_toggle_stops_polling` | ■ 停止で poll ループ停止 |
| `test_changing_selection_stops_audition` | 試聴中に別行を選ぶと poll 停止 |
| `test_ok_returns_selected_pid` | OK で選択中 PID が返る |
| `test_cancel_returns_none` | Cancel で None |
| `test_close_stops_audition_poll` | ダイアログ閉鎖時に poll が確実に停止 |

### `tests/test_settings_panel.py`(追記)

| テスト | 観点 |
|---|---|
| `test_capture_kind_device_shows_source_dropdown` | DEVICE kind は従来の source プルダウン |
| `test_capture_kind_process_shows_select_button` | PROCESS kind は「プロセス選択…」ボタン |
| `test_select_button_label_reflects_current_pid` | 選択済みの場合ボタンラベルに現在値表示 |
| `test_kind_change_replaces_widget` | backend 切替で kind が変わったら UI ウィジェットが切り替わる |

### `tests/test_control_panel.py`(追記)

| テスト | 観点 |
|---|---|
| `test_start_disabled_when_process_source_empty` | PROCESS kind かつ source 未選択 → Start disable |
| `test_status_label_shows_select_prompt` | 同条件で「プロセスを選択してください」ラベル |
| `test_start_enabled_when_process_source_selected` | PID 選択済みなら Start 復活 |
| `test_device_kind_unaffected` | DEVICE kind は従来挙動のまま |

### `tests/test_config_store.py` or 起動 load 経路(追記)

| テスト | 観点 |
|---|---|
| `test_process_source_not_persisted` | PROCESS kind の source は ConfigStore.save 時に除外 |
| `test_process_source_loaded_as_empty` | 起動時に古い PID が config にあっても空扱いで読まれる |

### `tests/test_proctap_backend.py`(既存修正)

| テスト | 観点 |
|---|---|
| `test_list_sources_calls_enumerator` | `list_sources()` が `process_enumerator.enumerate_active_processes()` を呼ぶ(monkeypatch で確認) |
| (既存 `test_list_sources_is_empty_until_stage3`) | 削除(段階 3 で本実装に切り替わるため) |

---

## large(手動実行)

### `tests/test_proctap_backend.py::TestProcTapLargeSelfCapture`(既存拡張)

| テスト | 観点 |
|---|---|
| `test_list_sources_returns_real_sessions` | 実 pycaw で `list_sources()` が 0 件以上返ること。型・kind 検証 |

実行: `py -m uv run pytest tests/test_proctap_backend.py -m large -q`

特定セッションの存在は要求しない(実行マシンの状態に依存するため)。

---

## 回帰

- `tests/test_backend_setup.py`: 既存の期待値 `["soundcard", "proctap"]` を維持
- 全 small `py -m uv run pytest -q --ignore=tests/test_google_cloud_tts_large.py` で回帰なし

---

## 手動(実機)確認

| 観点 | 手順 |
|---|---|
| extras 追加 | `py -m uv sync --extra cpu --extra capture-proctap` でエラーなく pycaw / psutil が入る |
| pycaw 動作 | `py -m uv run python -c "from pycaw.pycaw import AudioUtilities; print(len(AudioUtilities.GetAllSessions()))"` で件数が表示される |
| プロセス選択 UI 表示 | アプリ起動 → 設定 → Capture backend を proctap に → 「プロセス選択…」ボタンが出る |
| 列挙 | ボタン押下 → ダイアログ表示 → Spotify / YouTube タブ等 Active なプロセスが列挙される |
| 試聴 | 行を選択 → ▶ 試聴開始 → 当該アプリで音を鳴らす → レベルメータが動く |
| 停止 | ■ 停止押下 → メータ 0 に落ちる |
| 別行選択 | 試聴中に別行を選ぶ → 自動で停止 → 改めて開始ボタン |
| 確定 | OK → SettingsPanel ボタンラベルが `"chrome.exe (1234) ▼"` 等に変わる |
| Cancel | Cancel → SettingsPanel 側に変更が反映されない |
| 未選択時 Start | PROCESS kind で未選択のまま開始ボタンを見る → disable + 「プロセスを選択してください」 |
| 再起動の空扱い | config.yaml に古い PID が残っていてもアプリ起動時は未選択になる |
| 本番動作 | OK 後にロード → 開始 → 当該プロセスの音が翻訳パイプラインを流れる |

---

## 段階 4 以降で確認予定(本ブランチでは未着手)

| 観点 | 備考 |
|---|---|
| プロセス起動/終了の追従(動的更新) | pendList 起票済み |
| Linux/Mac の process-kind 列挙 | pendList 起票済み |
