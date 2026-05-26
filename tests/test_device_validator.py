"""DeviceValidator の単体テスト。"""

from __future__ import annotations

import pytest

from voice_translator.common.device_validator import DeviceValidator
from voice_translator.common.errors import FatalError


class TestDeviceValidator:
    def test_different_devices_ok(self) -> None:
        DeviceValidator.validate("mic_1", "spk_1")  # 例外が出ないこと

    def test_same_devices_raises(self) -> None:
        with pytest.raises(FatalError, match="同じ"):
            DeviceValidator.validate("same_id", "same_id")

    def test_empty_input_raises(self) -> None:
        with pytest.raises(FatalError, match="入力"):
            DeviceValidator.validate("", "spk_1")

    def test_empty_output_raises(self) -> None:
        with pytest.raises(FatalError, match="出力"):
            DeviceValidator.validate("mic_1", "")

    def test_none_input_raises(self) -> None:
        with pytest.raises(FatalError, match="入力"):
            DeviceValidator.validate(None, "spk_1")

    def test_none_output_raises(self) -> None:
        with pytest.raises(FatalError, match="出力"):
            DeviceValidator.validate("mic_1", None)
