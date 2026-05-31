"""OpenAiGptTranslatorBackend の実 API 動作確認(large テスト)。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SECRETS_PATH = PROJECT_ROOT / "local.secrets"


def _read_api_key() -> str | None:
    if not SECRETS_PATH.exists():
        return None
    try:
        data = json.loads(SECRETS_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return (data.get("openai_gpt") or {}).get("api_key")


@pytest.fixture(scope="module")
def api_key() -> str:
    key = _read_api_key()
    if not key:
        pytest.skip("local.secrets に openai_gpt.api_key が無いため skip")
    return key


@pytest.fixture(scope="module")
def _httpx_installed() -> None:
    try:
        import httpx  # type: ignore  # noqa: F401
    except Exception:  # noqa: BLE001
        pytest.skip(
            "httpx 未インストール(`uv sync --extra translator-openai-api` が必要)"
        )


@pytest.mark.large
class TestOpenAIGptRealCall:
    def test_verify_returns_ok(self, api_key, _httpx_installed) -> None:
        from voice_translator.translator.openai_gpt_backend import (
            OpenAiGptTranslatorBackend,
        )
        r = OpenAiGptTranslatorBackend.verify_credentials({"api_key": api_key})
        assert r.ok is True

    def test_translate_returns_text(self, api_key, _httpx_installed) -> None:
        from voice_translator.translator.openai_gpt_backend import (
            OpenAiGptTranslatorBackend,
        )
        b = OpenAiGptTranslatorBackend(api_key=api_key)
        result = b.translate("Hello, world.", "en", "ja")
        assert isinstance(result, str)
        assert result
