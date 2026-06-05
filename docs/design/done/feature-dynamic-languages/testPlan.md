# feature/dynamic-languages — テスト項目

## small (自動)

| ファイル | 観点 |
|---|---|
| `tests/test_dynamic_languages.py::TestCoordinatorSetLanguages::test_both_swap` | src/tgt 同時更新 |
| `tests/test_dynamic_languages.py::TestCoordinatorSetLanguages::test_none_keeps_field` | None で当該フィールド維持 |
| `tests/test_dynamic_languages.py::TestCoordinatorSetLanguages::test_no_args_is_noop` | 引数無しは no-op |
| `tests/test_dynamic_languages.py::TestCoordinatorSetLanguages::test_coerces_to_string` | str 以外は str に変換 |
| `tests/test_dynamic_languages.py::TestCoordinatorSetLanguages::test_next_payload_uses_new_src_lang` | 切替後 self._src_lang を読んだ RawPayload に新値が乗る |
| `tests/test_dynamic_languages.py::TestAppControllerLanguageRelay::*` | running 時 → 転送 / 停止時 → 転送せず / 他キー → 転送せず / 値 str 強制 / 未知キー → 無視 |

## middle (自動)

| ファイル | 観点 |
|---|---|
| `tests/test_pipeline_e2e.py::TestPipelineE2EWithSynthPcm::test_set_languages_takes_effect_on_next_utterance` | WAV を流して動作中に tgt を en に切替 → 切替前は "-> ja"、切替後は "-> en" の発話が両方 done に現れる |

## 回帰

- `tests/test_pipeline.py` 全体 / `tests/test_app_controller.py` 全体 が引き続き緑。
- `py -m uv run pytest -q --ignore=tests/test_google_cloud_tts_large.py` で small 892 passed。

## 手動

| 観点 | 手順 |
|---|---|
| 動作中 tgt 切替 | アプリ起動 → 開始 → 翻訳セクションで tgt を変更 → 次発話以降の翻訳が新言語に切り替わる |
| 動作中 src 切替 | 開始 → 入力言語を変更 → 既にキューに入っている発話は古い hint で完走、以降の発話は新 hint |
| 停止中 → 起動 | 停止状態で言語変更 → 起動 → 設定通りの言語で動く(従来動作の維持) |
