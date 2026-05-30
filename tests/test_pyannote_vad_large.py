"""PyannoteVadBackend の実モデルロード + process 動作確認(large テスト)。

新方針(2026-05-30): token が用意された backend は実物の DL/ロードまで含めて動作確認する。
small テスト(モック)では「依存パッケージのバージョン間の互換」を検出できないため。

本テストは:
- `local.secrets` に `pyannote.hf_token` が無ければ自動 skip
- `pyannote.audio` 未インストール環境(`vad-extra` extras 不選択)でも自動 skip
- それ以外は実モデル DL → backend 構築 → process() に音声を流して期待動作まで検証

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


def _read_hf_token() -> str | None:
    if not SECRETS_PATH.exists():
        return None
    try:
        data = json.loads(SECRETS_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return (data.get("pyannote") or {}).get("hf_token")


def _make_voiced_chunk(duration_sec: float, freq_hz: float = 220.0) -> np.ndarray:
    """サイン波。pyannote の VAD で speech と判定されやすい音圧。"""
    n = int(duration_sec * INTERNAL_SAMPLE_RATE)
    t = np.arange(n, dtype=np.float32) / INTERNAL_SAMPLE_RATE
    return (0.5 * np.sin(2 * np.pi * freq_hz * t)).astype(np.float32)


def _make_silence(duration_sec: float) -> np.ndarray:
    return np.zeros(int(duration_sec * INTERNAL_SAMPLE_RATE), dtype=np.float32)


@pytest.fixture(scope="module")
def hf_token() -> str:
    """HF token が用意されていなければ自動 skip。"""
    token = _read_hf_token()
    if not token:
        pytest.skip("local.secrets に pyannote.hf_token が無いため skip")
    return token


@pytest.fixture(scope="module")
def _pyannote_installed() -> None:
    """pyannote.audio 未インストール(vad-extra 不選択)なら skip。"""
    try:
        import pyannote.audio  # type: ignore  # noqa: F401
    except Exception:  # noqa: BLE001
        pytest.skip("pyannote.audio 未インストール(`uv sync --extra vad-extra` が必要)")


# ============================================================
# large テスト本体
# ============================================================
@pytest.mark.large
class TestPyannoteRealLoad:
    """実 token + 実モデルでのロード + 動作確認。"""

    def test_loads_to_loaded_status(self, hf_token, _pyannote_installed) -> None:
        """HF からモデル DL → LOADED 状態になる。"""
        from voice_translator.vad.pyannote_backend import PyannoteVadBackend

        backend = PyannoteVadBackend(hf_token=hf_token)
        assert backend.get_status() == ModelStatus.LOADED

    def test_process_on_silence_returns_no_segment(
        self, hf_token, _pyannote_installed
    ) -> None:
        """3 秒の無音 → segment 0 件。"""
        from voice_translator.vad.pyannote_backend import PyannoteVadBackend

        backend = PyannoteVadBackend(hf_token=hf_token)
        result = backend.process(_make_silence(3.0))
        assert result == []

    def test_process_on_voiced_signal_returns_at_least_one_segment(
        self, hf_token, _pyannote_installed
    ) -> None:
        """3 秒のサイン波 → 少なくとも 1 件の VadSegment。"""
        from voice_translator.vad.pyannote_backend import PyannoteVadBackend
        from voice_translator.vad.backend import VadSegment

        backend = PyannoteVadBackend(hf_token=hf_token)
        result = backend.process(_make_voiced_chunk(3.0))
        assert len(result) >= 1
        assert all(isinstance(s, VadSegment) for s in result)
        assert all(s.pcm.dtype == np.float32 and s.pcm.size > 0 for s in result)

    def test_reset_clears_buffer_after_real_load(
        self, hf_token, _pyannote_installed
    ) -> None:
        """real backend でも reset() が動く。"""
        from voice_translator.vad.pyannote_backend import PyannoteVadBackend

        backend = PyannoteVadBackend(hf_token=hf_token)
        backend.process(_make_voiced_chunk(1.0))  # batch 未満
        backend.reset()
        assert backend._buffer.size == 0  # type: ignore[attr-defined]
