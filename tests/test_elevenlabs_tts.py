"""ElevenLabsTtsBackend の単体テスト(small)。

httpx を sys.modules で差し替えて検証。OpenAI TTS と同形のエラーマッピング
(401/403=Fatal、429/5xx=Recoverable、422=Fatal)。
"""

from __future__ import annotations

import sys
from unittest.mock import MagicMock

import numpy as np
import pytest

from voice_translator.common.errors import FatalError, RecoverableError, SkipError


@pytest.fixture()
def fake_httpx(monkeypatch):
    fake = MagicMock()
    fake.HTTPError = type("HTTPError", (Exception,), {})

    pcm_bytes = np.zeros(256, dtype=np.int16).tobytes()
    fake.post = MagicMock(
        return_value=MagicMock(status_code=200, content=pcm_bytes)
    )
    fake.get = MagicMock(return_value=MagicMock(status_code=200))
    monkeypatch.setitem(sys.modules, "httpx", fake)
    return fake


class TestSupportedOutputLanguages:
    def test_includes_multilingual_v2_set(self) -> None:
        from voice_translator.tts.elevenlabs_backend import ElevenLabsTtsBackend
        langs = ElevenLabsTtsBackend.supported_output_languages()
        # eleven_multilingual_v2 の代表
        for code in ("en", "ja", "zh", "fr", "de", "ko", "hi"):
            assert code in langs, f"{code} がリストに無い"


class TestInitialization:
    def test_no_api_key_sets_missing_credentials(self, fake_httpx) -> None:
        from voice_translator.common.types import ModelStatus
        from voice_translator.tts.elevenlabs_backend import ElevenLabsTtsBackend
        backend = ElevenLabsTtsBackend(api_key=None)
        assert backend.get_status() == ModelStatus.MISSING_CREDENTIALS

    def test_with_api_key_loaded(self, fake_httpx) -> None:
        from voice_translator.common.types import ModelStatus
        from voice_translator.tts.elevenlabs_backend import ElevenLabsTtsBackend
        backend = ElevenLabsTtsBackend(api_key="xi-x")
        assert backend.get_status() == ModelStatus.LOADED


class TestSynthesize:
    def test_empty_text_raises_skip(self, fake_httpx) -> None:
        from voice_translator.tts.elevenlabs_backend import ElevenLabsTtsBackend
        backend = ElevenLabsTtsBackend(api_key="xi-x")
        with pytest.raises(SkipError):
            backend.synthesize("", "en")

    def test_returns_float32_pcm_16khz(self, fake_httpx) -> None:
        from voice_translator.tts.elevenlabs_backend import ElevenLabsTtsBackend
        backend = ElevenLabsTtsBackend(api_key="xi-x")
        pcm, sr = backend.synthesize("hello", "en")
        assert isinstance(pcm, np.ndarray)
        assert pcm.dtype == np.float32
        assert sr == 16000  # output_format=pcm_16000

    def test_401_raises_fatal(self, fake_httpx) -> None:
        fake_httpx.post.return_value = MagicMock(status_code=401, text="bad")
        from voice_translator.tts.elevenlabs_backend import ElevenLabsTtsBackend
        backend = ElevenLabsTtsBackend(api_key="bad")
        with pytest.raises(FatalError, match="認証"):
            backend.synthesize("hi", "en")

    def test_422_raises_fatal(self, fake_httpx) -> None:
        """voice_id が無効など 422 は Fatal(再試行しても意味なし)。"""
        fake_httpx.post.return_value = MagicMock(
            status_code=422, text="invalid voice_id"
        )
        from voice_translator.tts.elevenlabs_backend import ElevenLabsTtsBackend
        backend = ElevenLabsTtsBackend(api_key="xi-x", voice_id="bad")
        with pytest.raises(FatalError, match="入力エラー"):
            backend.synthesize("hi", "en")

    def test_429_raises_recoverable(self, fake_httpx) -> None:
        fake_httpx.post.return_value = MagicMock(status_code=429, text="rate")
        from voice_translator.tts.elevenlabs_backend import ElevenLabsTtsBackend
        backend = ElevenLabsTtsBackend(api_key="xi-x")
        with pytest.raises(RecoverableError):
            backend.synthesize("hi", "en")

    def test_500_raises_recoverable(self, fake_httpx) -> None:
        fake_httpx.post.return_value = MagicMock(status_code=502, text="bad gateway")
        from voice_translator.tts.elevenlabs_backend import ElevenLabsTtsBackend
        backend = ElevenLabsTtsBackend(api_key="xi-x")
        with pytest.raises(RecoverableError):
            backend.synthesize("hi", "en")

    def test_no_api_key_raises_fatal(self, fake_httpx) -> None:
        from voice_translator.tts.elevenlabs_backend import ElevenLabsTtsBackend
        backend = ElevenLabsTtsBackend(api_key=None)
        with pytest.raises(FatalError, match="API Key"):
            backend.synthesize("hi", "en")
