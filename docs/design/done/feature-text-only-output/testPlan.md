# feature/text-only-output — テスト項目

## small (自動 / `tests/test_text_only_output.py`)

| クラス | 観点 |
|---|---|
| `TestCoordinatorTextOnlyConstruction::test_audio_mode_requires_tts_and_output` | audio で tts/output が None なら ValueError |
| `TestCoordinatorTextOnlyConstruction::test_text_only_allows_none_tts_and_output` | text_only で tts/output が None で構築できる |
| `TestCoordinatorTextOnlyConstruction::test_unknown_mode_falls_back_to_audio` | 未知の output_mode は audio として扱う |
| `TestCoordinatorTextOnlyRuntime::test_no_tts_output_threads_in_text_only` | text_only で tts_thread / output_thread が生成されない |
| `TestCoordinatorTextOnlyRuntime::test_text_only_calls_on_text_ready` | text_only で on_text_ready 受信 / on_utterance_done 未受信 / timeline に t_translate 含まれ t_tts/t_playback 無し |
| `TestCoordinatorTextOnlyRuntime::test_text_only_does_not_invoke_tts_or_output` | spy backend を渡しても synthesize/play が 0 回 |
| `TestCoordinatorTextOnlyRuntime::test_text_only_ledger_drained_after_translator` | ready した seq_id が ledger に残らない |
| `TestCoordinatorTextOnlyRuntime::test_text_only_does_not_use_translated_or_synthesized_queue` | translated/synthesized キュー qsize=0 |
| `TestCoordinatorModeSwitchBuffers::test_audio_to_text_only_no_leak` | audio → text_only 別 Coordinator で残骸なし |
| `TestCoordinatorModeSwitchBuffers::test_restart_drains_old_queues` | 同一 Coordinator(text_only)の 2 回 start で drain が効く |
| `TestCoordinatorAudioRegression::test_audio_mode_still_works` | audio 既定動作の回帰 / t_playback まで埋まる |
| `TestAppControllerOutputMode::test_output_mode_default_is_audio` | デフォルト audio |
| `TestAppControllerOutputMode::test_output_mode_text_only` | 設定読み込みで text_only |
| `TestAppControllerOutputMode::test_output_mode_unknown_falls_back_to_audio` | 未知値は audio |
| `TestAppControllerOutputMode::test_active_layers_audio_has_all` | audio で全レイヤ |
| `TestAppControllerOutputMode::test_active_layers_text_only_excludes_tts_output` | text_only で TTS/Output 除外 |
| `TestAppControllerHandleTextReady::test_text_only_writes_logs` | text_only で jsonl/processtime/_push に書く |
| `TestAppControllerHandleTextReady::test_audio_does_not_write_logs_in_text_ready` | audio では _handle_text_ready は UI 通知のみ |
| `TestAppControllerHandleTextReady::test_text_only_log_failure_does_not_break_ui_notify` | ログ書き出し失敗時も UI 通知される |
| `TestConfigStoreDefault::test_pipeline_output_mode_default_is_audio` | DEFAULT_CONFIG 確認 |
| `TestConfigStoreDefault::test_loaded_config_keeps_user_value` | 保存 yaml の値が load 後保持される |

## 回帰

- `py -m uv run pytest tests/test_pipeline.py tests/test_pipeline_e2e.py tests/test_app_controller.py` 緑(全レイヤ backend が居る audio パス)。
- `py -m uv run pytest -q --ignore=tests/test_google_cloud_tts_large.py` 全 small 緑(本作業中 901 passed)。

## 手動

| 観点 | 手順 |
|---|---|
| 出力モード切替 UI | 「バックエンド」セクション → 「出力モード」を「テキストのみ」に切替 → TTS/Output 行がグレーアウト |
| text_only 縦通し | テキストのみで開始 → 履歴に翻訳テキストが表示される / 音は鳴らない |
| audio 復帰 | 「音声で出力」に戻す → 設定保存 → 再起動(or ↻ ロード)→ 音声出力が復活 |
| バッファ確認 | text_only モードで長時間動作させ、メモリ使用量が増え続けないこと(ledger leak の感覚的確認) |
| 認証 gate | クラウド TTS 選択中でも text_only なら認証情報なしで Start できる |
