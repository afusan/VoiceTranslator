# feature/tts-picks 計画

## 目的
`docs/design/append/backendCandidates.html` の TTS 候補で ✓ を付けた **4 backend を実装**する:
- **Piper TTS**(ローカル、マルチ OS、ONNX 軽量)
- **ElevenLabs**(クラウド、API key、プリメイド voice 主軸)
- **OpenAI TTS**(クラウド、既存 `openai_gpt` の API key 共有)
- **Google Cloud TTS**(クラウド、既存 `google_stt` のサービスアカウント JSON 共有)

親ブランチ `feature/tts-lang-support` で導入した `TtsBackend.supported_output_languages()` を
全 backend が宣言する。音声クローニングは `[⏳保留 2026-05-31]` で先送り、本ブランチでは
**各 backend のプリメイド voice 主軸**で実装する。

## 設計方針

### 共通(各 backend 共通)
- **遅延 import**: 各 backend の `__init__` 内で実依存(piper / httpx / google-cloud-texttospeech)
  を import。settings ダイアログを開いただけで重い依存が引かれないようにする。
- **status 管理**: `BackendBase` 継承で `INIT → DOWNLOADING(必要時)→ LOADING → LOADED` を発信。
- **エラーマッピング**: 401/403/quota → `FatalError`、429/5xx → `RecoverableError`(リトライ対象)、
  入力テキスト空 → `SkipError`。
- **PCM 出力**: 各 backend が(pcm: np.ndarray float32, samplerate: int)を返す。
  内部標準は 16kHz/mono/float32 だが、TTS は voice の native sample rate のままで OK
  (Output レイヤがリサンプリングする想定)。

### 個別 backend

#### 1. PiperTtsBackend(`src/voice_translator/tts/piper_backend.py`)
- 依存: `piper-tts` + `onnxruntime` + `huggingface_hub`(extras: `tts-piper`)
- voice モデル: `rhasspy/piper-voices`(Hugging Face)から DL
- 設定キー: `voice_name`(例: `en_US-amy-low`)
- API: `PiperVoice.load(onnx_path)` → `synthesize_stream_raw(text)` で int16 PCM bytes
- supported_output_languages: piper-voices で配布される代表言語(en/de/fr/es/it/zh/ru/pl 等)。
  **日本語(ja)は piper-voices に標準配布なし** → リストから外す
- 認証: 不要

#### 2. ElevenLabsTtsBackend(`src/voice_translator/tts/elevenlabs_backend.py`)
- 依存: `httpx`(extras: `tts-elevenlabs`)
- 認証: API key
- 設定キー: `voice_id`(プリメイドの ID、デフォルトは Rachel 等)、`model_id`(例: `eleven_multilingual_v2`)
- API: `POST /v1/text-to-speech/{voice_id}?output_format=pcm_16000`
  - `output_format=pcm_16000` を指定すれば 16kHz int16 PCM が直接返る(MP3 デコード不要)
  - body: `{"text": "...", "model_id": "..."}`
- verify_credentials: `GET /v1/voices` で疎通確認
- supported_output_languages: `eleven_multilingual_v2` の対応 29 言語

#### 3. OpenAITtsBackend(`src/voice_translator/tts/openai_tts_backend.py`)
- 依存: `httpx`(extras: `tts-openai-api`)
- 認証: API key(`openai_gpt` / `openai_whisper_api` と別保存。将来共有化は別ブランチ)
- 設定キー: `voice`(alloy / echo / fable / onyx / nova / shimmer)、`model`(tts-1 / tts-1-hd)
- API: `POST /v1/audio/speech` body `{"model": "...", "voice": "...", "input": "...", "response_format": "pcm"}`
  - response_format=pcm: **24kHz mono signed 16-bit PCM が直接返る**(WAV ヘッダなし)
- verify_credentials: `GET /v1/models` で疎通確認
- supported_output_languages: OpenAI TTS は Whisper 99 言語と同等(`common/whisper_languages.py` 流用)

#### 4. GoogleCloudTtsBackend(`src/voice_translator/tts/google_cloud_tts_backend.py`)
- 依存: `google-cloud-texttospeech`(extras: `tts-google`)
- 認証: サービスアカウント JSON(`google_stt` と同形式、`field_type=file`)
- 設定キー: `voice_name`(空ならデフォルト `<lang>-Standard-A`)、`default_language`(`en` 等の ISO 639-1)
- API: SDK の `synthesize_speech(input, voice, audio_config)` で `audio_encoding=LINEAR16` 指定
- verify_credentials: SDK の認証だけ確認(ListVoices で疎通)
- supported_output_languages: Google TTS の対応言語(40+)を ISO 639-1 で宣言

## 着手順序
1. **Plan / testPlan / verify スケルトン作成**
2. **Piper backend 実装**(無認証、最も独立)+ small テスト
3. **ElevenLabs backend 実装** + small + large テスト
4. **OpenAI TTS backend 実装** + small + large テスト
5. **Google Cloud TTS backend 実装** + small + large テスト
6. **backend_setup 登録** + pyproject extras 追加
7. **layer_settings_schema 拡張** + 詳細ダイアログ確認
8. **test_credential_flow の skip 解除**(OpenAI TTS)
9. **local.secrets キー追加**(placeholder)
10. **Class.md 更新** + verify.md 充実化
11. **全 small テスト pass 確認** → commit

## 既存設計への影響
- TTS backend が 1 → 5 に増える(SAPI + 4 新規)
- `backend_setup` の register 呼び出しが増える(各 backend に `backend_cls` + `capabilities` 付与)
- `local.secrets` に新規キー(`elevenlabs.api_key` / `openai_tts.api_key` / `google_tts.credentials_path`)
- pyproject extras に `tts-piper` / `tts-elevenlabs` / `tts-openai-api` / `tts-google` 追加

## 対象外(後続ブランチへ)
- 音声クローニング(`feature/tts-voice-cloning`、pendList [⏳保留 2026-05-31])
- OpenAI key を `openai_gpt` / `openai_whisper_api` / `openai_tts` で共有化
- GUI で voice 一覧を `list_voices` API から動的取得(ElevenLabs / Google)
- Piper の voice モデル切替時の DL 進捗バー
