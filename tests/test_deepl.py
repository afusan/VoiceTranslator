"""DeepLTranslatorBackend の単体テスト。httpx を完全モック化。"""

from __future__ import annotations

import sys
from unittest.mock import MagicMock

import pytest

from voice_translator.common.errors import FatalError, RecoverableError, SkipError
from voice_translator.common.types import ModelStatus


@pytest.fixture()
def fake_httpx(monkeypatch):
    fake = MagicMock(name="httpx_module")
    client = MagicMock(name="httpx_client")
    fake.Client = MagicMock(return_value=client)
    monkeypatch.setitem(sys.modules, "httpx", fake)
    return fake, client


def _resp(status_code: int, json_payload=None, text: str = "") -> MagicMock:
    r = MagicMock()
    r.status_code = status_code
    r.json = MagicMock(return_value=json_payload or {})
    r.text = text
    return r


# ============================================================
class TestInitialization:
    def test_missing_key_sets_missing_status(self) -> None:
        from voice_translator.translator.deepl_backend import DeepLTranslatorBackend
        b = DeepLTranslatorBackend(api_key="")
        assert b.get_status() == ModelStatus.MISSING_CREDENTIALS

    def test_with_key_loads(self, fake_httpx) -> None:
        from voice_translator.translator.deepl_backend import DeepLTranslatorBackend
        b = DeepLTranslatorBackend(api_key="abcd-efgh-ijkl:fx")
        assert b.get_status() == ModelStatus.LOADED

    def test_free_key_uses_free_endpoint(self, fake_httpx) -> None:
        from voice_translator.translator.deepl_backend import (
            DeepLTranslatorBackend,
            _FREE_URL,
        )
        b = DeepLTranslatorBackend(api_key="key:fx")
        assert b._translate_url == _FREE_URL  # type: ignore[attr-defined]

    def test_pro_key_uses_pro_endpoint(self, fake_httpx) -> None:
        from voice_translator.translator.deepl_backend import (
            DeepLTranslatorBackend,
            _PRO_URL,
        )
        b = DeepLTranslatorBackend(api_key="abcd-efgh-ijkl")  # 末尾 :fx 無し
        assert b._translate_url == _PRO_URL  # type: ignore[attr-defined]


# ============================================================
class TestCredentialSpec:
    def test_api_key_field(self) -> None:
        from voice_translator.translator.deepl_backend import DeepLTranslatorBackend
        spec = DeepLTranslatorBackend.credential_spec()
        assert spec[0].key_name == "api_key"


# ============================================================
class TestVerifyCredentials:
    def test_empty_failure(self) -> None:
        from voice_translator.translator.deepl_backend import DeepLTranslatorBackend
        r = DeepLTranslatorBackend.verify_credentials({"api_key": ""})
        assert r.ok is False

    def test_200_ok_free(self, monkeypatch) -> None:
        fake = MagicMock()
        fake.get = MagicMock(return_value=_resp(200))
        monkeypatch.setitem(sys.modules, "httpx", fake)
        from voice_translator.translator.deepl_backend import DeepLTranslatorBackend
        r = DeepLTranslatorBackend.verify_credentials({"api_key": "test:fx"})
        assert r.ok is True
        assert "Free" in r.message

    def test_200_ok_pro(self, monkeypatch) -> None:
        fake = MagicMock()
        fake.get = MagicMock(return_value=_resp(200))
        monkeypatch.setitem(sys.modules, "httpx", fake)
        from voice_translator.translator.deepl_backend import DeepLTranslatorBackend
        r = DeepLTranslatorBackend.verify_credentials({"api_key": "test-pro"})
        assert r.ok is True
        assert "Pro" in r.message

    def test_401_invalid(self, monkeypatch) -> None:
        fake = MagicMock()
        fake.get = MagicMock(return_value=_resp(401))
        monkeypatch.setitem(sys.modules, "httpx", fake)
        from voice_translator.translator.deepl_backend import DeepLTranslatorBackend
        r = DeepLTranslatorBackend.verify_credentials({"api_key": "bad:fx"})
        assert r.ok is False

    def test_456_quota(self, monkeypatch) -> None:
        fake = MagicMock()
        fake.get = MagicMock(return_value=_resp(456))
        monkeypatch.setitem(sys.modules, "httpx", fake)
        from voice_translator.translator.deepl_backend import DeepLTranslatorBackend
        r = DeepLTranslatorBackend.verify_credentials({"api_key": "test:fx"})
        assert r.ok is False
        assert "クォータ" in r.message

    def test_network_error(self, monkeypatch) -> None:
        fake = MagicMock()
        fake.get = MagicMock(side_effect=RuntimeError("net"))
        monkeypatch.setitem(sys.modules, "httpx", fake)
        from voice_translator.translator.deepl_backend import DeepLTranslatorBackend
        r = DeepLTranslatorBackend.verify_credentials({"api_key": "test:fx"})
        assert r.ok is False


# ============================================================
class TestTranslate:
    def test_empty_text_returns_empty(self, fake_httpx) -> None:
        from voice_translator.translator.deepl_backend import DeepLTranslatorBackend
        b = DeepLTranslatorBackend(api_key="test:fx")
        assert b.translate("", "en", "ja") == ""
        assert b.translate("   ", "en", "ja") == ""

    def test_success(self, fake_httpx) -> None:
        _, client = fake_httpx
        client.post = MagicMock(return_value=_resp(
            200, {"translations": [{"text": "こんにちは"}]},
        ))
        from voice_translator.translator.deepl_backend import DeepLTranslatorBackend
        b = DeepLTranslatorBackend(api_key="test:fx")
        result = b.translate("hello", "en", "ja")
        assert result == "こんにちは"
        # ペイロード確認
        data = client.post.call_args.kwargs["data"]
        assert data["text"] == "hello"
        assert data["target_lang"] == "JA"
        assert data["source_lang"] == "EN"

    def test_auto_src_omits_source_lang(self, fake_httpx) -> None:
        _, client = fake_httpx
        client.post = MagicMock(return_value=_resp(
            200, {"translations": [{"text": "text"}]},
        ))
        from voice_translator.translator.deepl_backend import DeepLTranslatorBackend
        b = DeepLTranslatorBackend(api_key="test:fx")
        b.translate("hi", "auto", "ja")
        data = client.post.call_args.kwargs["data"]
        assert "source_lang" not in data

    def test_401_fatal(self, fake_httpx) -> None:
        _, client = fake_httpx
        client.post = MagicMock(return_value=_resp(401, text="bad"))
        from voice_translator.translator.deepl_backend import DeepLTranslatorBackend
        b = DeepLTranslatorBackend(api_key="bad:fx")
        with pytest.raises(FatalError):
            b.translate("hi", "en", "ja")

    def test_456_fatal_quota(self, fake_httpx) -> None:
        _, client = fake_httpx
        client.post = MagicMock(return_value=_resp(456))
        from voice_translator.translator.deepl_backend import DeepLTranslatorBackend
        b = DeepLTranslatorBackend(api_key="test:fx")
        with pytest.raises(FatalError, match="クォータ"):
            b.translate("hi", "en", "ja")

    def test_429_recoverable(self, fake_httpx) -> None:
        _, client = fake_httpx
        client.post = MagicMock(return_value=_resp(429))
        from voice_translator.translator.deepl_backend import DeepLTranslatorBackend
        b = DeepLTranslatorBackend(api_key="test:fx")
        with pytest.raises(RecoverableError):
            b.translate("hi", "en", "ja")

    def test_network_error_recoverable(self, fake_httpx) -> None:
        _, client = fake_httpx
        client.post = MagicMock(side_effect=RuntimeError("conn reset"))
        from voice_translator.translator.deepl_backend import DeepLTranslatorBackend
        b = DeepLTranslatorBackend(api_key="test:fx")
        with pytest.raises(RecoverableError):
            b.translate("hi", "en", "ja")

    def test_empty_response_raises_skip(self, fake_httpx) -> None:
        _, client = fake_httpx
        client.post = MagicMock(return_value=_resp(200, {"translations": []}))
        from voice_translator.translator.deepl_backend import DeepLTranslatorBackend
        b = DeepLTranslatorBackend(api_key="test:fx")
        with pytest.raises(SkipError):
            b.translate("hi", "en", "ja")


# ============================================================
class TestCapabilities:
    def test_cloud_and_requires_credentials(self, fake_httpx) -> None:
        from voice_translator.translator.deepl_backend import DeepLTranslatorBackend
        b = DeepLTranslatorBackend(api_key="test:fx")
        caps = b.capabilities()
        assert caps.is_cloud is True
        assert caps.requires_credentials is True


# ============================================================
class TestSupportedTargetLanguages:
    def test_includes_majors(self) -> None:
        from voice_translator.translator.deepl_backend import DeepLTranslatorBackend
        langs = DeepLTranslatorBackend.supported_target_languages()
        for code in ["en", "ja", "zh", "de", "fr"]:
            assert code in langs

    def test_all_codes_in_common_language_table(self) -> None:
        from voice_translator.common.languages import LANGUAGE_NAMES
        from voice_translator.translator.deepl_backend import DeepLTranslatorBackend
        langs = DeepLTranslatorBackend.supported_target_languages()
        unknown = [c for c in langs if c not in LANGUAGE_NAMES]
        assert not unknown, f"未登録: {unknown}"
