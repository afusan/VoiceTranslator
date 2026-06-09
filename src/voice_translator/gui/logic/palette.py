"""palette: UI 配色の定数表。

役割: ModelStatus・アクセラレータ表示・グレーアウトの色コードを一元管理する。
値の出自: settings_panel.py / control_panel.py に散在していたリテラル(P1 で集約)。
"""

from __future__ import annotations

from voice_translator.common.types import ModelStatus

# ModelStatus → 色(customtkinter は hex 色名をそのまま使える)
STATUS_COLORS: dict[ModelStatus, str] = {
    ModelStatus.INIT: "#64748b",            # slate gray (まだロード起動前)
    ModelStatus.NOT_DOWNLOADED: "#dc2626",  # red
    ModelStatus.LOADING: "#d97706",         # amber
    ModelStatus.LOADED: "#16a34a",          # green
}
STATUS_COLOR_DEFAULT = "#64748b"

# アクセラレータ表示(演算: GPU / CPU のみ / 不明)
ACCEL_GREEN = "#16a34a"
ACCEL_AMBER = "#d97706"
ACCEL_SLATE = "#94a3b8"

# TTS=(なし) 時のグレーアウト文字色
DISABLED_TEXT = "#475569"
