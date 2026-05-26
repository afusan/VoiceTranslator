"""共通型 (types.py) の単体テスト。"""

from __future__ import annotations

import numpy as np

from voice_translator.common.types import (
    INTERNAL_CHANNELS,
    INTERNAL_DTYPE,
    INTERNAL_SAMPLE_RATE,
    BackendCapabilities,
    CaptureSource,
    LayerKind,
    OutputDevice,
)


class TestConstants:
    def test_internal_format(self) -> None:
        assert INTERNAL_SAMPLE_RATE == 16_000
        assert INTERNAL_CHANNELS == 1
        assert INTERNAL_DTYPE == np.float32

    def test_layer_kind_values(self) -> None:
        # 想定するレイヤがすべて列挙されていること
        expected = {"capture", "vad", "asr", "translator", "tts", "output"}
        assert {layer.value for layer in LayerKind} == expected


class TestCaptureSource:
    def test_construct_minimal(self) -> None:
        src = CaptureSource(source_id="abc", display_name="Mic")
        assert src.is_loopback is False

    def test_loopback_flag(self) -> None:
        src = CaptureSource(source_id="spk", display_name="Speakers", is_loopback=True)
        assert src.is_loopback is True


class TestOutputDevice:
    def test_construct(self) -> None:
        d = OutputDevice(device_id="hp", display_name="Headphones")
        assert d.device_id == "hp"


class TestBackendCapabilities:
    def test_defaults(self) -> None:
        cap = BackendCapabilities()
        assert cap.supported_languages == ()
        assert cap.requires_gpu is False
        assert cap.notes == ""
        assert cap.extra == {}
