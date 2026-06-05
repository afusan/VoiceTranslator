# feature/proctap-backend — テスト項目

## small (自動 / `tests/test_proctap_backend.py`)

### `TestCaptureKindAndListSources`

| メソッド | 観点 |
|---|---|
| `test_capture_kind_is_process` | `ProcTapCaptureBackend.capture_kind() == PROCESS` |
| `test_list_sources_is_empty_until_stage3` | 段階 3 まで空リスト |

### `TestConvertPcm`

| メソッド | 観点 |
|---|---|
| `test_empty_bytes_returns_empty_array` | 空入力で長さ 0 の float32 ndarray |
| `test_downsample_ratio_is_three_to_one` | 48kHz/3000 frame → 16kHz/~1000 sample |
| `test_stereo_is_averaged_to_mono` | L=+1.0 / R=-1.0 をダウンミックスすると平均 ~0 |
| `test_odd_size_buffer_is_truncated` | stereo の端数 1 サンプルは切り捨てて続行 |

### `TestLifecycleWithMockedProcTap`(`proctap.ProcessAudioCapture` を monkeypatch)

| メソッド | 観点 |
|---|---|
| `test_start_passes_int_pid` | `start("1234")` で `ProcessAudioCapture(pid=1234, ...)` |
| `test_start_with_non_integer_source_id_raises_fatal` | `"not-a-pid"` で FatalError |
| `test_start_when_proctap_raises_becomes_fatal` | proc-tap 構築失敗 → FatalError、`_tap` は None に復帰 |
| `test_double_start_raises_runtime_error` | start 中の再 start で RuntimeError |
| `test_read_chunk_before_start_raises_runtime_error` | start 前の read_chunk で RuntimeError |
| `test_read_chunk_converts_bytes` | proc-tap の 48kHz/2ch bytes → 16kHz/mono ndarray |
| `test_read_chunk_returns_none_on_empty` | 空 bytes → None |
| `test_read_chunk_raises_fatal_on_proctap_error` | read 例外 → FatalError |
| `test_stop_is_idempotent` | stop 2 回呼んでも 1 回だけ proc-tap.stop が呼ばれる |

### `TestProctapMissing`

| メソッド | 観点 |
|---|---|
| `test_init_raises_fatal_when_proctap_missing` | `sys.modules["proctap"] = None` 状態で `__init__` が FatalError |

## large(自動だが手動実行向け)

### `TestProcTapLargeSelfCapture`(`@pytest.mark.large`)

| メソッド | 観点 |
|---|---|
| `test_lifecycle_with_real_proctap` | 実 ProcTap で Python 自身の PID から start → 数回 read_chunk → stop。無音でも例外なくライフサイクルが回る(chunk が None / 全 0 でも OK)。chunk が返れば dtype=float32 / ndim=1 を確認 |

実行: `py -m uv run pytest tests/test_proctap_backend.py -m large -q`

## 回帰

- `tests/test_backend_setup.py`: CAPTURE の期待値 `["soundcard", "proctap"]` に更新済み
- 全 small `py -m uv run pytest -q --ignore=tests/test_google_cloud_tts_large.py` で **981 件 pass / 5 skipped**(回帰なし)

## 手動(実機)

| 観点 | 手順 |
|---|---|
| extras インストール | `py -m uv sync --extra cpu --extra capture-proctap` → エラーなく `proc-tap==1.0.3` / `scipy` が入る |
| アプリ起動 | `config.yaml` の `backends.capture: proctap`、`devices.input: "<pid>"`(動作中アプリの PID、タスクマネージャ参照) → 起動 → 当該プロセスの音だけが翻訳される |
| 不正 PID | `devices.input: "abc"` で起動 → FatalError が起動時に通知される |
| 音が出ていないプロセス | 起動直後の Idle Python など → 無音が流れて何も翻訳されない(エラー無し) |

## 段階 3 で確認予定(本ブランチでは未着手)

| 観点 | 備考 |
|---|---|
| プロセス列挙(`pycaw`) | 「音声出力中」フィルタの判定閾値 / 更新頻度 |
| エコーバック確認 UI | レベルメータの置き場所(別ダイアログ / ControlPanel) |
