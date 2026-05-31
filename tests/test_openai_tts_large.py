"""OpenAiTtsBackend の large テスト(実 OpenAI API 呼び出し)。

実行条件:
- `local.secrets` に `openai_tts.api_key`(実 key)が保存されている
- `uv sync --extra tts-openai-api` 済み
- ネットワーク接続あり

skip 条件: key 未保存 / httpx 未インストール / key が placeholder("xxxxx")。
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import numpy as np
import pytest


_HTTPX_AVAILABLE = importlib.util.find_spec("httpx") is not None


def _load_real_key() -> str | None:
    """local.secrets から openai_tts.api_key を読み出す(無ければ None)。"""
    sec_path = Path(__file__).resolve().parents[1] / "local.secrets"
    if not sec_path.exists():
        return None
    try:
        data = json.loads(sec_path.read_text(encoding="utf-8"))
    except Exception:
        return None
    val = data.get("openai_tts", {}).get("api_key", "")
    if not val or val == "xxxxx":
        return None
    return val


_API_KEY = _load_real_key()


@pytest.mark.large
@pytest.mark.skipif(
    not _HTTPX_AVAILABLE,
    reason="httpx 未インストール(`uv sync --extra tts-openai-api`)",
)
@pytest.mark.skipif(
    _API_KEY is None,
    reason="openai_tts.api_key が local.secrets に未設定(placeholder のみ)",
)
class TestOpenAITtsReal:
    def test_verify_real_key_ok(self) -> None:
        from voice_translator.tts.openai_tts_backend import OpenAiTtsBackend

        result = OpenAiTtsBackend.verify_credentials({"api_key": _API_KEY})
        assert result.ok, f"verify 失敗: {result.message}"

    def test_synthesize_returns_pcm(self) -> None:
        from voice_translator.tts.openai_tts_backend import OpenAiTtsBackend

        backend = OpenAiTtsBackend(api_key=_API_KEY)
        pcm, sr = backend.synthesize("Hello from OpenAI TTS.", "en")
        assert isinstance(pcm, np.ndarray)
        assert pcm.dtype == np.float32
        assert pcm.size > 0
        assert sr == 24000
