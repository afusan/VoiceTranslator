"""FasterWhisperAsrBackend の単体テスト。faster-whisper を完全モック化。

R-2 でプリミティブ I/F に変更: transcribe(pcm, hint) -> (text, lang)。
"""

from __future__ import annotations

import sys
from unittest.mock import MagicMock

import numpy as np
import pytest

from voice_translator.common.errors import FatalError, SkipError


@pytest.fixture()
def fake_faster_whisper(monkeypatch):
    """faster_whisper.WhisperModel をモックに差し替える。"""
    fake_module = MagicMock()
    fake_model = MagicMock(name="whisper_model")

    # transcribe の戻り値: (segments_iter, info)
    fake_segment = MagicMock()
    fake_segment.text = "  hello world  "
    fake_info = MagicMock()
    fake_info.language = "en"

    fake_model.transcribe = MagicMock(return_value=(iter([fake_segment]), fake_info))
    fake_module.WhisperModel = MagicMock(return_value=fake_model)

    monkeypatch.setitem(sys.modules, "faster_whisper", fake_module)
    return fake_module, fake_model


class TestInitialization:
    def test_calls_whisper_model_with_size(self, fake_faster_whisper) -> None:
        fake_module, _ = fake_faster_whisper
        from voice_translator.asr.faster_whisper_backend import (
            FasterWhisperAsrBackend,
        )

        FasterWhisperAsrBackend(model_size="tiny", device="cpu", compute_type="int8")

        fake_module.WhisperModel.assert_called_once_with(
            "tiny", device="cpu", compute_type="int8"
        )

    def test_init_failure_raises_fatal(self, monkeypatch) -> None:
        fake_module = MagicMock()
        fake_module.WhisperModel = MagicMock(side_effect=OSError("no model"))
        monkeypatch.setitem(sys.modules, "faster_whisper", fake_module)
        from voice_translator.asr.faster_whisper_backend import (
            FasterWhisperAsrBackend,
        )

        with pytest.raises(FatalError, match="初期化に失敗"):
            FasterWhisperAsrBackend()


class TestTranscribe:
    def test_empty_pcm_raises_skip(self, fake_faster_whisper) -> None:
        from voice_translator.asr.faster_whisper_backend import (
            FasterWhisperAsrBackend,
        )

        backend = FasterWhisperAsrBackend()
        with pytest.raises(SkipError):
            backend.transcribe(np.zeros(0, dtype=np.float32))

    def test_none_pcm_raises_skip(self, fake_faster_whisper) -> None:
        from voice_translator.asr.faster_whisper_backend import (
            FasterWhisperAsrBackend,
        )

        backend = FasterWhisperAsrBackend()
        with pytest.raises(SkipError):
            backend.transcribe(None)

    def test_transcribe_returns_text_and_lang(self, fake_faster_whisper) -> None:
        from voice_translator.asr.faster_whisper_backend import (
            FasterWhisperAsrBackend,
        )

        backend = FasterWhisperAsrBackend()
        text, lang = backend.transcribe(np.ones(16000, dtype=np.float32), "auto")
        assert text == "hello world"
        assert lang == "en"  # 自動検出を採用

    def test_explicit_lang_passed_to_model(self, fake_faster_whisper) -> None:
        _, fake_model = fake_faster_whisper
        from voice_translator.asr.faster_whisper_backend import (
            FasterWhisperAsrBackend,
        )

        backend = FasterWhisperAsrBackend()
        text, lang = backend.transcribe(np.ones(160, dtype=np.float32), "en")
        kwargs = fake_model.transcribe.call_args.kwargs
        assert kwargs["language"] == "en"
        assert kwargs["task"] == "transcribe"
        # 明示指定があれば検出結果ではなく指定を返す
        assert lang == "en"

    def test_auto_lang_passes_none(self, fake_faster_whisper) -> None:
        _, fake_model = fake_faster_whisper
        from voice_translator.asr.faster_whisper_backend import (
            FasterWhisperAsrBackend,
        )

        backend = FasterWhisperAsrBackend()
        backend.transcribe(np.ones(160, dtype=np.float32), "auto")
        assert fake_model.transcribe.call_args.kwargs["language"] is None

    def test_inference_exception_wrapped_fatal(self, fake_faster_whisper) -> None:
        _, fake_model = fake_faster_whisper
        fake_model.transcribe = MagicMock(side_effect=RuntimeError("oom"))
        from voice_translator.asr.faster_whisper_backend import (
            FasterWhisperAsrBackend,
        )

        backend = FasterWhisperAsrBackend()
        with pytest.raises(FatalError, match="推論失敗"):
            backend.transcribe(np.ones(160, dtype=np.float32))
