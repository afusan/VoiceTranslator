"""OpenAiTtsBackend の単体テスト(small)。

httpx を sys.modules で差し替え、HTTP レスポンス別の挙動を検証する。
- 正常 → float32 PCM (24kHz) が返る
- 401/403 → FatalError(認証エラー)
- 429/5xx → RecoverableError(リトライ対象)
- 空テキスト → SkipError
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

    # 既定: 200 OK で int16 PCM 512 サンプル
    pcm_bytes = np.zeros(512, dtype=np.int16).tobytes()
    fake.post = MagicMock(
        return_value=MagicMock(status_code=200, content=pcm_bytes)
    )
    fake.get = MagicMock(return_value=MagicMock(status_code=200))
    monkeypatch.setitem(sys.modules, "httpx", fake)
    return fake


class TestSupportedOutputLanguages:
    def test_returns_whisper_language_set(self) -> None:
        from voice_translator.tts.openai_tts_backend import OpenAiTtsBackend
        langs = OpenAiTtsBackend.supported_output_languages()
        assert "en" in langs
        assert "ja" in langs
        assert "auto" not in langs  # auto は出力言語に意味なし


class TestInitialization:
    def test_no_api_key_sets_missing_credentials(self, fake_httpx) -> None:
        from voice_translator.common.types import ModelStatus
        from voice_translator.tts.openai_tts_backend import OpenAiTtsBackend
        backend = OpenAiTtsBackend(api_key=None)
        assert backend.get_status() == ModelStatus.MISSING_CREDENTIALS

    def test_with_api_key_loaded(self, fake_httpx) -> None:
        from voice_translator.common.types import ModelStatus
        from voice_translator.tts.openai_tts_backend import OpenAiTtsBackend
        backend = OpenAiTtsBackend(api_key="sk-x")
        assert backend.get_status() == ModelStatus.LOADED


class TestSynthesize:
    def test_empty_text_raises_skip(self, fake_httpx) -> None:
        from voice_translator.tts.openai_tts_backend import OpenAiTtsBackend
        backend = OpenAiTtsBackend(api_key="sk-x")
        with pytest.raises(SkipError):
            backend.synthesize("", "en")

    def test_returns_float32_pcm_24khz(self, fake_httpx) -> None:
        from voice_translator.tts.openai_tts_backend import OpenAiTtsBackend
        backend = OpenAiTtsBackend(api_key="sk-x")
        pcm, sr = backend.synthesize("hello", "en")
        assert isinstance(pcm, np.ndarray)
        assert pcm.dtype == np.float32
        assert sr == 24000

    def test_401_raises_fatal(self, fake_httpx) -> None:
        fake_httpx.post.return_value = MagicMock(status_code=401, text="bad")
        from voice_translator.tts.openai_tts_backend import OpenAiTtsBackend
        backend = OpenAiTtsBackend(api_key="bad")
        with pytest.raises(FatalError, match="認証"):
            backend.synthesize("hi", "en")

    def test_429_raises_recoverable(self, fake_httpx) -> None:
        fake_httpx.post.return_value = MagicMock(status_code=429, text="rate limit")
        from voice_translator.tts.openai_tts_backend import OpenAiTtsBackend
        backend = OpenAiTtsBackend(api_key="sk-x")
        with pytest.raises(RecoverableError):
            backend.synthesize("hi", "en")

    def test_500_raises_recoverable(self, fake_httpx) -> None:
        fake_httpx.post.return_value = MagicMock(status_code=503, text="server down")
        from voice_translator.tts.openai_tts_backend import OpenAiTtsBackend
        backend = OpenAiTtsBackend(api_key="sk-x")
        with pytest.raises(RecoverableError):
            backend.synthesize("hi", "en")

    def test_400_other_raises_fatal(self, fake_httpx) -> None:
        fake_httpx.post.return_value = MagicMock(status_code=400, text="invalid voice")
        from voice_translator.tts.openai_tts_backend import OpenAiTtsBackend
        backend = OpenAiTtsBackend(api_key="sk-x", voice="invalid")
        with pytest.raises(FatalError):
            backend.synthesize("hi", "en")

    def test_no_api_key_raises_fatal(self, fake_httpx) -> None:
        from voice_translator.tts.openai_tts_backend import OpenAiTtsBackend
        backend = OpenAiTtsBackend(api_key=None)
        with pytest.raises(FatalError, match="API Key"):
            backend.synthesize("hi", "en")
