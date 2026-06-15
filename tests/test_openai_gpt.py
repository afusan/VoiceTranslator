"""OpenAiGptTranslatorBackend の単体テスト。httpx 完全モック化。"""

from __future__ import annotations

import sys
from unittest.mock import MagicMock

import pytest

from voice_translator.common.errors import FatalError, RecoverableError, SkipError
from voice_translator.common.types import ModelStatus


@pytest.fixture()
def fake_httpx(monkeypatch):
    fake = MagicMock()
    client = MagicMock()
    fake.Client = MagicMock(return_value=client)
    monkeypatch.setitem(sys.modules, "httpx", fake)
    return fake, client


def _resp(status: int, json_payload=None, text: str = "") -> MagicMock:
    r = MagicMock()
    r.status_code = status
    r.json = MagicMock(return_value=json_payload or {})
    r.text = text
    return r


def _completion(text: str) -> dict:
    return {"choices": [{"message": {"content": text}}]}


class TestInitialization:
    def test_missing_key_missing_status(self) -> None:
        from voice_translator.translator.openai_gpt_backend import (
            OpenAiGptTranslatorBackend,
        )
        b = OpenAiGptTranslatorBackend(api_key="")
        assert b.get_status() == ModelStatus.MISSING_CREDENTIALS

    def test_with_key_loads(self, fake_httpx) -> None:
        from voice_translator.translator.openai_gpt_backend import (
            OpenAiGptTranslatorBackend,
        )
        b = OpenAiGptTranslatorBackend(api_key="sk-x")
        assert b.get_status() == ModelStatus.LOADED


class TestVerify:
    def test_200_ok(self, monkeypatch) -> None:
        fake = MagicMock()
        fake.get = MagicMock(return_value=_resp(200))
        monkeypatch.setitem(sys.modules, "httpx", fake)
        from voice_translator.translator.openai_gpt_backend import (
            OpenAiGptTranslatorBackend,
        )
        r = OpenAiGptTranslatorBackend.verify_credentials({"api_key": "sk-x"})
        assert r.ok is True

    def test_401_fail(self, monkeypatch) -> None:
        fake = MagicMock()
        fake.get = MagicMock(return_value=_resp(401))
        monkeypatch.setitem(sys.modules, "httpx", fake)
        from voice_translator.translator.openai_gpt_backend import (
            OpenAiGptTranslatorBackend,
        )
        r = OpenAiGptTranslatorBackend.verify_credentials({"api_key": "bad"})
        assert r.ok is False


class TestTranslate:
    def test_empty_returns_empty(self, fake_httpx) -> None:
        from voice_translator.translator.openai_gpt_backend import (
            OpenAiGptTranslatorBackend,
        )
        b = OpenAiGptTranslatorBackend(api_key="sk-x")
        assert b.translate("", "eng", "jpn") == ""

    def test_success(self, fake_httpx) -> None:
        _, client = fake_httpx
        client.post = MagicMock(return_value=_resp(200, _completion("こんにちは")))
        from voice_translator.translator.openai_gpt_backend import (
            OpenAiGptTranslatorBackend,
        )
        b = OpenAiGptTranslatorBackend(api_key="sk-x")
        assert b.translate("hello", "eng", "jpn") == "こんにちは"

    def test_strips_translation_prefix(self, fake_httpx) -> None:
        _, client = fake_httpx
        client.post = MagicMock(return_value=_resp(200, _completion("Translation: こんにちは")))
        from voice_translator.translator.openai_gpt_backend import (
            OpenAiGptTranslatorBackend,
        )
        b = OpenAiGptTranslatorBackend(api_key="sk-x")
        assert b.translate("hello", "eng", "jpn") == "こんにちは"

    def test_strips_japanese_prefix(self, fake_httpx) -> None:
        _, client = fake_httpx
        client.post = MagicMock(return_value=_resp(200, _completion("訳: こんにちは")))
        from voice_translator.translator.openai_gpt_backend import (
            OpenAiGptTranslatorBackend,
        )
        b = OpenAiGptTranslatorBackend(api_key="sk-x")
        assert b.translate("hello", "eng", "jpn") == "こんにちは"

    def test_401_fatal(self, fake_httpx) -> None:
        _, client = fake_httpx
        client.post = MagicMock(return_value=_resp(401))
        from voice_translator.translator.openai_gpt_backend import (
            OpenAiGptTranslatorBackend,
        )
        b = OpenAiGptTranslatorBackend(api_key="bad")
        with pytest.raises(FatalError):
            b.translate("hi", "eng", "jpn")

    def test_429_recoverable(self, fake_httpx) -> None:
        _, client = fake_httpx
        client.post = MagicMock(return_value=_resp(429))
        from voice_translator.translator.openai_gpt_backend import (
            OpenAiGptTranslatorBackend,
        )
        b = OpenAiGptTranslatorBackend(api_key="sk-x")
        with pytest.raises(RecoverableError):
            b.translate("hi", "eng", "jpn")

    def test_network_recoverable(self, fake_httpx) -> None:
        _, client = fake_httpx
        client.post = MagicMock(side_effect=RuntimeError("conn"))
        from voice_translator.translator.openai_gpt_backend import (
            OpenAiGptTranslatorBackend,
        )
        b = OpenAiGptTranslatorBackend(api_key="sk-x")
        with pytest.raises(RecoverableError):
            b.translate("hi", "eng", "jpn")

    def test_empty_response_skip(self, fake_httpx) -> None:
        _, client = fake_httpx
        client.post = MagicMock(return_value=_resp(200, _completion("   ")))
        from voice_translator.translator.openai_gpt_backend import (
            OpenAiGptTranslatorBackend,
        )
        b = OpenAiGptTranslatorBackend(api_key="sk-x")
        with pytest.raises(SkipError):
            b.translate("hi", "eng", "jpn")


class TestSupportedTargetLanguages:
    def test_includes_majors(self) -> None:
        from voice_translator.translator.openai_gpt_backend import (
            OpenAiGptTranslatorBackend,
        )
        langs = OpenAiGptTranslatorBackend.supported_target_languages()
        for code in ["eng", "jpn", "zho", "fra", "deu"]:
            assert code in langs

    def test_all_codes_in_common_language_table(self) -> None:
        from voice_translator.common.languages import LANGUAGE_NAMES
        from voice_translator.translator.openai_gpt_backend import (
            OpenAiGptTranslatorBackend,
        )
        langs = OpenAiGptTranslatorBackend.supported_target_languages()
        unknown = [c for c in langs if c not in LANGUAGE_NAMES]
        assert not unknown, f"未登録: {unknown}"


class TestCapabilities:
    def test_cloud_credentials(self, fake_httpx) -> None:
        from voice_translator.translator.openai_gpt_backend import (
            OpenAiGptTranslatorBackend,
        )
        b = OpenAiGptTranslatorBackend(api_key="sk-x")
        c = b.capabilities()
        assert c.is_cloud is True
        assert c.requires_credentials is True
