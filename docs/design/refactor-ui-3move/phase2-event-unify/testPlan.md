# P2: event-unify — テスト計画

作成: 2026-06-10。全テスト small。

## 1. AppController(test_app_controller.py に追加 / 書き換え)

| # | ケース | 期待 |
|---|---|---|
| 1 | `add_text_ready_listener` 登録 → `_handle_text_ready(record)` | record が届く |
| 2 | `add_utterance_done_listener` 登録 → `_handle_utterance_done(record)` | record が届く(jsonl 等の既存後処理も従来どおり) |
| 3 | `add_fatal_listener` / `add_warn_listener` → ErrorHandler 経由の通知 | message + context kwargs が届く(`_start_coord` 構築の ErrorHandler 注入で検証 or `_emit_fatal` 直叩き) |
| 4 | `add_settings_listener` → `set_setting("languages","src","en")` | `("languages","src")` が届く |
| 5 | listener の `Subscription.unsubscribe()` | 以後届かない(既存 status テストの形を踏襲) |
| 6 | listener 内例外 | 他 listener と本体を止めない |
| 7 | `set_setting("devices","input",...)` 動作中(`is_running=True` 相当) | restart イベント started → completed の順で届く + stop→start 実行 |
| 8 | 同上で start 失敗 | started → failed(message 付き)が届く |
| 9 | `set_setting("devices","input",...)` 停止中 | restart イベントは流れない |
| 10 | `set_setting("devices","output",...)` 動作中 | device_key="output" で started が届く |
| 11 | `set_callbacks` が存在しない | `hasattr(ctrl, "set_callbacks") is False`(撤去の明示) |

**削除するテスト**(挙動ごと廃止、シナリオは listener 版 #1〜6 で温存):
旧 single callback 互換を検証していたもの(`set_callbacks(on_status_change=...)` 経由の
イベント受信、`on_utterance_done` 直登録)。

## 2. SettingsPanel(test_dynamic_devices.py 書き換え)

| # | ケース | 期待 |
|---|---|---|
| 1 | `_on_capture_changed` / `_on_output_changed`(動作中) | `set_setting` は呼ぶが **`restart_pipeline_async` を直接呼ばない**(移管の確認) |
| 2 | `_apply_restart_event(started)` | banner.show_info(「入力デバイスを切り替えました(再開中…)」, duration_ms=0) |
| 3 | `_apply_restart_event(completed)` | banner.dismiss |
| 4 | `_apply_restart_event(failed)` | banner.show_error(device 種別 + 理由を含む) |
| 5 | banner=None | いずれも例外なし |
| 6 | `set_control_panel` が存在しない | hasattr で撤去確認 |

`restart_pipeline_async` 単体の挙動テスト(stop→start 順序 / 失敗 / 多重)は**現状維持**。

## 3. ControlPanel(test_control_panel_test_output.py 等 + 新規)

| # | ケース | 期待 |
|---|---|---|
| 1 | 構築時に listener 6 種が登録される | スタブ controller の登録記録で検証 |
| 2 | settings イベント `("devices","input")` 受信 | `_sync_ready_state` 相当の再計算が走る(PROCESS kind: 「プロセス未選択」→「▶ 開始」遷移 = 契約 §11.5) |
| 3 | settings イベント `("ui","collapsed","x")` 受信 | ready 再計算は走らない(devices 以外は無視) |
| 4 | status イベント受信 | SettingsPanel への転送をしない(スタブに forward 先が無くても動く = 既に引数廃止で構造的に保証) |

## 4. 既存テストの修正

| ファイル | 変更 |
|---|---|
| `test_control_panel_test_output.py` / `test_capture_process_source_lifecycle.py` | スタブ controller: `set_callbacks` を削除し、`add_*_listener` 6 種(登録記録 + ダミー Subscription 返し)を追加。`ControlPanel(...)` の `settings_panel` 引数を除去 |
| `test_main_smoke.py` | MainWindow 構築が通ること(引数変更追従) |
| `test_app_controller.py` | §1 のとおり |
| `test_dynamic_devices.py` | §2 のとおり |

## 5. 手動チェック(契約)

- §2.8(multi-listener: SettingsPanel と ControlPanel の両方に状態が届く)
- §3.11(❌ 書き換え: 動作中デバイス変更 → 自動 restart + バナー。再列挙 fallback でも restart)
- §11.5(PID 選択完了 → Start enable 即時遷移)
- §13.2〜13.6(置き換え一覧の実施確認)
