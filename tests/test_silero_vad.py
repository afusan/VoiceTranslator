"""SileroVadBackend の単体テスト。silero-vad を完全モック化。"""

from __future__ import annotations

import sys
from unittest.mock import MagicMock

import numpy as np
import pytest

from voice_translator.common.errors import FatalError


@pytest.fixture()
def fake_silero(monkeypatch):
    """silero_vad モジュールをモックに差し替える。"""
    fake_module = MagicMock()
    fake_model = MagicMock(name="silero_model")
    fake_iter = MagicMock(name="vad_iterator")
    # __call__ のデフォルトは None(無音)を返す。テストごとに上書き。
    fake_iter.return_value = None
    fake_iter.reset_states = MagicMock()

    fake_module.load_silero_vad = MagicMock(return_value=fake_model)
    fake_module.VADIterator = MagicMock(return_value=fake_iter)

    monkeypatch.setitem(sys.modules, "silero_vad", fake_module)
    return fake_module, fake_iter


class TestInitialization:
    def test_calls_load_and_iterator(self, fake_silero) -> None:
        fake_module, _ = fake_silero
        from voice_translator.vad.silero_backend import SileroVadBackend

        SileroVadBackend()

        fake_module.load_silero_vad.assert_called_once()
        fake_module.VADIterator.assert_called_once()
        # samplerate=16000 が渡されていること
        assert fake_module.VADIterator.call_args.kwargs["sampling_rate"] == 16000

    def test_default_tuning_values(self, fake_silero) -> None:
        """Step1 で更新したデフォルト値が VADIterator に渡ること。"""
        fake_module, _ = fake_silero
        from voice_translator.vad.silero_backend import SileroVadBackend

        SileroVadBackend()
        kwargs = fake_module.VADIterator.call_args.kwargs
        assert kwargs["min_silence_duration_ms"] == 800  # 既定 500 → 800
        assert kwargs["speech_pad_ms"] == 250            # 既定 100 → 250
        assert kwargs["threshold"] == 0.5                # 維持

    def test_load_failure_raises_fatal(self, monkeypatch) -> None:
        # silero_vad import 時に例外を出す
        fake_module = MagicMock()
        fake_module.load_silero_vad = MagicMock(side_effect=RuntimeError("model dead"))
        fake_module.VADIterator = MagicMock()
        monkeypatch.setitem(sys.modules, "silero_vad", fake_module)
        from voice_translator.vad.silero_backend import SileroVadBackend

        with pytest.raises(FatalError, match="初期化に失敗"):
            SileroVadBackend()


class TestProcessAndBuffer:
    def test_empty_chunk_returns_empty(self, fake_silero) -> None:
        from voice_translator.vad.silero_backend import SileroVadBackend

        backend = SileroVadBackend()
        assert backend.process(np.zeros(0, dtype=np.float32)) == []

    def test_buffer_under_chunk_returns_empty(self, fake_silero) -> None:
        from voice_translator.vad.silero_backend import SileroVadBackend

        backend = SileroVadBackend()
        # 512未満は VAD に渡らない
        result = backend.process(np.zeros(100, dtype=np.float32))
        assert result == []
        _, fake_iter = fake_silero
        fake_iter.assert_not_called()

    def test_silent_window_no_utterance(self, fake_silero) -> None:
        from voice_translator.vad.silero_backend import SileroVadBackend

        _, fake_iter = fake_silero
        fake_iter.return_value = None  # 常に無音
        backend = SileroVadBackend()
        # 512 * 3 サンプル投入
        result = backend.process(np.zeros(512 * 3, dtype=np.float32))
        assert result == []
        assert fake_iter.call_count == 3

    def test_full_speech_cycle_produces_utterance(self, fake_silero) -> None:
        """start イベント → 継続 → end イベントで Utterance が生成される。"""
        from voice_translator.vad.silero_backend import SileroVadBackend

        _, fake_iter = fake_silero
        # 1回目: start, 2回目: None(継続), 3回目: end
        fake_iter.side_effect = [{"start": 0}, None, {"end": 0}]

        backend = SileroVadBackend()
        result = backend.process(np.ones(512 * 3, dtype=np.float32))

        assert len(result) == 1
        utt = result[0]
        # 3ウィンドウ分の連結 = 1536サンプル
        assert utt.pcm.shape == (512 * 3,)
        assert utt.timeline.get("t_capture") is not None
        assert utt.timeline.get("t_vad_end") is not None

    def test_reset_clears_state(self, fake_silero) -> None:
        from voice_translator.vad.silero_backend import SileroVadBackend

        _, fake_iter = fake_silero
        fake_iter.side_effect = [{"start": 0}, None, None]
        backend = SileroVadBackend()
        backend.process(np.ones(512 * 3, dtype=np.float32))
        # この時点で _in_speech=True

        backend.reset()
        fake_iter.reset_states.assert_called()
        assert backend._buffer.size == 0
        assert backend._in_speech is False
