"""gui/logic/backend_display.py の単体テスト(純関数)。

旧 test_settings_panel_tts_none.py / test_capture_kind.py のモジュール関数テストから
変換シナリオを移植(P1 / refactor-ui-3move)。
"""

from __future__ import annotations

from voice_translator.common.types import CaptureKind, LayerKind
from voice_translator.gui.logic.backend_display import (
    backend_display_to_internal,
    backend_internal_to_display,
    capture_display_to_internal,
    capture_internal_to_display,
    tts_display_to_internal,
    tts_internal_to_display,
)


class TestTtsConversion:
    def test_none_display_to_internal(self) -> None:
        assert tts_display_to_internal("(なし)") == "none"

    def test_regular_names_pass_through_to_internal(self) -> None:
        assert tts_display_to_internal("sapi") == "sapi"
        assert tts_display_to_internal("piper") == "piper"

    def test_none_internal_to_display(self) -> None:
        assert tts_internal_to_display("none") == "(なし)"

    def test_regular_names_pass_through_to_display(self) -> None:
        assert tts_internal_to_display("sapi") == "sapi"

    def test_roundtrip(self) -> None:
        for name in ("none", "sapi"):
            assert tts_display_to_internal(tts_internal_to_display(name)) == name


class TestCaptureConversion:
    def test_display_to_internal_extracts_backend_name(self) -> None:
        assert capture_display_to_internal("デバイス (soundcard)") == "soundcard"
        assert capture_display_to_internal("プロセス (proctap)") == "proctap"

    def test_display_to_internal_passes_through_plain_names(self) -> None:
        assert capture_display_to_internal("plain_backend") == "plain_backend"

    def test_internal_to_display_with_device_kind(self) -> None:
        assert (
            capture_internal_to_display("soundcard", CaptureKind.DEVICE)
            == "デバイス (soundcard)"
        )

    def test_internal_to_display_with_process_kind(self) -> None:
        assert (
            capture_internal_to_display("proctap", CaptureKind.PROCESS)
            == "プロセス (proctap)"
        )

    def test_internal_to_display_with_unknown_kind_passes_through(self) -> None:
        """kind 解決失敗(None)は backend 名そのまま(防衛挙動)。"""
        assert capture_internal_to_display("soundcard", None) == "soundcard"

    def test_internal_to_display_unregistered_uses_tr(self) -> None:
        from voice_translator.gui.logic.backend_display import (
            UNREGISTERED_INTERNAL,
            unregistered_display,
        )

        # 未登録 sentinel は CJK を含まない内部値で、表示は tr() で解決する(en でも漏れない)。
        assert capture_internal_to_display(UNREGISTERED_INTERNAL, CaptureKind.DEVICE) == unregistered_display()
        assert capture_internal_to_display("", CaptureKind.DEVICE) == ""


class TestLayerDispatch:
    def test_tts_layer_dispatches_to_tts_conversion(self) -> None:
        assert backend_display_to_internal(LayerKind.TTS, "(なし)") == "none"
        assert backend_internal_to_display(LayerKind.TTS, "none") == "(なし)"

    def test_capture_layer_dispatches_to_capture_conversion(self) -> None:
        assert (
            backend_display_to_internal(LayerKind.CAPTURE, "デバイス (soundcard)")
            == "soundcard"
        )
        assert (
            backend_internal_to_display(
                LayerKind.CAPTURE, "soundcard", capture_kind=CaptureKind.DEVICE,
            )
            == "デバイス (soundcard)"
        )

    def test_other_layers_pass_through(self) -> None:
        assert backend_display_to_internal(LayerKind.ASR, "faster_whisper") == "faster_whisper"
        assert backend_internal_to_display(LayerKind.ASR, "faster_whisper") == "faster_whisper"


class TestSkippedStatusText:
    """編成表示の固定文言(変更はふるまい変更としてここに現れる)。

    吸収レイヤのステータス欄文言(absorbed_status_text)は撤去済み: 設定パネルでは
    プルダウン無効化 + 空表示で伝え、代行 backend 名の明示はステータス集約
    (status_summary)に一本化した。
    """

    def test_skipped_status_text(self) -> None:
        from voice_translator.gui.logic.backend_display import skipped_status_text

        assert skipped_status_text() == "(なし)"
