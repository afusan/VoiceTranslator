# Phase F1: VAD バックエンド3件追加 (Test Plan)

## small テスト (毎回 run)

### `tests/test_webrtcvad_backend.py`
- 依存 `webrtcvad` モジュールを `sys.modules` でモック
- 初期化: `Vad(aggressiveness)` が呼ばれる / `LOADED` 遷移
- import 失敗時 → `FatalError`
- フレーム判定:
  - 連続 N フレーム speech 検出で「speech 開始」
  - 連続 M フレーム silence で「speech 終了」 → `VadSegment` emit
  - 部分バッファ(フレーム長未満)は持ち越し
- `max_speech_sec` で強制区切り
- `reset()` で内部バッファクリア

### `tests/test_pyannote_vad_backend.py`
- 依存 `pyannote.audio.Pipeline` をモック(`from_pretrained`)
- 初期化: HF token を受けて `Pipeline.from_pretrained` が呼ばれる
- token なし → `FatalError`
- `process(chunk)` → 一定量バッファ後 pipeline 起動 → アノテーション → 切り出し
- `reset()`
- `credential_spec()` が `hf_token` を返す
- `verify_credentials({"hf_token": "x"})` の正常/失敗(モック)

### `tests/test_pvcobra_vad_backend.py`
- 依存 `pvcobra` をモック
- 初期化: access_key 必須 / `Cobra(access_key=...).process(pcm)` を呼ぶ
- access_key 欠落で `MISSING_CREDENTIALS`
- 連続 N フレーム probability > threshold → speech 開始
- 連続 M フレーム probability < threshold → speech 終了 → `VadSegment`
- `max_speech_sec` 強制区切り
- `reset()`
- `credential_spec()` が `access_key` を返す
- `verify_credentials({"access_key": "valid"})` 正常 / `RuntimeError` 例外時に `ok=False`

### `tests/test_backend_setup.py` 追加分
- 新 VAD backend が `registry.list_names(VAD)` に並ぶ
- 各 backend の config が反映される
  - webrtcvad: `aggressiveness`, `frame_ms`, `min_speech_ms`, `min_silence_ms`, `max_speech_sec`
  - pyannote: `model_id`, `device`, `min_speech_ms`, `min_silence_ms`, `max_speech_sec`
  - pvcobra: `threshold`, `min_speech_ms`, `min_silence_ms`, `max_speech_sec`
- 認証必須 backend (pyannote / pvcobra) は `BackendCapabilities(requires_credentials=True)`
- `backend_cls` が登録され `credential_spec()` を引ける

### `tests/test_vad_switching.py` (新規)
モデル切替の構造テスト。VAD レイヤで silero → webrtcvad → pyannote → pvcobra と切り替えた時に:
- 旧 backend の subscription が解除される
- `_backends[VAD]` が新 backend インスタンスで上書きされる
- 状態遷移が `INIT → LOADING → LOADED` (or 認証無しなら MISSING_CREDENTIALS) を通る
- pvcobra に切替後、access_key 未入力で `start_pipeline` が gate される
- access_key 入力 + verified 後は gate を通過

ファクトリは全てモック差し替え(実 backend は呼ばない)。

## middle テスト
- 今回は無し(small で構造を抑え、large で実機検証する方針)

## large テスト
- 手動で `uv sync --extra cpu --extra vad-extra` を打って依存をインストールした上で:
  - 各 backend が本物の audio で動くこと(testData の WAV を流す)
  - pyannote は HF token 必須なので、token を `local.secrets` に置いた状態で検証
- 自動化はしない(モデル DL 時間と HF token の扱いを CI に乗せたくない)
