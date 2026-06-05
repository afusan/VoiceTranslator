# feature/capture-kind — テスト項目

## small (自動 / `tests/test_capture_kind.py`)

| クラス::メソッド | 観点 |
|---|---|
| `TestCaptureKindEnum::test_values` | enum の値 `device` / `process` |
| `TestCaptureSourceKindField::test_default_kind_is_device` | `CaptureSource(kind=...)` 既定 DEVICE |
| `TestCaptureSourceKindField::test_explicit_kind_process` | PROCESS 明示で保持 |
| `TestAudioCaptureBackendDefault::test_default_capture_kind_is_device` | 抽象基底の classmethod 既定 DEVICE |
| `TestSoundcardBackendDeclaresDevice::test_capture_kind_is_device` | SoundcardCaptureBackend が DEVICE 宣言 |
| `TestSoundcardBackendDeclaresDevice::test_list_sources_kind_is_device` | `list_sources` の各 source に kind=DEVICE |
| `TestAppControllerGetCaptureKind::test_returns_kind_from_backend_class` | 登録済み backend の `capture_kind` を返す |
| `TestAppControllerGetCaptureKind::test_unknown_backend_falls_back_to_device` | 未登録は DEVICE フォールバック |
| `TestAppControllerGetCaptureKind::test_exception_in_capture_kind_falls_back` | `capture_kind()` が例外でも DEVICE |
| `TestSettingsPanelCaptureDisplay::test_dropdown_shows_kind_label_with_backend` | 「デバイス (soundcard)」表記 |
| `TestSettingsPanelCaptureDisplay::test_process_kind_is_labeled` | 「プロセス (proctap)」表記 |
| `TestSettingsPanelCaptureDisplay::test_selecting_display_saves_internal_name` | プルダウン選択で set_setting に backend 名 |
| `TestSettingsPanelCaptureDisplay::test_helper_display_to_internal_extracts_backend_name` | `_capture_display_to_internal` の単体動作 |

## 回帰

- `py -m uv run pytest tests/test_settings_panel_*.py tests/test_capture_backend_split.py tests/test_dynamic_devices.py -q` 全体緑(本ブランチで 69 件 + 13 件確認)
- `py -m uv run pytest -q --ignore=tests/test_google_cloud_tts_large.py` 全 small 964 件 pass(回帰なし)
- Python 3.12.13 で全テストが通ることを確認

## 手動

| 観点 | 手順 |
|---|---|
| Python 3.12 化 | `py -m uv run python --version` で `Python 3.12.13` |
| 「音声取得」プルダウン | アプリ起動 → 「バックエンド」セクション → 「音声取得」プルダウンに「デバイス (soundcard)」が並ぶ |
| 選択挙動 | プルダウン操作で従来通り動く(`backends.capture` 内部値は `soundcard` のまま) |
| 設定保存 | 「設定を保存」→ `config.yaml` の `backends.capture` が `soundcard` のまま(表示形式は ConfigStore に保存されない) |

## 段階 2 移行時に確認するもの(本ブランチでは対象外)

| 観点 | 期待 |
|---|---|
| PyPI Python 3.12 wheel | `pip install proc-tap` で cp312 wheel が降ってくる |
| ProcTap の出力フォーマット | 48kHz/2ch/float32 が VoiceTranslator の 16kHz/1ch/float32 に変換される |
| プロセス選択 | (段階 3 で `pycaw` 連携、本ブランチでは未着手) |
