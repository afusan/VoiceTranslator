"""DeepgramAsrBackend の実 API 動作確認(large テスト)。

新方針(2026-05-30): token が用意された backend は実物の DL/ロード/疎通まで含めて動作確認する。

本テストは:
- `local.secrets` に `deepgram.api_key` が無ければ自動 skip
- `deepgram-sdk` 未インストール環境(`asr-deepgram` extras 不選択)なら自動 skip
- それ以外は実 API で verify → backend 構築 → transcribe で 1 件結果を確認

CI には載せない(`@pytest.mark.large`)。手元で 1 回は通してから commit する。
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from voice_translator.common.types import INTERNAL_SAMPLE_RATE, ModelStatus


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SECRETS_PATH = PROJECT_ROOT / "local.secrets"


def _read_api_key() -> str | None:
    if not SECRETS_PATH.exists():
        return None
    try:
        data = json.loads(SECRETS_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return (data.get("deepgram") or {}).get("api_key")


def _make_silence(duration_sec: float) -> np.ndarray:
    return np.zeros(int(duration_sec * INTERNAL_SAMPLE_RATE), dtype=np.float32)


@pytest.fixture(scope="module")
def api_key() -> str:
    key = _read_api_key()
    if not key:
        pytest.skip("local.secrets に deepgram.api_key が無いため skip")
    return key


@pytest.fixture(scope="module")
def _deepgram_installed() -> None:
    try:
        import deepgram  # type: ignore  # noqa: F401
    except Exception:  # noqa: BLE001
        pytest.skip("deepgram-sdk 未インストール(`uv sync --extra asr-deepgram` が必要)")


@pytest.mark.large
class TestDeepgramRealCall:
    def test_verify_credentials_returns_ok(self, api_key, _deepgram_installed) -> None:
        from voice_translator.asr.deepgram_backend import DeepgramAsrBackend
        result = DeepgramAsrBackend.verify_credentials({"api_key": api_key})
        assert result.ok is True

    def test_backend_loads(self, api_key, _deepgram_installed) -> None:
        from voice_translator.asr.deepgram_backend import DeepgramAsrBackend
        backend = DeepgramAsrBackend(api_key=api_key)
        assert backend.get_status() == ModelStatus.LOADED

    def test_transcribe_returns_text(self, api_key, _deepgram_installed) -> None:
        from voice_translator.asr.deepgram_backend import DeepgramAsrBackend
        backend = DeepgramAsrBackend(api_key=api_key)
        text, lang = backend.transcribe(_make_silence(1.0), src_lang_hint="en")
        assert isinstance(text, str)
        assert lang == "en"
