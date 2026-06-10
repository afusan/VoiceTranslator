"""OpenAiWhisperApiTranslateBackend の実 API 動作確認(large テスト)。

実物の OpenAI `/v1/audio/translations` で複合の契約
(src_text 空 / tgt_lang="en" / テキスト型)が守られることを検証する。
従量課金が発生する(音声数秒ぶん)。CI には載せない(`@pytest.mark.large`)。
"""

from __future__ import annotations

import json
import wave
from pathlib import Path

import numpy as np
import pytest

from voice_translator.common.types import INTERNAL_SAMPLE_RATE, ModelStatus


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SECRETS_PATH = PROJECT_ROOT / "local.secrets"
SPEECH_WAV = PROJECT_ROOT / "docs" / "forRunner" / "testData" / "seq_0001_vad.wav"


def _read_api_key() -> str | None:
    if not SECRETS_PATH.exists():
        return None
    try:
        data = json.loads(SECRETS_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    # 専用エントリは無い想定: 同じ OpenAI key を使う openai_whisper_api を流用
    key = (
        (data.get("openai_whisper_api_translate") or {}).get("api_key")
        or (data.get("openai_whisper_api") or {}).get("api_key")
        or ""
    )
    # placeholder("xxxxx" 等)は未設定とみなす(fail ではなく skip に倒す)
    if not key or key.strip("x") == "" or len(key) < 10:
        return None
    return key


def _load_speech_pcm() -> np.ndarray:
    """実発話 WAV(テストデータ)を 16kHz/mono/float32 で読む。無ければサイン波。"""
    if SPEECH_WAV.exists():
        with wave.open(str(SPEECH_WAV), "rb") as wf:
            frames = wf.readframes(wf.getnframes())
            pcm_i16 = np.frombuffer(frames, dtype=np.int16)
        return (pcm_i16.astype(np.float32) / 32768.0)
    n = int(2.0 * INTERNAL_SAMPLE_RATE)
    t = np.arange(n, dtype=np.float32) / INTERNAL_SAMPLE_RATE
    return (0.3 * np.sin(2 * np.pi * 220.0 * t)).astype(np.float32)


@pytest.fixture(scope="module")
def api_key() -> str:
    key = _read_api_key()
    if not key:
        pytest.skip("local.secrets に OpenAI api_key が無いため skip")
    return key


@pytest.fixture(scope="module")
def _httpx_installed() -> None:
    try:
        import httpx  # type: ignore  # noqa: F401
    except Exception:  # noqa: BLE001
        pytest.skip("httpx 未インストール(`uv sync --extra asr-openai-api` が必要)")


@pytest.mark.large
class TestWhisperApiTranslateRealCall:
    def test_verify_returns_ok(self, api_key, _httpx_installed) -> None:
        from voice_translator.asr.openai_whisper_api_translate_backend import (
            OpenAiWhisperApiTranslateBackend,
        )
        r = OpenAiWhisperApiTranslateBackend.verify_credentials({"api_key": api_key})
        assert r.ok is True

    def test_transcribe_translate_contract(self, api_key, _httpx_installed) -> None:
        from voice_translator.asr.openai_whisper_api_translate_backend import (
            OpenAiWhisperApiTranslateBackend,
        )
        b = OpenAiWhisperApiTranslateBackend(api_key=api_key)
        assert b.get_status() == ModelStatus.LOADED

        src_text, src_lang, tgt_text, tgt_lang = b.transcribe_translate(
            _load_speech_pcm(), src_lang_hint="auto", tgt_lang="en",
        )
        assert src_text == ""            # translations は源文を返さない
        assert isinstance(src_lang, str) and src_lang
        assert isinstance(tgt_text, str)
        assert tgt_lang == "en"          # 英語固定
