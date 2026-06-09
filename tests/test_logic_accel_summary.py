"""gui/logic/accel_summary.py の単体テスト(純関数)。

移行元: control_panel.py の `_refresh_accel_label` の判定部。
"""

from __future__ import annotations

from voice_translator.common.types import LayerKind
from voice_translator.gui.logic.accel_summary import summarize_accel
from voice_translator.gui.logic.palette import ACCEL_AMBER, ACCEL_GREEN, ACCEL_SLATE


def _devices(**by_layer: str | None) -> dict[LayerKind, str | None]:
    """layer.value をキーに device 文字列を指定する補助(無指定レイヤは None)。"""
    base: dict[LayerKind, str | None] = {layer: None for layer in LayerKind}
    for key, value in by_layer.items():
        base[LayerKind(key)] = value
    return base


class TestSummarizeAccel:
    def test_cuda_reports_gpu_green(self) -> None:
        text, color = summarize_accel(
            _devices(asr="cuda", translator="cpu"), output_mode="audio",
        )
        assert text == "演算: GPU (cuda)"
        assert color == ACCEL_GREEN

    def test_mps_reports_gpu(self) -> None:
        text, _ = summarize_accel(_devices(asr="mps"), output_mode="audio")
        assert text == "演算: GPU (mps)"

    def test_multiple_gpus_sorted(self) -> None:
        text, _ = summarize_accel(
            _devices(asr="mps", translator="cuda"), output_mode="audio",
        )
        assert text == "演算: GPU (cuda, mps)"

    def test_all_cpu_reports_amber(self) -> None:
        text, color = summarize_accel(
            _devices(asr="cpu", translator="cpu"), output_mode="audio",
        )
        assert text == "演算: CPU のみ"
        assert color == ACCEL_AMBER

    def test_no_devices_reports_preparing(self) -> None:
        text, color = summarize_accel(_devices(), output_mode="audio")
        assert text == "演算: -(モデル準備中)"
        assert color == ACCEL_SLATE

    def test_text_only_ignores_tts_output_devices(self) -> None:
        """text_only では TTS / OUTPUT の device 報告を無視する。"""
        text, _ = summarize_accel(
            _devices(tts="cuda", output="cuda", asr="cpu"), output_mode="text_only",
        )
        assert text == "演算: CPU のみ"

    def test_uppercase_device_normalized(self) -> None:
        text, _ = summarize_accel(_devices(asr="CUDA"), output_mode="audio")
        assert text == "演算: GPU (cuda)"
