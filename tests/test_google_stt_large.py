"""GoogleSttAsrBackend の実 API 動作確認(large テスト)。

新方針(2026-05-30): token が用意された backend は実物の DL/ロード/疎通まで含めて動作確認する。

本テストは:
- `local.secrets` に `google_stt.credentials_path` が無ければ自動 skip
- `google-cloud-speech` 未インストール環境(`asr-google-stt` extras 不選択)なら自動 skip
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


def _read_credentials_path() -> str | None:
    if not SECRETS_PATH.exists():
        return None
    try:
        data = json.loads(SECRETS_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return (data.get("google_stt") or {}).get("credentials_path")


def _make_silence(duration_sec: float) -> np.ndarray:
    return np.zeros(int(duration_sec * INTERNAL_SAMPLE_RATE), dtype=np.float32)


@pytest.fixture(scope="module")
def credentials_path() -> str:
    """credentials_path が用意されていなければ自動 skip。"""
    path = _read_credentials_path()
    if not path or not Path(path).exists():
        pytest.skip("local.secrets に google_stt.credentials_path が無い、またはファイル不在")
    return path


@pytest.fixture(scope="module")
def _google_speech_installed() -> None:
    try:
        from google.cloud import speech  # type: ignore  # noqa: F401
    except Exception:  # noqa: BLE001
        pytest.skip(
            "google-cloud-speech 未インストール(`uv sync --extra asr-google-stt` が必要)"
        )


@pytest.mark.large
class TestGoogleSttRealCall:
    """実 JSON + 実エンドポイントでの疎通 + 推論。"""

    def test_verify_credentials_returns_ok(
        self, credentials_path, _google_speech_installed
    ) -> None:
        from voice_translator.asr.google_stt_backend import GoogleSttAsrBackend
        result = GoogleSttAsrBackend.verify_credentials(
            {"credentials_path": credentials_path}
        )
        assert result.ok is True

    def test_backend_loads(self, credentials_path, _google_speech_installed) -> None:
        from voice_translator.asr.google_stt_backend import GoogleSttAsrBackend
        backend = GoogleSttAsrBackend(credentials_path=credentials_path)
        assert backend.get_status() == ModelStatus.LOADED

    def test_transcribe_returns_text(
        self, credentials_path, _google_speech_installed
    ) -> None:
        """1 秒の無音 → text は空でも API 呼び出しが通れば OK。"""
        from voice_translator.asr.google_stt_backend import GoogleSttAsrBackend
        backend = GoogleSttAsrBackend(credentials_path=credentials_path)
        text, lang = backend.transcribe(_make_silence(1.0), src_lang_hint="en")
        assert isinstance(text, str)
        assert lang == "en"
