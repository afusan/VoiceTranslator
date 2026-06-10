"""FasterWhisperTranslateBackend(ASR+翻訳複合)の実モデル動作確認(large テスト)。

実物の faster-whisper(tiny)で task=translate が完走し、複合の契約
(src_text 空 / tgt_lang="en" / テキスト型)が守られることを検証する。

CI には載せない(`@pytest.mark.large`)。手元で 1 回は通してから commit する。
"""

from __future__ import annotations

import numpy as np
import pytest

from voice_translator.common.types import INTERNAL_SAMPLE_RATE, LayerKind, ModelStatus


def _make_voiced_chunk(duration_sec: float, freq_hz: float = 220.0) -> np.ndarray:
    n = int(duration_sec * INTERNAL_SAMPLE_RATE)
    t = np.arange(n, dtype=np.float32) / INTERNAL_SAMPLE_RATE
    return (0.3 * np.sin(2 * np.pi * freq_hz * t)).astype(np.float32)


@pytest.fixture(scope="module")
def _faster_whisper_installed() -> None:
    try:
        import faster_whisper  # type: ignore  # noqa: F401
    except Exception:  # noqa: BLE001
        pytest.skip("faster-whisper 未インストール")


@pytest.mark.large
class TestFasterWhisperTranslateRealLoad:
    """実モデルでのロード + translate 推論動作確認。"""

    def test_loads_tiny_to_loaded_status(self, _faster_whisper_installed) -> None:
        from voice_translator.asr.faster_whisper_translate_backend import (
            FasterWhisperTranslateBackend,
        )

        backend = FasterWhisperTranslateBackend(model_size="tiny", device="cpu")
        assert backend.get_status() == ModelStatus.LOADED
        # 編成申告: ASR+Translator 複合 / TRANSLATED 産出
        assert backend.covers_roles() == (LayerKind.ASR, LayerKind.TRANSLATOR)

    def test_transcribe_translate_returns_contract_shape(
        self, _faster_whisper_installed,
    ) -> None:
        """サイン波 2 秒 → 複合契約の 4 タプルが返る(中身は環境依存で問わない)。"""
        from voice_translator.asr.faster_whisper_translate_backend import (
            FasterWhisperTranslateBackend,
        )

        backend = FasterWhisperTranslateBackend(model_size="tiny", device="cpu")
        src_text, src_lang, tgt_text, tgt_lang = backend.transcribe_translate(
            _make_voiced_chunk(2.0), src_lang_hint="auto", tgt_lang="en",
        )
        assert src_text == ""        # Whisper translate は源文を出さない
        assert isinstance(src_lang, str) and src_lang
        assert isinstance(tgt_text, str)
        assert tgt_lang == "en"      # 英語固定
