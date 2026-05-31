@echo off
REM 全 extras を CUDA(NVIDIA GPU)build で一括インストール。
REM cpu と cuda は排他(pyproject.toml の [tool.uv].conflicts で宣言済)。
REM CUDA 12.6 wheels を使うため、ドライババージョン 555+ が目安。
REM 含まれる backend は setup_all_cpu.bat と同じ。

echo === Installing all extras (CUDA build) ===
py -m uv sync ^
  --extra cuda ^
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
echo === All extras installed (CUDA build) ===
