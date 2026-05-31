"""OpenAiWhisperApiAsrBackend の単体テスト。httpx を完全モック化。"""

from __future__ import annotations

import sys
from unittest.mock import MagicMock

import numpy as np
import pytest

from voice_translator.common.errors import FatalError, RecoverableError, SkipError
from voice_translator.common.types import ModelStatus


@pytest.fixture()
def fake_httpx(monkeypatch):
    """httpx をモックに差し替える。"""
    fake_module = MagicMock(name="httpx_module")
    fake_client = MagicMock(name="httpx_client")
    # post の戻りは個別テストで設定するので、ここでは MagicMock を用意するだけ
    fake_module.Client = MagicMock(return_value=fake_client)
    monkeypatch.setitem(sys.modules, "httpx", fake_module)
    return fake_module, fake_client


def _make_response(status_code: int, json_payload: dict | None = None, text: str = "") -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    resp.json = MagicMock(return_value=json_payload or {})
    resp.text = text
    return resp


# ============================================================
class TestInitialization:
    def test_missing_api_key_sets_missing_credentials_status(self) -> None:
        from voice_translator.asr.openai_whisper_api_backend import (
            OpenAiWhisperApiAsrBackend,
        )
        backend = OpenAiWhisperApiAsrBackend(api_key="")
        assert backend.get_status() == ModelStatus.MISSING_CREDENTIALS

    def test_with_api_key_loads_successfully(self, fake_httpx) -> None:
        from voice_translator.asr.openai_whisper_api_backend import (
            OpenAiWhisperApiAsrBackend,
        )
        backend = OpenAiWhisperApiAsrBackend(api_key="sk-test")
        assert backend.get_status() == ModelStatus.LOADED


# ============================================================
class TestCredentialSpec:
    def test_returns_api_key_field(self) -> None:
        from voice_translator.asr.openai_whisper_api_backend import (
            OpenAiWhisperApiAsrBackend,
        )
        spec = OpenAiWhisperApiAsrBackend.credential_spec()
        assert len(spec) == 1
        assert spec[0].key_name == "api_key"
        assert spec[0].secret is True


# ============================================================
class TestVerifyCredentials:
    def test_empty_key_returns_failure(self) -> None:
        from voice_translator.asr.openai_whisper_api_backend import (
            OpenAiWhisperApiAsrBackend,
        )
        result = OpenAiWhisperApiAsrBackend.verify_credentials({"api_key": ""})
        assert result.ok is False
        assert "未入力" in result.message

    def test_200_returns_ok(self, monkeypatch) -> None:
        fake_module = MagicMock()
        fake_module.get = MagicMock(return_value=_make_response(200, {}))
        monkeypatch.setitem(sys.modules, "httpx", fake_module)

        from voice_translator.asr.openai_whisper_api_backend import (
            OpenAiWhisperApiAsrBackend,
        )
        result = OpenAiWhisperApiAsrBackend.verify_credentials({"api_key": "sk-test"})
        assert result.ok is True

    def test_401_returns_invalid(self, monkeypatch) -> None:
        fake_module = MagicMock()
        fake_module.get = MagicMock(return_value=_make_response(401))
        monkeypatch.setitem(sys.modules, "httpx", fake_module)

        from voice_translator.asr.openai_whisper_api_backend import (
            OpenAiWhisperApiAsrBackend,
        )
        result = OpenAiWhisperApiAsrBackend.verify_credentials({"api_key": "sk-bad"})
        assert result.ok is False
        assert "無効" in result.message

    def test_429_returns_quota(self, monkeypatch) -> None:
        fake_module = MagicMock()
        fake_module.get = MagicMock(return_value=_make_response(429))
        monkeypatch.setitem(sys.modules, "httpx", fake_module)

        from voice_translator.asr.openai_whisper_api_backend import (
            OpenAiWhisperApiAsrBackend,
        )
        result = OpenAiWhisperApiAsrBackend.verify_credentials({"api_key": "sk-test"})
        assert result.ok is False
        assert "クォータ" in result.message or "残高" in result.message

    def test_network_error_returns_failure_no_exception(self, monkeypatch) -> None:
        fake_module = MagicMock()
        fake_module.get = MagicMock(side_effect=RuntimeError("network down"))
        monkeypatch.setitem(sys.modules, "httpx", fake_module)

        from voice_translator.asr.openai_whisper_api_backend import (
            OpenAiWhisperApiAsrBackend,
        )
        result = OpenAiWhisperApiAsrBackend.verify_credentials({"api_key": "sk-test"})
        assert result.ok is False
        # 例外は呼び出し元に伝播せず、VerifyResult で表現


# ============================================================
class TestTranscribe:
    def test_empty_pcm_raises_skip(self, fake_httpx) -> None:
        from voice_translator.asr.openai_whisper_api_backend import (
            OpenAiWhisperApiAsrBackend,
        )
        backend = OpenAiWhisperApiAsrBackend(api_key="sk-test")
        with pytest.raises(SkipError):
            backend.transcribe(np.zeros(0, dtype=np.float32))

    def test_success_extracts_text_and_language(self, fake_httpx) -> None:
        _, fake_client = fake_httpx
        fake_client.post = MagicMock(return_value=_make_response(
            200, {"text": "  hello  ", "language": "english"}
        ))

        from voice_translator.asr.openai_whisper_api_backend import (
            OpenAiWhisperApiAsrBackend,
        )
        backend = OpenAiWhisperApiAsrBackend(api_key="sk-test")
        text, lang = backend.transcribe(np.zeros(16000, dtype=np.float32), src_lang_hint="auto")
        assert text == "hello"
        assert lang == "en"  # english → en に正規化

    def test_language_hint_omits_language_parameter(self, fake_httpx) -> None:
        _, fake_client = fake_httpx
        fake_client.post = MagicMock(return_value=_make_response(
            200, {"text": "text", "language": "japanese"}
        ))

        from voice_translator.asr.openai_whisper_api_backend import (
            OpenAiWhisperApiAsrBackend,
        )
        backend = OpenAiWhisperApiAsrBackend(api_key="sk-test")
        # auto なら language キーは送らない
        backend.transcribe(np.zeros(16000, dtype=np.float32), src_lang_hint="auto")
        data = fake_client.post.call_args.kwargs["data"]
        assert "language" not in data

    def test_language_hint_passes_iso_code(self, fake_httpx) -> None:
        _, fake_client = fake_httpx
        fake_client.post = MagicMock(return_value=_make_response(
            200, {"text": "text", "language": "japanese"}
        ))

        from voice_translator.asr.openai_whisper_api_backend import (
            OpenAiWhisperApiAsrBackend,
        )
        backend = OpenAiWhisperApiAsrBackend(api_key="sk-test")
        text, lang = backend.transcribe(np.zeros(16000, dtype=np.float32), src_lang_hint="ja")
        data = fake_client.post.call_args.kwargs["data"]
        assert data["language"] == "ja"
        assert lang == "ja"  # hint を尊重

    def test_huge_pcm_raises_fatal(self, fake_httpx) -> None:
        from voice_translator.asr.openai_whisper_api_backend import (
            OpenAiWhisperApiAsrBackend,
        )
        backend = OpenAiWhisperApiAsrBackend(api_key="sk-test")
        # 25MB 超(16kHz int16 = 32KB/秒 → 800秒で 25MB 超え)
        huge_pcm = np.zeros(16000 * 850, dtype=np.float32)
        with pytest.raises(FatalError, match="25MB"):
            backend.transcribe(huge_pcm)

    def test_401_raises_fatal(self, fake_httpx) -> None:
        _, fake_client = fake_httpx
        fake_client.post = MagicMock(return_value=_make_response(401, text="bad key"))

        from voice_translator.asr.openai_whisper_api_backend import (
            OpenAiWhisperApiAsrBackend,
        )
        backend = OpenAiWhisperApiAsrBackend(api_key="sk-bad")
        with pytest.raises(FatalError, match="認証"):
            backend.transcribe(np.zeros(16000, dtype=np.float32))

    def test_429_raises_recoverable(self, fake_httpx) -> None:
        _, fake_client = fake_httpx
        fake_client.post = MagicMock(return_value=_make_response(429))

        from voice_translator.asr.openai_whisper_api_backend import (
            OpenAiWhisperApiAsrBackend,
        )
        backend = OpenAiWhisperApiAsrBackend(api_key="sk-test")
        with pytest.raises(RecoverableError):
            backend.transcribe(np.zeros(16000, dtype=np.float32))

    def test_500_raises_recoverable(self, fake_httpx) -> None:
        _, fake_client = fake_httpx
        fake_client.post = MagicMock(return_value=_make_response(500))

        from voice_translator.asr.openai_whisper_api_backend import (
            OpenAiWhisperApiAsrBackend,
        )
        backend = OpenAiWhisperApiAsrBackend(api_key="sk-test")
        with pytest.raises(RecoverableError):
            backend.transcribe(np.zeros(16000, dtype=np.float32))

    def test_network_error_raises_recoverable(self, fake_httpx) -> None:
        _, fake_client = fake_httpx
        fake_client.post = MagicMock(side_effect=RuntimeError("connection reset"))

        from voice_translator.asr.openai_whisper_api_backend import (
            OpenAiWhisperApiAsrBackend,
        )
        backend = OpenAiWhisperApiAsrBackend(api_key="sk-test")
        with pytest.raises(RecoverableError):
            backend.transcribe(np.zeros(16000, dtype=np.float32))


# ============================================================
class TestCapabilities:
    def test_cloud_and_requires_credentials(self, fake_httpx) -> None:
        from voice_translator.asr.openai_whisper_api_backend import (
            OpenAiWhisperApiAsrBackend,
        )
        backend = OpenAiWhisperApiAsrBackend(api_key="sk-test")
        caps = backend.capabilities()
        assert caps.is_cloud is True
        assert caps.requires_credentials is True
        assert caps.service_name == "OpenAI Whisper API"


# ============================================================
class TestSupportedInputLanguages:
    def test_returns_whisper_99_languages(self) -> None:
        from voice_translator.asr.openai_whisper_api_backend import (
            OpenAiWhisperApiAsrBackend,
        )
        langs = OpenAiWhisperApiAsrBackend.supported_input_languages()
        assert "en" in langs
        assert "ja" in langs
        assert len(langs) >= 90

    def test_supports_auto_detect(self) -> None:
        from voice_translator.asr.openai_whisper_api_backend import (
            OpenAiWhisperApiAsrBackend,
        )
        assert OpenAiWhisperApiAsrBackend.supports_auto_detect() is True
