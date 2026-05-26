"""SoundcardOutputBackend の単体テスト。"""

from __future__ import annotations

from unittest.mock import MagicMock

import numpy as np
import pytest

from voice_translator.common.errors import FatalError, SkipError
from voice_translator.common.types import INTERNAL_SAMPLE_RATE
from voice_translator.common.utterance import Utterance
from voice_translator.output.soundcard_backend import SoundcardOutputBackend


def _make_fake_speaker(spk_id: str, name: str) -> MagicMock:
    """soundcard の Speaker 相当のモック。player() は context manager を返す。"""
    spk = MagicMock()
    spk.id = spk_id
    spk.name = name
    player_obj = MagicMock()
    player_obj.play = MagicMock()
    cm = MagicMock()
    cm.__enter__ = MagicMock(return_value=player_obj)
    cm.__exit__ = MagicMock(return_value=False)
    spk.player = MagicMock(return_value=cm)
    spk._cm = cm
    spk._player_obj = player_obj
    return spk


def _patch_all_speakers(mocker, speakers: list[MagicMock]) -> None:
    mocker.patch(
        "voice_translator.output.soundcard_backend.sc.all_speakers",
        return_value=speakers,
    )


class TestListDevices:
    def test_lists_speakers(self, mocker) -> None:
        speakers = [
            _make_fake_speaker("spk_a", "Speakers"),
            _make_fake_speaker("spk_b", "Headphones"),
        ]
        _patch_all_speakers(mocker, speakers)
        backend = SoundcardOutputBackend()
        devices = backend.list_devices()
        assert [d.device_id for d in devices] == ["spk_a", "spk_b"]
        assert devices[1].display_name == "Headphones"


class TestStartStop:
    def test_start_holds_speaker(self, mocker) -> None:
        spk = _make_fake_speaker("hp", "Headphones")
        _patch_all_speakers(mocker, [spk])
        backend = SoundcardOutputBackend()
        backend.start("hp")
        backend.stop()  # 例外なし

    def test_start_unknown_raises_fatal(self, mocker) -> None:
        _patch_all_speakers(mocker, [_make_fake_speaker("a", "A")])
        backend = SoundcardOutputBackend()
        with pytest.raises(FatalError, match="見つかりません"):
            backend.start("missing")


class TestPlay:
    def _setup(self, mocker, spk_id: str = "hp"):
        spk = _make_fake_speaker(spk_id, "Headphones")
        _patch_all_speakers(mocker, [spk])
        backend = SoundcardOutputBackend()
        backend.start(spk_id)
        return backend, spk

    def test_play_uses_default_samplerate_when_unset(self, mocker) -> None:
        backend, spk = self._setup(mocker)
        u = Utterance(tts_pcm=np.zeros(1600, dtype=np.float32))
        backend.play(u)
        spk.player.assert_called_once()
        assert spk.player.call_args.kwargs["samplerate"] == INTERNAL_SAMPLE_RATE
        assert spk.player.call_args.kwargs["channels"] == 1
        spk._player_obj.play.assert_called_once()
        backend.stop()

    def test_play_uses_utterance_samplerate(self, mocker) -> None:
        backend, spk = self._setup(mocker)
        u = Utterance(tts_pcm=np.zeros(2205, dtype=np.float32), tts_samplerate=22050)
        backend.play(u)
        assert spk.player.call_args.kwargs["samplerate"] == 22050
        backend.stop()

    def test_play_handles_2d_stereo(self, mocker) -> None:
        backend, spk = self._setup(mocker)
        pcm = np.zeros((1000, 2), dtype=np.float32)
        u = Utterance(tts_pcm=pcm, tts_samplerate=16000)
        backend.play(u)
        assert spk.player.call_args.kwargs["channels"] == 2
        backend.stop()

    def test_empty_pcm_raises_skip(self, mocker) -> None:
        backend, _ = self._setup(mocker)
        u = Utterance(tts_pcm=np.array([], dtype=np.float32))
        with pytest.raises(SkipError):
            backend.play(u)
        backend.stop()

    def test_none_pcm_raises_skip(self, mocker) -> None:
        backend, _ = self._setup(mocker)
        u = Utterance(tts_pcm=None)
        with pytest.raises(SkipError):
            backend.play(u)
        backend.stop()

    def test_non_ndarray_pcm_raises_fatal(self, mocker) -> None:
        backend, _ = self._setup(mocker)
        u = Utterance(tts_pcm=b"raw bytes")
        with pytest.raises(FatalError, match="np.ndarray"):
            backend.play(u)
        backend.stop()

    def test_play_before_start_raises_runtime(self) -> None:
        backend = SoundcardOutputBackend()
        u = Utterance(tts_pcm=np.zeros(10, dtype=np.float32))
        with pytest.raises(RuntimeError):
            backend.play(u)

    def test_player_exception_wrapped_in_fatal(self, mocker) -> None:
        backend, spk = self._setup(mocker)
        spk._player_obj.play.side_effect = OSError("device gone")
        u = Utterance(tts_pcm=np.zeros(10, dtype=np.float32))
        with pytest.raises(FatalError, match="音声再生"):
            backend.play(u)
        backend.stop()
