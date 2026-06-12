@echo off
REM 全 extras を CPU floor で一括インストール。
REM 配布方針: CPU を floor、GPU は opt-in(GPU 版は setup_all_gpu.bat)。
REM 含まれる backend:
REM   VAD       : webrtcvad / pyannote.audio / pvcobra(vad-extra)
REM   ASR       : openai-whisper(公式)/ OpenAI Whisper API / Google Cloud STT / Deepgram
REM   Translator: DeepL / OpenAI GPT / Anthropic Claude
REM   TTS       : Piper / ElevenLabs / OpenAI TTS / Google Cloud TTS
REM MVP の faster-whisper / NLLB-200 / SAPI は base 依存に含まれるため extras 指定不要。

echo === Installing all extras (CPU build) ===
py -m uv sync ^
  --extra cpu ^
  --extra vad-extra ^
  --extra asr-whisper-official ^
  --extra asr-openai-api ^
  --extra asr-google-stt ^
  --extra asr-deepgram ^
  --extra translator-deepl ^
  --extra translator-openai-api ^
  --extra translator-anthropic ^
  --extra tts-piper ^
  --extra tts-elevenlabs ^
  --extra tts-openai-api ^
  --extra tts-google
if errorlevel 1 (
  echo === FAILED ===
  exit /b 1
)
echo === All extras installed (CPU build) ===
