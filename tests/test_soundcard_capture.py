"""SoundcardCaptureBackend の単体テスト。

soundcard ライブラリの呼び出しをモック化し、実機を使わずに振る舞いを検証する。
"""

from __future__ import annotations

from unittest.mock import MagicMock

import numpy as np
import pytest

from voice_translator.capture.soundcard_backend import (
    SoundcardCaptureBackend,
    _to_internal_format,
)
from voice_translator.common.errors import FatalError
from voice_translator.common.types import (
    INTERNAL_CHANNELS,
    INTERNAL_SAMPLE_RATE,
)


def _make_fake_mic(mic_id: str, name: str, *, is_loopback: bool = False) -> MagicMock:
    """soundcard の Microphone オブジェクト相当のモックを作る。"""
    m = MagicMock()
    m.id = mic_id
    m.name = name
    m.isloopback = is_loopback
    return m


def _patch_all_microphones(mocker, mics: list[MagicMock]) -> None:
    """soundcard.all_microphones の差し替え(モジュール参照に注意)。"""
    mocker.patch(
        "voice_translator.capture.soundcard_backend.sc.all_microphones",
        return_value=mics,
    )


class TestListSources:
    def test_lists_mics_and_loopbacks(self, mocker) -> None:
        mics = [
            _make_fake_mic("mic_1", "Microphone 1"),
            _make_fake_mic("spk_lb_1", "Speakers", is_loopback=True),
        ]
        _patch_all_microphones(mocker, mics)

        backend = SoundcardCaptureBackend()
        sources = backend.list_sources()

        assert len(sources) == 2
        assert sources[0].source_id == "mic_1"
        assert sources[0].is_loopback is False
        assert sources[1].source_id == "spk_lb_1"
        assert sources[1].is_loopback is True
        assert sources[1].display_name.startswith("[LB] ")

    def test_dedupes_by_id(self, mocker) -> None:
        # 同じ id が複数回返ってきても 1 件にまとまる
        mic = _make_fake_mic("dup", "Dup")
        _patch_all_microphones(mocker, [mic, mic])
        backend = SoundcardCaptureBackend()
        assert len(backend.list_sources()) == 1


class TestStartStop:
    def test_start_finds_mic_and_enters_recorder(self, mocker) -> None:
        mic = _make_fake_mic("mic_1", "Mic")
        # recorder() は context manager を返す
        cm = MagicMock()
        cm.__enter__ = MagicMock(return_value=MagicMock(name="recorder_obj"))
        cm.__exit__ = MagicMock(return_value=False)
        mic.recorder = MagicMock(return_value=cm)
        _patch_all_microphones(mocker, [mic])

        backend = SoundcardCaptureBackend(chunk_size=1600)
        backend.start("mic_1")

        mic.recorder.assert_called_once()
        kwargs = mic.recorder.call_args.kwargs
        assert kwargs["samplerate"] == INTERNAL_SAMPLE_RATE
        assert kwargs["channels"] == INTERNAL_CHANNELS
        assert kwargs["blocksize"] == 1600
        backend.stop()
        cm.__exit__.assert_called_once()

    def test_start_unknown_id_raises_fatal(self, mocker) -> None:
        _patch_all_microphones(mocker, [_make_fake_mic("a", "A")])
        backend = SoundcardCaptureBackend()
        with pytest.raises(FatalError, match="見つかりません"):
            backend.start("missing")

    def test_start_twice_raises_runtime(self, mocker) -> None:
        mic = _make_fake_mic("mic_1", "Mic")
        cm = MagicMock()
        cm.__enter__ = MagicMock(return_value=MagicMock())
        cm.__exit__ = MagicMock(return_value=False)
        mic.recorder = MagicMock(return_value=cm)
        _patch_all_microphones(mocker, [mic])

        backend = SoundcardCaptureBackend()
        backend.start("mic_1")
        with pytest.raises(RuntimeError):
            backend.start("mic_1")
        backend.stop()

    def test_stop_when_not_started_is_safe(self) -> None:
        SoundcardCaptureBackend().stop()  # 例外が出ないこと


class TestReadChunk:
    def _setup(self, mocker, recorder_data: np.ndarray):
        mic = _make_fake_mic("mic_1", "Mic")
        recorder_obj = MagicMock()
        recorder_obj.record = MagicMock(return_value=recorder_data)
        cm = MagicMock()
        cm.__enter__ = MagicMock(return_value=recorder_obj)
        cm.__exit__ = MagicMock(return_value=False)
        mic.recorder = MagicMock(return_value=cm)
        _patch_all_microphones(mocker, [mic])
        backend = SoundcardCaptureBackend(chunk_size=4)
        backend.start("mic_1")
        return backend, recorder_obj

    def test_returns_mono_float32(self, mocker) -> None:
        # 2ch ステレオを返すケース → 平均してモノラル化される
        stereo = np.array(
            [[0.0, 1.0], [0.0, 1.0], [0.0, 1.0], [0.0, 1.0]], dtype=np.float32
        )
        backend, _ = self._setup(mocker, stereo)
        chunk = backend.read_chunk()
        assert chunk is not None
        assert chunk.dtype == np.float32
        assert chunk.ndim == 1
        assert chunk.shape == (4,)
        # 平均 = 0.5
        assert np.allclose(chunk, 0.5)
        backend.stop()

    def test_already_mono_passes_through(self, mocker) -> None:
        mono = np.array([[0.1], [0.2], [0.3], [0.4]], dtype=np.float32)
        backend, _ = self._setup(mocker, mono)
        chunk = backend.read_chunk()
        assert chunk is not None
        assert chunk.shape == (4,)
        assert np.allclose(chunk, [0.1, 0.2, 0.3, 0.4])
        backend.stop()

    def test_read_before_start_raises(self) -> None:
        backend = SoundcardCaptureBackend()
        with pytest.raises(RuntimeError):
            backend.read_chunk()

    def test_record_exception_wrapped_in_fatal(self, mocker) -> None:
        mic = _make_fake_mic("mic_1", "Mic")
        recorder_obj = MagicMock()
        recorder_obj.record = MagicMock(side_effect=OSError("device gone"))
        cm = MagicMock()
        cm.__enter__ = MagicMock(return_value=recorder_obj)
        cm.__exit__ = MagicMock(return_value=False)
        mic.recorder = MagicMock(return_value=cm)
        _patch_all_microphones(mocker, [mic])

        backend = SoundcardCaptureBackend()
        backend.start("mic_1")
        with pytest.raises(FatalError, match="音声取得"):
            backend.read_chunk()
        backend.stop()


class TestToInternalFormat:
    def test_2d_stereo_to_mono(self) -> None:
        data = np.array([[1.0, 3.0], [2.0, 4.0]], dtype=np.float32)
        result = _to_internal_format(data)
        assert result.shape == (2,)
        assert np.allclose(result, [2.0, 3.0])

    def test_2d_mono_squeezed(self) -> None:
        data = np.array([[1.0], [2.0]], dtype=np.float32)
        result = _to_internal_format(data)
        assert result.shape == (2,)

    def test_dtype_normalized_to_float32(self) -> None:
        data = np.array([[1.0]], dtype=np.float64)
        result = _to_internal_format(data)
        assert result.dtype == np.float32
