# feature/proctap-backend — 計画

ProcTap 取り込みの **段階 2**。`ProcTapCaptureBackend` を実装し、`proc-tap` パッケージを
`AudioCaptureBackend` 抽象に適合させる。プロセス列挙とエコーバック確認 UI は **段階 3** で別途。

---

## 1. スコープ

### in
- `pyproject.toml`: extras `capture-proctap = ["proc-tap>=0.4"]` 追加(opt-in)
- `src/voice_translator/capture/proctap_backend.py` 新規:
  - `capture_kind() -> CaptureKind.PROCESS`
  - `list_sources()` は段階 3 まで **空リスト仮実装**(docstring で明記)
  - `start(source_id)` で `int(source_id)` → `ProcessAudioCapture(pid=..., resample_quality=...)`
  - `read_chunk()` で **48kHz/2ch/float32 → 16kHz/mono/float32** に変換:
    - `np.frombuffer(dtype=float32).reshape(-1, 2).mean(axis=1)` でモノラル化
    - `scipy.signal.resample_poly(up=1, down=3)` で 16kHz へリサンプル
  - 失敗系(extras 未インストール / 不正 PID / WASAPI 起動失敗 / read 失敗)を `FatalError` で包む
- `backend_setup.py` に opt-in register
- `ConfigStore`: `backends_config.proctap.{auto_load, resample_quality}` 既定
- `tests/test_proctap_backend.py`:
  - small 16 件(`capture_kind` / `list_sources` / `_convert_pcm` 単体 4 件 / ライフサイクル 9 件 / extras 未インストール時の挙動)
  - large 1 件(`@pytest.mark.large`、実 ProcTap で Python 自身の PID 録音、無音でもライフサイクル確認)
- 既存 `tests/test_backend_setup.py` の CAPTURE 期待値に `proctap` を追加
- docs: Class.md / manual.md / pendList.md / メタ Plan の完了反映

### out
- **プロセス列挙(`list_sources()` 実装)+ エコーバック確認 UI** は段階 3 で別ブランチ
- 動作中の CAPTURE backend 切替(動作中 restart)は **既存 pendList の課題**(本ブランチでは触らない)

---

## 2. 設計上のポイント

### 2-1. PCM 変換(`_convert_pcm`)

```
bytes(48kHz/2ch/float32, 1 frame = 8 bytes)
  → np.frombuffer(dtype=float32)              # 1D array (frames × 2)
  → reshape(-1, 2).mean(axis=1)               # mono にダウンミックス
  → scipy.signal.resample_poly(up=1, down=3)  # 48000 → 16000 Hz
  → astype(float32)
```

- `resample_poly` は polyphase filter ベースで品質と速度のバランスが良い。チャンク境界の
  アーチファクトは僅か残るが、ストリーミング処理では実用上問題なし。
- 端数(stereo frame として割り切れない 1 サンプル余り)は理論上発生しないが、防衛として
  切り捨て処理を入れる。

### 2-2. 遅延 import

- `from proctap import ProcessAudioCapture` は `start()` 内で遅延 import。
- `__init__` で `import proctap` の有無だけ確認し、未インストールなら `FatalError` を即発生。
- これは他の opt-in backend(pyannote / pvcobra 等)と同じパターン。

### 2-3. `source_id` の規約

- `source_id` は **PID の文字列**(例: `"1234"`)。`start()` 内で `int(source_id)` で整数化。
- 不正な値(`"abc"` 等)は `FatalError` で即時失敗。
- `CaptureSource.source_id` の自由形式文字列規約はそのまま維持(`AudioCaptureBackend` の
  抽象 I/F は変えない)。

### 2-4. `list_sources()` 仮実装の妥当性

段階 3 で「音声出力中のプロセスのみ列挙」を `pycaw` で実装する。段階 2 で全プロセスを仮列挙する
案もあるが:
- 全列挙は数百件並んで使いにくい(段階 3 の主題と相反する UX 悪化)
- 仮実装が UI で見えると「全プロセスが並んで使えない」印象を与える
- 段階 3 で消す手間が発生

そのため **段階 2 では `list_sources() = []`** とし、テストや手動運用では `start()` に直接 PID を
渡す。これで段階 3 の `pycaw` 連携時に「プロセス列挙だけ追加すれば動く」状態を維持できる。

### 2-5. Python 3.12 wheel の確認

PyPI 配布の `proc-tap==1.0.3` が `cp312-win_amd64` wheel として落ちてくることを確認(本ブランチで実証)。
段階 1 で `requires-python = ">=3.12"` に引き上げ済みのため、整合性 OK。

---

## 3. 変更ファイル

| ファイル | 変更内容 |
|---|---|
| `pyproject.toml` | extras `capture-proctap = ["proc-tap>=0.4"]` |
| `src/voice_translator/capture/proctap_backend.py` | 新規(ProcTapCaptureBackend + `_convert_pcm`) |
| `src/voice_translator/common/backend_setup.py` | opt-in register |
| `src/voice_translator/common/config_store.py` | `backends_config.proctap.{auto_load, resample_quality}` 既定 |
| `tests/test_proctap_backend.py` | 新規 small 16 件 + large 1 件 |
| `tests/test_backend_setup.py` | CAPTURE 期待値 `["soundcard", "proctap"]` に更新 |
| `docs/design/Class.md` | `AudioCaptureBackend` 実装に `ProcTapCaptureBackend` 追記 |
| `docs/manual.md` | extras / 「プロセス (proctap)」プルダウンの注意点(段階 3 まで列挙無し) |
| `docs/design/pendList.md` | 段階 2 を ✅完了 / 段階 3 はそのまま |
| `docs/design/feature-runtime-flex-and-input/Plan.md` | P6-2 完了マーク |

---

## 4. 確認手順

1. `py -m uv sync --extra cpu --extra capture-proctap` → `proc-tap` / `scipy` インストール
2. `py -m uv run python -c "import proctap; print(proctap.__version__)"` で `1.0.3` 確認
3. `py -m uv run pytest tests/test_proctap_backend.py -q` → small 16 件 pass
4. `py -m uv run pytest tests/test_proctap_backend.py -m large -q` → large 1 件 pass(実 ProcTap で自プロセス録音)
5. `py -m uv run pytest -q --ignore=tests/test_google_cloud_tts_large.py` → 全 small 981 件 pass(回帰なし)
6. 実機: `config.yaml` の `backends.capture` を `proctap`、`devices.input` に動作中アプリの PID(タスクマネージャで確認)を書く → 起動 → 当該プロセスの音が翻訳パイプラインを流れる

---

## 5. 段階 3(別ブランチで実装予定)

- `list_sources()` を `pycaw` で実装:
  - `AudioUtilities.GetAllSessions()` で音声セッション一覧
  - 各セッションの `IAudioMeterInformation.GetPeakValue()` で「現在音を出しているか」判定
  - プロセス名 + PID で `CaptureSource(source_id=str(pid), display_name=f"{name} ({pid})", kind=PROCESS)` を返す
- エコーバック確認機能(別ダイアログ or ControlPanel 内のメータ):
  - 選択中プロセスのキャプチャストリームをタップしてレベルメータ表示
  - ユーザが「鳴っているか」を視覚的に確認できる
- 詳細仕様は段階 3 着手時に詰める(pendList 起票済み)。
