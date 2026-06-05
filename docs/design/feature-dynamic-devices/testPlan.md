# feature/dynamic-devices — テスト項目

## small (自動 / `tests/test_dynamic_devices.py`)

### `TestRestartPipelineAsync`

| メソッド | 観点 |
|---|---|
| `test_not_running_calls_on_restarted_immediately` | 動作中でない → 即 on_restarted、stop/start 未呼出 |
| `test_running_stops_then_starts` | 動作中 → stop → start の順、on_restarted 発火 |
| `test_stop_failure_invokes_on_failed_and_skips_start` | stop 失敗 → on_failed、start 未試行 |
| `test_start_failure_invokes_on_failed` | start 失敗(DeviceValidator 等) → on_failed |
| `test_concurrent_restart_is_rejected` | 走行中の 2 回目呼出 → on_failed("既に再開中です") |
| `test_default_callbacks_swallow` | callback 省略でも例外にならない |

### `TestSettingsPanelDeviceRestart`

| メソッド | 観点 |
|---|---|
| `test_capture_change_triggers_restart_when_running` | 動作中の capture 切替で restart_pipeline_async が呼ばれる |
| `test_capture_change_does_not_restart_when_stopped` | 動作中でなければ呼ばれない |
| `test_output_change_triggers_restart_when_running` | 動作中の output 切替で restart_pipeline_async が呼ばれる |
| `test_trigger_shows_info_banner` | バナーに「(入力/出力)デバイスを切り替えました(再開中…)」を `duration_ms=0` で表示 |
| `test_apply_restart_completed_dismisses_banner` | 完了で banner.dismiss |
| `test_apply_restart_failed_shows_error` | 失敗で banner.show_error(メッセージに種別と原因) |
| `test_banner_none_safe` | banner=None でも例外にならない |

## 回帰

- `py -m uv run pytest -q --ignore=tests/test_google_cloud_tts_large.py` で small 全体 942 件 pass(本ブランチで確認済み)。
- 既存 `test_app_controller.py` / `test_pipeline.py` / `test_pipeline_e2e.py` は変更なしで通る。

## middle / 手動

middle 階層(WAV を流して動作中に capture を swap)は実装の責務が AppController と SettingsPanel に
跨るため、本ブランチでは small で十分担保したと判断し追加しない。実機での挙動は手動で確認:

| 観点 | 手順 |
|---|---|
| 動作中の入力切替 | 配信視聴中に SettingsPanel で入力デバイスを変更 → 1〜2 秒の中断後に新デバイスで翻訳再開 |
| 動作中の出力切替 | 同上、出力デバイス側 |
| バナー表示 | 切替直後に青色「再開中…」が画面上部に出る / 完了で自動的に消える |
| エラー時 | 入力=出力 になる組合せに変更 → 赤色エラーバナーが出る / パイプラインは停止 |
| 連続変更 | 短時間に複数回変えると 2 回目以降は「既に再開中」のエラーバナー(or 何も起きない) |
