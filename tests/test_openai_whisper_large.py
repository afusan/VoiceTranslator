"""OpenAiWhisperAsrBackend の実モデルロード + transcribe 動作確認(large テスト)。

新方針(2026-05-30): 実依存パッケージのバージョン乖離を検出するため、
依存が揃った backend は実物の DL/ロード/推論まで含めて動作確認する。

本テストは:
- `openai-whisper` 未インストール環境(`asr-whisper-official` extras 不選択)では自動 skip
- それ以外は実モデル DL → backend 構築 → transcribe() に短い音声を流して text が返ることを検証

CI には載せない(`@pytest.mark.large`)。手元で 1 回は通してから commit する。
"""

from __future__ import annotations

import numpy as np
import pytest

from voice_translator.common.types import INTERNAL_SAMPLE_RATE, ModelStatus


def _make_silence(duration_sec: float) -> np.ndarray:
    return np.zeros(int(duration_sec * INTERNAL_SAMPLE_RATE), dtype=np.float32)


def _make_voiced_chunk(duration_sec: float, freq_hz: float = 220.0) -> np.ndarray:
    n = int(duration_sec * INTERNAL_SAMPLE_RATE)
    t = np.arange(n, dtype=np.float32) / INTERNAL_SAMPLE_RATE
    return (0.3 * np.sin(2 * np.pi * freq_hz * t)).astype(np.float32)


@pytest.fixture(scope="module")
def _openai_whisper_installed() -> None:
    """openai-whisper 未インストールなら skip。"""
    try:
        import whisper  # type: ignore  # noqa: F401
    except Exception:  # noqa: BLE001
        pytest.skip(
            "openai-whisper 未インストール(`uv sync --extra asr-whisper-official` が必要)"
        )


# ============================================================
# large テスト本体
# ============================================================
@pytest.mark.large
class TestOpenAiWhisperRealLoad:
    """実モデルでのロード + 推論動作確認。"""

    def test_loads_tiny_to_loaded_status(self, _openai_whisper_installed) -> None:
        """tiny を DL → LOADED 状態になる(検証のために最小モデルを使う)。"""
        from voice_translator.asr.openai_whisper_backend import (
            OpenAiWhisperAsrBackend,
        )

        backend = OpenAiWhisperAsrBackend(model_size="tiny", device="cpu")
        assert backend.get_status() == ModelStatus.LOADED

    def test_transcribe_returns_text(self, _openai_whisper_installed) -> None:
        """サイン波 2 秒 → text が返る(中身は問わない、API が通ることだけ確認)。"""
        from voice_translator.asr.openai_whisper_backend import (
            OpenAiWhisperAsrBackend,
        )

        backend = OpenAiWhisperAsrBackend(model_size="tiny", device="cpu")
        text, lang = backend.transcribe(_make_voiced_chunk(2.0), src_lang_hint="auto")
        # text の中身は環境依存(無音/サイン波で何が出るかは不定)
        # ここでは API が例外なく完走し、戻り値の型が正しいことだけを担保する。
        assert isinstance(text, str)
        assert isinstance(lang, str)
        assert lang  # 何らかの言語コードが返る("auto" 含む)

    def test_transcribe_with_language_hint(self, _openai_whisper_installed) -> None:
        """language ヒントを渡したケース。"""
        from voice_translator.asr.openai_whisper_backend import (
            OpenAiWhisperAsrBackend,
        )

        backend = OpenAiWhisperAsrBackend(model_size="tiny", device="cpu")
        text, lang = backend.transcribe(_make_silence(1.0), src_lang_hint="en")
        assert isinstance(text, str)
        assert lang == "en"  # hint がそのまま返る
