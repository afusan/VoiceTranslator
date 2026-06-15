"""DeepgramAsrBackend の単体テスト。deepgram-sdk / httpx を完全モック化。"""

from __future__ import annotations

import sys
from unittest.mock import MagicMock

import numpy as np
import pytest

from voice_translator.common.errors import FatalError, RecoverableError, SkipError
from voice_translator.common.types import ModelStatus


@pytest.fixture()
def fake_deepgram(monkeypatch):
    """deepgram-sdk をモック化。yields (fake_module, fake_client, fake_options_cls)。"""
    fake_module = MagicMock(name="deepgram_module")
    fake_client = MagicMock(name="dg_client")
    fake_module.DeepgramClient = MagicMock(return_value=fake_client)
    fake_module.PrerecordedOptions = MagicMock(name="PrerecordedOptions")
    monkeypatch.setitem(sys.modules, "deepgram", fake_module)
    return fake_module, fake_client


def _make_response(text: str, detected_language: str = "") -> MagicMock:
    alt = MagicMock()
    alt.transcript = text
    channel = MagicMock()
    channel.alternatives = [alt]
    channel.detected_language = detected_language
    results = MagicMock()
    results.channels = [channel]
    response = MagicMock()
    response.results = results
    return response


# ============================================================
class TestInitialization:
    def test_missing_api_key_sets_missing_status(self) -> None:
        from voice_translator.asr.deepgram_backend import DeepgramAsrBackend
        backend = DeepgramAsrBackend(api_key="")
        assert backend.get_status() == ModelStatus.MISSING_CREDENTIALS

    def test_with_api_key_loads(self, fake_deepgram) -> None:
        from voice_translator.asr.deepgram_backend import DeepgramAsrBackend
        backend = DeepgramAsrBackend(api_key="dg-test")
        assert backend.get_status() == ModelStatus.LOADED


# ============================================================
class TestCredentialSpec:
    def test_returns_api_key_field(self) -> None:
        from voice_translator.asr.deepgram_backend import DeepgramAsrBackend
        spec = DeepgramAsrBackend.credential_spec()
        assert len(spec) == 1
        assert spec[0].key_name == "api_key"
        assert spec[0].secret is True


# ============================================================
class TestVerifyCredentials:
    def test_empty_key_returns_failure(self) -> None:
        from voice_translator.asr.deepgram_backend import DeepgramAsrBackend
        result = DeepgramAsrBackend.verify_credentials({"api_key": ""})
        assert result.ok is False

    def test_200_returns_ok(self, monkeypatch) -> None:
        fake_httpx = MagicMock()
        fake_httpx.get = MagicMock(return_value=MagicMock(status_code=200))
        monkeypatch.setitem(sys.modules, "httpx", fake_httpx)
        from voice_translator.asr.deepgram_backend import DeepgramAsrBackend
        result = DeepgramAsrBackend.verify_credentials({"api_key": "dg-test"})
        assert result.ok is True

    def test_401_returns_invalid(self, monkeypatch) -> None:
        fake_httpx = MagicMock()
        fake_httpx.get = MagicMock(return_value=MagicMock(status_code=401))
        monkeypatch.setitem(sys.modules, "httpx", fake_httpx)
        from voice_translator.asr.deepgram_backend import DeepgramAsrBackend
        result = DeepgramAsrBackend.verify_credentials({"api_key": "dg-bad"})
        assert result.ok is False

    def test_network_error_returns_failure(self, monkeypatch) -> None:
        fake_httpx = MagicMock()
        fake_httpx.get = MagicMock(side_effect=RuntimeError("connection refused"))
        monkeypatch.setitem(sys.modules, "httpx", fake_httpx)
        from voice_translator.asr.deepgram_backend import DeepgramAsrBackend
        result = DeepgramAsrBackend.verify_credentials({"api_key": "dg-test"})
        assert result.ok is False


# ============================================================
class TestTranscribe:
    def test_empty_pcm_raises_skip(self, fake_deepgram) -> None:
        from voice_translator.asr.deepgram_backend import DeepgramAsrBackend
        backend = DeepgramAsrBackend(api_key="dg-test")
        with pytest.raises(SkipError):
            backend.transcribe(np.zeros(0, dtype=np.float32))

    def test_success_extracts_text(self, fake_deepgram) -> None:
        _, fake_client = fake_deepgram
        v = MagicMock()
        v.transcribe_file = MagicMock(return_value=_make_response("hello", detected_language="en"))
        fake_client.listen.rest.v = MagicMock(return_value=v)

        from voice_translator.asr.deepgram_backend import DeepgramAsrBackend
        backend = DeepgramAsrBackend(api_key="dg-test")
        text, lang = backend.transcribe(np.zeros(16000, dtype=np.float32), src_lang_hint="auto")
        assert text == "hello"
        assert lang == "eng"  # 検出言語(639-1)を正準 639-3 へ持ち上げて返す

    def test_auto_passes_detect_language(self, fake_deepgram) -> None:
        fake_module, fake_client = fake_deepgram
        v = MagicMock()
        v.transcribe_file = MagicMock(return_value=_make_response("text", detected_language="ja"))
        fake_client.listen.rest.v = MagicMock(return_value=v)

        from voice_translator.asr.deepgram_backend import DeepgramAsrBackend
        backend = DeepgramAsrBackend(api_key="dg-test")
        backend.transcribe(np.zeros(16000, dtype=np.float32), src_lang_hint="auto")
        opts_kwargs = fake_module.PrerecordedOptions.call_args.kwargs
        assert opts_kwargs.get("detect_language") is True
        assert "language" not in opts_kwargs

    def test_lang_hint_passes_language(self, fake_deepgram) -> None:
        fake_module, fake_client = fake_deepgram
        v = MagicMock()
        v.transcribe_file = MagicMock(return_value=_make_response("text"))
        fake_client.listen.rest.v = MagicMock(return_value=v)

        from voice_translator.asr.deepgram_backend import DeepgramAsrBackend
        backend = DeepgramAsrBackend(api_key="dg-test")
        text, lang = backend.transcribe(np.zeros(16000, dtype=np.float32), src_lang_hint="jpn")
        opts_kwargs = fake_module.PrerecordedOptions.call_args.kwargs
        assert opts_kwargs.get("language") == "ja"  # Deepgram には 639-1 で渡す
        assert "detect_language" not in opts_kwargs
        assert lang == "jpn"  # hint(正準 639-3)を尊重

    def test_auth_error_raises_fatal(self, fake_deepgram) -> None:
        _, fake_client = fake_deepgram
        class Unauthorized(Exception):
            pass
        v = MagicMock()
        v.transcribe_file = MagicMock(side_effect=Unauthorized("bad key"))
        fake_client.listen.rest.v = MagicMock(return_value=v)

        from voice_translator.asr.deepgram_backend import DeepgramAsrBackend
        backend = DeepgramAsrBackend(api_key="dg-bad")
        with pytest.raises(FatalError):
            backend.transcribe(np.zeros(16000, dtype=np.float32))

    def test_other_error_raises_recoverable(self, fake_deepgram) -> None:
        _, fake_client = fake_deepgram
        class ServiceUnavailable(Exception):
            pass
        v = MagicMock()
        v.transcribe_file = MagicMock(side_effect=ServiceUnavailable("503"))
        fake_client.listen.rest.v = MagicMock(return_value=v)

        from voice_translator.asr.deepgram_backend import DeepgramAsrBackend
        backend = DeepgramAsrBackend(api_key="dg-test")
        with pytest.raises(RecoverableError):
            backend.transcribe(np.zeros(16000, dtype=np.float32))


# ============================================================
class TestCapabilities:
    def test_cloud_and_requires_credentials(self, fake_deepgram) -> None:
        from voice_translator.asr.deepgram_backend import DeepgramAsrBackend
        backend = DeepgramAsrBackend(api_key="dg-test")
        caps = backend.capabilities()
        assert caps.is_cloud is True
        assert caps.requires_credentials is True


# ============================================================
class TestSupportedInputLanguages:
    def test_includes_major_languages(self) -> None:
        from voice_translator.asr.deepgram_backend import DeepgramAsrBackend
        langs = DeepgramAsrBackend.supported_input_languages()
        assert "eng" in langs  # 正準 639-3 で申告
        assert "jpn" in langs

    def test_supports_auto_detect(self) -> None:
        from voice_translator.asr.deepgram_backend import DeepgramAsrBackend
        assert DeepgramAsrBackend.supports_auto_detect() is True
