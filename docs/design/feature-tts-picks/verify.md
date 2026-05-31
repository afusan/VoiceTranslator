# feature/tts-picks 動作確認手順

本ブランチで追加した 4 つの TTS backend(Piper / ElevenLabs / OpenAI TTS /
Google Cloud TTS)と、親ブランチの TTS 対応言語連動
(`feature/tts-lang-support`)の動作確認手順をまとめる。
ASR / Translator 側 verify.md と同じ運用。

---

## 0. 事前準備

```powershell
# 基本(MVP のみ、SAPI で十分なら追加 extras 不要)
py -m uv sync --extra cpu     # GPU なし環境
py -m uv sync --extra cuda    # NVIDIA GPU 環境

# 検証する TTS backend ごとに extras を追加
py -m uv sync --extra cpu --extra tts-piper        # Piper TTS(ローカル)
py -m uv sync --extra cpu --extra tts-elevenlabs   # ElevenLabs
py -m uv sync --extra cpu --extra tts-openai-api   # OpenAI TTS
py -m uv sync --extra cpu --extra tts-google       # Google Cloud TTS
```

GUI 起動:
```powershell
py -m voice_translator
```

`local.secrets`(プロジェクトルート、`.gitignore` 済み)に認証情報:
```json
{
  "elevenlabs":  { "api_key": "..." },
  "openai_tts":  { "api_key": "sk-..." },
  "google_tts":  { "credentials_path": "C:/path/to/sa.json" }
}
```
(Piper は無認証)

---

## 1. Piper TTS(ローカル、マルチ OS、ONNX 軽量)

### 動作確認
1. アプリ起動 → 設定で TTS を `piper` に切替(認証不要なので同意ダイアログは出ない)
2. **出力言語(tgt)プルダウンが Piper 対応言語**(en / de / fr / es / it / zh 等)に切り替わる
   - **日本語(ja)は Piper 対応外**: もし切替前に tgt=ja なら警告バナー
     「TTS バックエンド piper は読み上げ言語 ja (Japanese) に対応していません」が出る
3. TTS 「設定」→ voice ドロップダウンから `en_US-amy-low` 等を選択 → 保存
4. 中央「↻ ロード」→ 初回は voice モデル DL(`DOWNLOADING → LOADING → LOADED`)
   - HF キャッシュ後の 2 回目以降は即 LOADED
5. 動作開始 → 音声が再生される

### 期待動作
- voice モデル形式が不正 → FatalError(「voice_name の形式」)
- HF 通信失敗 → FatalError(「Piper voice DL 失敗」)
- 合成中例外 → FatalError
- voice の sample rate(amy-low は 16kHz)で PCM が返る

### 自動テスト
- small: `py -m uv run pytest tests/test_piper_tts.py`
- large(実 voice DL): `py -m uv run pytest -m large tests/test_piper_tts_large.py`

---

## 2. ElevenLabs(クラウド、プリメイド voice)

### 動作確認
1. extras を入れて起動
2. 設定で TTS を `elevenlabs` に切替 → 同意ダイアログ → 同意
3. **出力言語プルダウンが ElevenLabs 対応言語**(en / ja / zh / ko / fr / de … 31 言語)に
   切り替わる(`eleven_multilingual_v2` 基準)
4. TTS の「設定」→ API key 入力 → 「テスト」で OK(`/v1/voices` で疎通確認)
5. voice_id はデフォルトで Rachel(`21m00Tcm4TlvDq8ikWAM`)。
   - 別 voice を使うなら ElevenLabs ダッシュボード → Voices ページから ID をコピーして入力
6. 中央「↻ ロード」→ LOADED → 動作開始
7. 音声が再生される(プリメイド voice 主軸)

### 期待動作
- 401 = 「認証エラー」(Fatal、再試行されない)
- 422 = 「入力エラー」(voice_id が無効など、Fatal)
- 429 / 5xx = 自動リトライ
- 出力 PCM: 16kHz mono float32(`output_format=pcm_16000` で raw 取得)

### 既知の注意点
- 音声クローニング(IVC)は本ブランチでは未対応。pendList [⏳保留 2026-05-31] /
  別ブランチ `feature/tts-voice-cloning` で対応予定
- 月額無料 tier は数万文字/月。超えると 422 を返す可能性

### 自動テスト
- small: `py -m uv run pytest tests/test_elevenlabs_tts.py`
- 契約: `py -m uv run pytest tests/test_credential_flow.py::TestElevenLabsTtsCredentials`
- large(実 API): `py -m uv run pytest -m large tests/test_elevenlabs_tts_large.py`

---

## 3. OpenAI TTS(クラウド、プリメイド 6 voice)

### 動作確認
1. extras を入れて起動
2. 設定で TTS を `openai_tts` に切替 → 同意ダイアログ → 同意
3. **出力言語プルダウンが Whisper 99 言語ベース**で広く切り替わる
4. TTS 「設定」→ API key 入力 → 「テスト」(OpenAI `/v1/models` で疎通)
5. voice ドロップダウンで `alloy / echo / fable / onyx / nova / shimmer` 切替可能
6. model ドロップダウンで `tts-1`(低レイテンシ)/ `tts-1-hd`(高品質)切替
7. 中央「↻ ロード」→ LOADED → 動作開始 → 音声再生

### 期待動作
- 401 = Fatal、429 / 5xx = 自動リトライ
- 出力 PCM: **24kHz** mono float32(`response_format=pcm` で raw)
- OpenAI key は `openai_gpt` / `openai_whisper_api` と **別保存**(同じ key を別フィールドに入力)
  - 将来の共有化は別ブランチで検討

### 自動テスト
- small: `py -m uv run pytest tests/test_openai_tts.py`
- 契約: `py -m uv run pytest tests/test_credential_flow.py::TestOpenAITtsApiCredentials`
- large(実 API): `py -m uv run pytest -m large tests/test_openai_tts_large.py`

---

## 4. Google Cloud TTS(クラウド、サービスアカウント JSON)

### 動作確認
1. extras を入れて起動
2. 設定で TTS を `google_tts` に切替 → 同意ダイアログ → 同意
3. **出力言語プルダウンが Google TTS 対応 30+ 言語**に切り替わる
4. TTS 「設定」→ Service Account JSON ファイルを「参照…」で選択 → 「テスト」
   - `list_voices` で疎通確認
   - `google_stt` と同じ JSON を流用可能(同じ GCP プロジェクトで両方の API を有効化していれば)
5. voice 名は空でも OK(言語コードから既定 voice が自動選択される)。
   - 細かく指定するなら `en-US-Wavenet-A` / `ja-JP-Neural2-B` 等
6. 中央「↻ ロード」→ LOADED → 動作開始 → 音声再生

### 期待動作
- PERMISSION_DENIED / UNAUTHENTICATED = Fatal(認証エラー、API 未有効化等)
- その他例外(DEADLINE_EXCEEDED など)= Recoverable(自動リトライ)
- 出力 PCM: 16kHz LINEAR16 mono → float32 変換
- 言語コード自動マップ: `tgt_lang=en` → `en-US`、`ja` → `ja-JP` 等
  (`_ISO_TO_BCP47` テーブル参照)

### 自動テスト
- small: `py -m uv run pytest tests/test_google_cloud_tts.py`
- 契約: `py -m uv run pytest tests/test_credential_flow.py::TestGoogleCloudTtsCredentials`
- large(実 API): `py -m uv run pytest -m large tests/test_google_cloud_tts_large.py`

---

## 5. 横断確認(親ブランチ feature/tts-lang-support の機能)

### TTS 対応言語の警告連動
- TTS backend を切り替えるたびに **現在の Translator 出力言語(tgt)が TTS で読めるか確認**、
  非対応なら警告バナーが出る
- tgt_lang を切り替えるたびにも同じ確認
- Translator backend 切替で tgt が fallback で変わった後も確認
- 警告は出すが **TTS / tgt_lang は勝手に変更しない**(ASR / Translator の自動 fallback とは別方針)
- 例:
  - Translator: `deepl`(tgt=ja)→ TTS: `piper` に切替 → Piper は ja 非対応 →
    「TTS バックエンド piper は読み上げ言語 ja (Japanese) に対応していません」通知
  - tgt を `en` に変更 → 警告消える

### 全体テスト
```powershell
py -m uv run pytest                    # small すべて
py -m uv run pytest -m large           # large(認証済み backend だけ通る)
```

---

## 6. マージ前チェックリスト
- [ ] 4 backend それぞれで「動作確認」を一通り実施
- [ ] TTS 互換警告バナーが出る(Piper × ja 等)
- [ ] `py -m uv run pytest` が failure 0
- [ ] `local.secrets` を用意した backend は `-m large` で通る
- [ ] CLAUDE.md ルール準拠(マージは `--no-ff`、リモートに触らない)
