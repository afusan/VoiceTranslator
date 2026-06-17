"""has_settings 純関数の small テスト。

判定は layer_settings_schema.visible_fields() に委譲しているため、
スキーマ宣言と一致していることを確認する。GUI / controller は不要。
"""

from __future__ import annotations

import pytest

from voice_translator.common.types import LayerKind
from voice_translator.gui.logic.settings_button import has_settings


class TestHasSettings:
    def test_vad_silero_no_settings(self) -> None:
        """silero は auto_load 一掃後に設定項目ゼロ → False。"""
        assert has_settings(LayerKind.VAD, "silero") is False

    def test_vad_webrtcvad_has_settings(self) -> None:
        """webrtcvad は aggressiveness / frame_ms を持つ → True。"""
        assert has_settings(LayerKind.VAD, "webrtcvad") is True

    def test_tts_mms_no_settings(self) -> None:
        """mms は auto_load 一掃後に設定項目ゼロ → False。"""
        assert has_settings(LayerKind.TTS, "mms") is False

    def test_tts_sapi_has_settings(self) -> None:
        """sapi は rate 等の設定を持つ → True。"""
        assert has_settings(LayerKind.TTS, "sapi") is True

    def test_capture_soundcard_has_settings(self) -> None:
        assert has_settings(LayerKind.CAPTURE, "soundcard") is True

    def test_output_soundcard_has_settings(self) -> None:
        assert has_settings(LayerKind.OUTPUT, "soundcard") is True

    def test_asr_faster_whisper_has_settings(self) -> None:
        assert has_settings(LayerKind.ASR, "faster_whisper") is True
