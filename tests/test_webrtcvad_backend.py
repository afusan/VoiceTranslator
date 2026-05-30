"""WebRtcVadBackend の単体テスト。webrtcvad モジュールを完全モック化。

ヒステリシス検出ロジック(連続 N フレーム speech → 発話開始、連続 M フレーム silence
→ 発話終了)と、max_speech_sec 強制区切り、reset の動作を検証する。
"""

from __future__ import annotations

import sys
from unittest.mock import MagicMock

import numpy as np
import pytest

from voice_translator.common.errors import FatalError
from voice_translator.common.types import ModelStatus
from voice_translator.vad.backend import VadSegment


@pytest.fixture()
def fake_webrtcvad(monkeypatch):
    """`webrtcvad` モジュールをモック差し替え。

    `Vad(aggressiveness)` インスタンスの `is_speech(buf, sr)` を制御できるようにする。
    """
    fake_module = MagicMock(name="webrtcvad_module")
    fake_vad_inst = MagicMock(name="vad_inst")
    fake_vad_inst.is_speech = MagicMock(return_value=False)
    fake_module.Vad = MagicMock(return_value=fake_vad_inst)
    monkeypatch.setitem(sys.modules, "webrtcvad", fake_module)
    return fake_module, fake_vad_inst


# ============================================================
# 初期化
# ============================================================
class TestInitialization:
    def test_calls_vad_constructor_with_aggressiveness(self, fake_webrtcvad) -> None:
        from voice_translator.vad.webrtc_backend import WebRtcVadBackend

        fake_module, _ = fake_webrtcvad
        WebRtcVadBackend(aggressiveness=2)
        fake_module.Vad.assert_called_once_with(2)

    def test_status_is_loaded_after_init(self, fake_webrtcvad) -> None:
        from voice_translator.vad.webrtc_backend import WebRtcVadBackend

        backend = WebRtcVadBackend()
        assert backend.get_status() == ModelStatus.LOADED

    def test_invalid_frame_ms_raises(self, fake_webrtcvad) -> None:
        from voice_translator.vad.webrtc_backend import WebRtcVadBackend

        with pytest.raises(FatalError, match="frame_ms"):
            WebRtcVadBackend(frame_ms=25)

    def test_invalid_aggressiveness_raises(self, fake_webrtcvad) -> None:
        from voice_translator.vad.webrtc_backend import WebRtcVadBackend

        with pytest.raises(FatalError, match="aggressiveness"):
            WebRtcVadBackend(aggressiveness=5)

    def test_import_failure_raises_fatal(self, monkeypatch) -> None:
        """webrtcvad が import できない → FatalError(`vad-extra` ヒント付き)。"""
        # `import webrtcvad` が失敗するように、モジュール側を None 化
        monkeypatch.setitem(sys.modules, "webrtcvad", None)
        from voice_translator.vad.webrtc_backend import WebRtcVadBackend

        with pytest.raises(FatalError, match="vad-extra"):
            WebRtcVadBackend()


# ============================================================
# フレーム判定
# ============================================================
class TestFrameDetection:
    def test_empty_chunk_returns_empty(self, fake_webrtcvad) -> None:
        from voice_translator.vad.webrtc_backend import WebRtcVadBackend

        backend = WebRtcVadBackend()
        assert backend.process(np.zeros(0, dtype=np.float32)) == []

    def test_short_buffer_does_not_call_vad(self, fake_webrtcvad) -> None:
        """frame_samples 未満は webrtcvad に渡らない(持ち越し)。"""
        from voice_translator.vad.webrtc_backend import WebRtcVadBackend

        _, fake_inst = fake_webrtcvad
        backend = WebRtcVadBackend(frame_ms=30)  # 480 サンプル/フレーム
        backend.process(np.zeros(100, dtype=np.float32))
        fake_inst.is_speech.assert_not_called()

    def test_silence_stream_produces_no_segments(self, fake_webrtcvad) -> None:
        from voice_translator.vad.webrtc_backend import WebRtcVadBackend

        _, fake_inst = fake_webrtcvad
        fake_inst.is_speech.return_value = False
        backend = WebRtcVadBackend(frame_ms=30)
        result = backend.process(np.zeros(480 * 10, dtype=np.float32))
        assert result == []

    def test_hysteresis_speech_to_silence_emits_segment(self, fake_webrtcvad) -> None:
        """連続 speech フレームで発話開始 → 連続 silence フレームで発話終了 → emit。

        既定 min_speech_ms=60 / frame_ms=30 → 2 連続 speech で開始。
        min_silence_ms=500 / frame_ms=30 → ceil(500/30)=17 連続 silence で終了。
        """
        from voice_translator.vad.webrtc_backend import WebRtcVadBackend

        _, fake_inst = fake_webrtcvad
        # speech 5 フレーム → silence 17 フレーム → silence 1 フレーム
        fake_inst.is_speech.side_effect = [True] * 5 + [False] * 18
        backend = WebRtcVadBackend(
            frame_ms=30,
            min_speech_ms=60,
            min_silence_ms=500,
            speech_pad_ms=0,  # ring 余白なしで単純化
            max_speech_sec=0,
        )
        result = backend.process(np.zeros(480 * 23, dtype=np.float32))
        assert len(result) == 1
        assert isinstance(result[0], VadSegment)
        # speech 5 フレーム + (発話中の silence) ぶんが含まれる
        assert result[0].pcm.size > 0

    def test_isolated_speech_below_min_does_not_trigger(
        self, fake_webrtcvad
    ) -> None:
        """min_speech_ms 未満の speech フレームでは発話開始しない(ノイズ除去)。"""
        from voice_translator.vad.webrtc_backend import WebRtcVadBackend

        _, fake_inst = fake_webrtcvad
        # 1 フレームだけ speech、残りは silence
        fake_inst.is_speech.side_effect = [True] + [False] * 10
        backend = WebRtcVadBackend(
            frame_ms=30,
            min_speech_ms=60,  # 2 フレーム必要
            min_silence_ms=500,
            speech_pad_ms=0,
            max_speech_sec=0,
        )
        result = backend.process(np.zeros(480 * 11, dtype=np.float32))
        assert result == []


# ============================================================
# max_speech_sec 強制区切り
# ============================================================
class TestMaxSpeechCutoff:
    def test_force_cut_on_long_speech(self, fake_webrtcvad) -> None:
        from voice_translator.vad.webrtc_backend import WebRtcVadBackend

        _, fake_inst = fake_webrtcvad
        fake_inst.is_speech.return_value = True
        # 4 フレーム = 480*4 = 1920 サンプル、max=1024 → 強制区切り発火
        backend = WebRtcVadBackend(
            frame_ms=30,
            min_speech_ms=30,  # 1 フレームで発話開始
            min_silence_ms=500,
            speech_pad_ms=0,
            max_speech_sec=1024 / 16000,
        )
        result = backend.process(np.ones(480 * 4, dtype=np.float32))
        # 4 フレーム = 1920 サンプル、max 1024 → 少なくとも 1 件の強制区切り
        assert len(result) >= 1
        for seg in result:
            assert seg.pcm.size > 0


# ============================================================
# reset
# ============================================================
class TestReset:
    def test_reset_clears_state(self, fake_webrtcvad) -> None:
        from voice_translator.vad.webrtc_backend import WebRtcVadBackend

        _, fake_inst = fake_webrtcvad
        fake_inst.is_speech.return_value = True
        backend = WebRtcVadBackend(
            frame_ms=30, min_speech_ms=30, min_silence_ms=500,
            speech_pad_ms=0, max_speech_sec=0,
        )
        backend.process(np.ones(480 * 3, dtype=np.float32))  # speech 中
        backend.reset()
        assert backend._buffer.size == 0
        assert backend._in_speech is False
        assert backend._speech_accumulated_samples == 0
