"""accel_summary: アクセラレータ表示(演算: GPU / CPU のみ / 不明)の集約判定。

役割: 各レイヤの device 報告から「演算: …」ラベルの文言と色を決める。

移行元(P1 / refactor-ui-3move): control_panel.py の `_refresh_accel_label` の判定部。
文言・色・判定規則は移行元と同一に保つこと。
"""

from __future__ import annotations

from typing import Mapping

from voice_translator.common.types import LayerKind

from .palette import ACCEL_AMBER, ACCEL_GREEN, ACCEL_SLATE


def summarize_accel(
    devices: Mapping[LayerKind, str | None], *, output_mode: str,
) -> tuple[str, str]:
    """(表示文言, 色) を返す。

    - 1 つでも GPU(cuda / mps)報告があれば「演算: GPU (cuda)」(緑)
    - すべて CPU なら「演算: CPU のみ」(琥珀。動作はするが最速ではない)
    - device 報告がまだ無ければ「演算: -(モデル準備中)」(slate)
    - text_only モードでは TTS / OUTPUT の device 報告は無視する
    - device 文字列は小文字に正規化して判定する
    """
    gpu_devices: set[str] = set()
    has_cpu = False
    for layer, device in devices.items():
        if output_mode == "text_only" and layer in (LayerKind.TTS, LayerKind.OUTPUT):
            continue
        if not device:
            continue
        d = device.lower()
        if d in ("cuda", "mps"):
            gpu_devices.add(d)
        elif d == "cpu":
            has_cpu = True

    if gpu_devices:
        return f"演算: GPU ({', '.join(sorted(gpu_devices))})", ACCEL_GREEN
    if has_cpu:
        return "演算: CPU のみ", ACCEL_AMBER
    return "演算: -(モデル準備中)", ACCEL_SLATE
