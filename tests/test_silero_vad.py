"""SileroVadBackend の単体テスト。silero-vad を完全モック化。

R-3 で VadSegment 返却に変更。
"""

from __future__ import annotations

import sys
from unittest.mock import MagicMock

import numpy as np
import pytest

from voice_translator.common.errors import FatalError
from voice_translator.vad.backend import VadSegment


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

    def test_full_speech_cycle_produces_segment(self, fake_silero) -> None:
        """start イベント → 継続 → end イベントで VadSegment が生成される。"""
        from voice_translator.vad.silero_backend import SileroVadBackend

        _, fake_iter = fake_silero
        # 1回目: start, 2回目: None(継続), 3回目: end
        fake_iter.side_effect = [{"start": 0}, None, {"end": 0}]

        backend = SileroVadBackend()
        result = backend.process(np.ones(512 * 3, dtype=np.float32))

        assert len(result) == 1
        seg = result[0]
        assert isinstance(seg, VadSegment)
        # 3ウィンドウ分の連結 = 1536サンプル
        assert seg.pcm.shape == (512 * 3,)
        assert isinstance(seg.started_at_monotonic, float)
        assert seg.started_at_monotonic > 0

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
        assert backend._speech_accumulated_samples == 0


# ============================================================
# max_speech_sec(発話最大長の強制区切り)
# ============================================================
class TestMaxSpeechCutoff:
    """発話が長く続いた場合に N 秒で強制的に区切られることを検証。

    16kHz / 512サンプル/ウィンドウ なので、max_speech_sec=0.064 = 1024 サンプル =
    2ウィンドウぶんで強制区切りが起こる(start ウィンドウ + 継続 1 ウィンドウ で 2 達成)。
    """

    def test_force_cut_on_continuous_speech_without_end(self, fake_silero) -> None:
        """end イベントなしでも max_speech_sec を超えたら segment が emit される。"""
        from voice_translator.vad.silero_backend import SileroVadBackend

        _, fake_iter = fake_silero
        # start → None ×5 (end は来ない)。1024 サンプルごとに強制区切り。
        fake_iter.side_effect = [
            {"start": 0}, None, None, None, None, None,
        ]
        backend = SileroVadBackend(max_speech_sec=1024 / 16000)  # = 0.064 秒
        result = backend.process(np.ones(512 * 6, dtype=np.float32))

        # 6 ウィンドウで強制区切りが 3 回(2/4/6 番目で発火)
        assert len(result) == 3
        for seg in result:
            assert isinstance(seg, VadSegment)
            assert seg.pcm.shape == (1024,)  # 2 ウィンドウぶん
        # 発話継続中扱いで残っている(in_speech=True、accumulated=0 にリセット済み)
        assert backend._in_speech is True
        assert backend._speech_accumulated_samples == 0

    def test_force_cut_then_natural_end(self, fake_silero) -> None:
        """強制区切りした後でも、後続の end イベントは正しく処理される。"""
        from voice_translator.vad.silero_backend import SileroVadBackend

        _, fake_iter = fake_silero
        # start → None → (ここで強制区切り) → None → end → (None × 1 だが終わってる)
        fake_iter.side_effect = [
            {"start": 0}, None, None, {"end": 0},
        ]
        backend = SileroVadBackend(max_speech_sec=1024 / 16000)
        result = backend.process(np.ones(512 * 4, dtype=np.float32))

        # 強制区切り1件 + 自然 end 1件 = 2 セグメント
        assert len(result) == 2
        # 自然 end 後は _in_speech=False に戻っている
        assert backend._in_speech is False
        assert backend._speech_accumulated_samples == 0

    def test_zero_disables_force_cut(self, fake_silero) -> None:
        """max_speech_sec=0 で従来挙動(end が来るまで延々と蓄積)に戻る。"""
        from voice_translator.vad.silero_backend import SileroVadBackend

        _, fake_iter = fake_silero
        # start → None × 10(全部蓄積される。end が来ないので emit なし)
        fake_iter.side_effect = [{"start": 0}] + [None] * 10
        backend = SileroVadBackend(max_speech_sec=0)
        result = backend.process(np.ones(512 * 11, dtype=np.float32))

        assert result == []  # 強制区切りが無効なので何も出ない
        assert backend._in_speech is True
        # 11 ウィンドウぶん蓄積されている
        assert backend._speech_accumulated_samples == 512 * 11

    def test_negative_disables_force_cut(self, fake_silero) -> None:
        """max_speech_sec<0 でも無効化される。"""
        from voice_translator.vad.silero_backend import SileroVadBackend

        _, fake_iter = fake_silero
        fake_iter.side_effect = [{"start": 0}] + [None] * 5
        backend = SileroVadBackend(max_speech_sec=-1.0)
        result = backend.process(np.ones(512 * 6, dtype=np.float32))

        assert result == []  # 強制区切りなし

    def test_default_8_seconds_does_not_cut_short_speech(self, fake_silero) -> None:
        """既定 8 秒の上限では、短い発話は強制区切りされず素通り。"""
        from voice_translator.vad.silero_backend import SileroVadBackend

        _, fake_iter = fake_silero
        # 1 秒分(16000 サンプル = 31 ウィンドウ + 余り)→ 8 秒には届かない
        fake_iter.side_effect = [{"start": 0}] + [None] * 31
        backend = SileroVadBackend()  # 既定 max_speech_sec=8.0
        result = backend.process(np.ones(512 * 32, dtype=np.float32))

        # まだ強制区切り発火しない
        assert result == []
        assert backend._in_speech is True
