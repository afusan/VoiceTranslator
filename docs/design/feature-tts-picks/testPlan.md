# feature/tts-picks テスト項目

## small テスト(モック中心、< 1秒/件)

### `tests/test_piper_tts.py`
- `supported_output_languages` が piper-voices の主要言語(en/de/fr 等)を含み、
  日本語(ja)は含まないこと
- 既定 voice ロードで HF DL が 2 回(`.onnx` + `.onnx.json`)走る
- `piper` 不在で `FatalError("piper-tts ... uv sync --extra tts-piper")`
- voice_name 形式が不正 → `FatalError("voice_name の形式")`
- HF DL 失敗 → `FatalError("Piper voice")`
- 空テキスト → `SkipError`
- synthesize → float32 PCM (sample_rate は voice の native)
- 空音声 → `SkipError`
- 合成中例外 → `FatalError("Piper 合成失敗")`

### `tests/test_openai_tts.py`
- `supported_output_languages` が Whisper 99 言語ベース(en / ja を含み、auto は含まない)
- api_key 未設定 → `MISSING_CREDENTIALS` 状態
- api_key 設定 → `LOADED`
- 空テキスト → `SkipError`
- 200 OK → float32 PCM (24kHz)
- 401 → `FatalError("認証")`
- 429 → `RecoverableError`
- 5xx → `RecoverableError`
- 400 other → `FatalError`
- api_key なしで synthesize → `FatalError("API Key")`

### `tests/test_elevenlabs_tts.py`
- `supported_output_languages` が multilingual_v2 の代表 (en/ja/zh/fr/de/ko/hi 等)を含む
- api_key 未設定 → `MISSING_CREDENTIALS` 状態
- api_key 設定 → `LOADED`
- 空テキスト → `SkipError`
- 200 OK → float32 PCM (16kHz)
- 401 → `FatalError("認証")`
- 422 → `FatalError("入力エラー")`(voice_id 無効など)
- 429 / 5xx → `RecoverableError`
- api_key なしで synthesize → `FatalError("API Key")`

### `tests/test_google_cloud_tts.py`
- `supported_output_languages` が major 言語(en / ja / fr / de / es / zh / ko)を含む
- credentials_path 未設定 → `MISSING_CREDENTIALS`
- credentials_path 設定 → `LOADED`
- 空テキスト → `SkipError`
- 正常 → float32 PCM (16kHz)
- credentials なしで synthesize → `FatalError("認証情報")`
- PERMISSION_DENIED / UNAUTHENTICATED → `FatalError("認証")`
- その他例外 → `RecoverableError`

### `tests/test_credential_flow.py`(契約テスト)
親ブランチで追加した `TestOpenAITtsApiCredentials` の skip 解除 + 中身実装、
および `TestElevenLabsTtsCredentials` / `TestGoogleCloudTtsCredentials` を追加。
全 backend に対して以下の契約を確認:
- credential_spec が必要キー(api_key / credentials_path)を宣言する
- 有効な認証で `verify_credentials` が `ok=True`
- 無効な認証(401 / JSON 不正)で `ok=False`
- 実行中の 401 → `FatalError`(AppController が `invalidate_verification` を呼ぶ前提)

### `tests/test_backend_setup.py`
- TTS 5 backend(sapi / piper / elevenlabs / openai_tts / google_tts)が登録される
- fixture に 4 backend モジュールを追加

## large テスト(実 API / 実モデル必須、手動実行)
- `tests/test_piper_tts_large.py` — 既定 voice (en_US-amy-low) を HF から DL → 合成
- `tests/test_openai_tts_large.py` — 実 API key で verify + synthesize(24kHz PCM)
- `tests/test_elevenlabs_tts_large.py` — 実 API key で verify + Rachel voice 合成(16kHz PCM)
- `tests/test_google_cloud_tts_large.py` — 実 SA JSON で verify + synthesize(16kHz PCM)

実行: `py -m uv run pytest -m large tests/test_<backend>_large.py`

skip 条件: extras 未インストール / `local.secrets` の対応キーが placeholder("xxxxx")。

## 手動確認(GUI)
`verify.md` 参照。

## 対象外
- 音声クローニング(pendList [⏳保留 2026-05-31])
- voice 一覧の動的取得(list_voices API 経由)
- Piper の voice モデル切替時の DL 進捗バー
- OpenAI key の `openai_gpt` / `openai_whisper_api` / `openai_tts` 横断共有化
