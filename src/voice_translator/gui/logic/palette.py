"""palette: UI 配色の定数表。

役割: ModelStatus・アクセラレータ表示・グレーアウトの色コードを一元管理する。
値の出自: settings_panel.py / control_panel.py に散在していたリテラル(P1 で集約)。
"""

from __future__ import annotations

from voice_translator.common.types import ModelStatus

# ModelStatus → 色(customtkinter は hex 色名をそのまま使える)
STATUS_COLORS: dict[ModelStatus, str] = {
    ModelStatus.INIT: "#64748b",                  # slate gray (まだロード起動前)
    ModelStatus.MISSING_CREDENTIALS: "#dc2626",   # red (認証不足は失敗系と同格)
    ModelStatus.NOT_DOWNLOADED: "#dc2626",        # red
    ModelStatus.LOADING: "#d97706",               # amber
    ModelStatus.LOADED: "#16a34a",                # green
}
STATUS_COLOR_DEFAULT = "#64748b"

# 認証準備状態(AuthState)の上書き表示色。ModelStatus と同じ系統で揃える
# (未入力 = 失敗系の赤 / 未検証 = 進行中の琥珀)。
AUTH_MISSING_COLOR = "#dc2626"
AUTH_UNVERIFIED_COLOR = "#d97706"

# アクセラレータ表示(演算: GPU / CPU のみ / 不明)
ACCEL_GREEN = "#16a34a"
ACCEL_AMBER = "#d97706"
ACCEL_SLATE = "#94a3b8"

# TTS=(なし) 時のグレーアウト文字色
DISABLED_TEXT = "#475569"
