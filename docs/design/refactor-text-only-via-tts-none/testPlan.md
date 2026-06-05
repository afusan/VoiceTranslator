# refactor/text-only-via-tts-none — テスト項目

## small (自動)

### `tests/test_settings_panel_tts_none.py`(新規)

| クラス::メソッド | 観点 |
|---|---|
| `TestHelpers::test_display_to_internal_for_none` | `(なし)` → `none` 変換 |
| `TestHelpers::test_display_to_internal_for_real_backend` | 実 backend 名はそのまま |
| `TestHelpers::test_internal_to_display_for_none` | `none` → `(なし)` 変換 |
| `TestHelpers::test_internal_to_display_for_real_backend` | 実 backend 名はそのまま |
| `TestTtsDropdownChoices::test_tts_dropdown_includes_none_choice` | TTS プルダウンに `(なし)` が末尾追加 |
| `TestInitialDisplay::test_initial_value_for_sapi` | `backends.tts="sapi"` で StringVar=`sapi` |
| `TestInitialDisplay::test_initial_value_for_none` | `backends.tts="none"` で StringVar=`(なし)` |
| `TestOnBackendChange::test_selecting_none_saves_internal_none` | `(なし)` 選択で set_setting に `"none"` |
| `TestOnBackendChange::test_selecting_none_skips_cloud_consent` | `(なし)` はクラウド同意 gate を呼ばない |
| `TestOnBackendChange::test_selecting_real_backend_goes_through_consent` | 実 backend 選択は同意 gate を通す |
| `TestOutputRowGreyedOutByTtsNone::test_output_dropdown_disabled_when_tts_none` | TTS=(なし) で Output 行 disable |
| `TestOutputRowGreyedOutByTtsNone::test_output_dropdown_enabled_when_tts_real` | TTS=実 backend で Output 行 normal |
| `TestOutputRowGreyedOutByTtsNone::test_switching_to_none_disables_output_row` | sapi → (なし) 切替で Output 行 disable に変わる |

### `tests/test_text_only_output.py`(書き換え)

| クラス::メソッド | 観点 |
|---|---|
| `TestAppControllerOutputMode::test_default_with_tts_choice_is_audio` | `backends.tts="sapi"` で audio |
| `TestAppControllerOutputMode::test_tts_none_is_text_only` | `backends.tts="none"` で text_only |
| `TestAppControllerOutputMode::test_empty_tts_is_text_only` | 空文字も text_only |
| `TestAppControllerOutputMode::test_missing_tts_key_is_text_only` | キー欠落も text_only |
| `TestAppControllerOutputMode::test_active_layers_audio_has_all` | audio で全レイヤ |
| `TestAppControllerOutputMode::test_active_layers_text_only_excludes_tts_output` | text_only で TTS/Output 除外 |
| `TestAppControllerHandleTextReady::*` | `_make_ctrl_with_logs` を `backends.tts` 派生に変更 |
| `TestConfigStoreDefault::test_default_backends_tts_is_sapi` | `DEFAULT_CONFIG["backends"]["tts"]=="sapi"` |
| `TestConfigStoreDefault::test_no_output_mode_key_in_pipeline_defaults` | `pipeline.output_mode` キー無し |
| `TestConfigStoreDefault::test_loaded_config_keeps_tts_none` | yaml の `backends.tts: none` が load 後保持される |

その他 `TestCoordinator*` クラスは P3 の振る舞いそのままなので変更不要。

## 回帰

- `py -m uv run pytest -q --ignore=tests/test_google_cloud_tts_large.py` 全体 916 件 pass(本ブランチで確認済み)。
- 既存 `test_settings_panel_lang.py` / `test_settings_panel_sections.py` も変更なしで通る。

## 手動

| 観点 | 手順 |
|---|---|
| プルダウン外観 | アプリ起動 → 「バックエンド」セクション → TTS プルダウン末尾に `(なし)` |
| (なし) 選択 | TTS で `(なし)` 選択 → Output 行が灰色化 / dropdown/設定ボタンが操作不可 |
| 縦通し | (なし) で開始 → テキストのみ履歴に出る、音は鳴らない |
| 実 TTS 復帰 | TTS を SAPI 等に戻す → Output 行が活性化、ロード後に音声出力が復活 |
