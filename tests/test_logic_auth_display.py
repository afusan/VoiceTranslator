"""gui/logic/auth_display.py の単体テスト(純関数・固定文字列)。

認証準備状態のステータス欄上書き表示を固定する。文言は ModelStatus と同じ
英語表記で揃える(`Missing Credentials` は ModelStatus.MISSING_CREDENTIALS.value
と同一であること)。
"""

from __future__ import annotations

from voice_translator.common.types import AuthState, ModelStatus
from voice_translator.gui.logic.auth_display import (
    AUTH_MISSING_TEXT,
    AUTH_UNVERIFIED_TEXT,
    auth_status_override,
)
from voice_translator.gui.logic.palette import (
    AUTH_MISSING_COLOR,
    AUTH_UNVERIFIED_COLOR,
    STATUS_COLORS,
)


class TestAuthStatusOverride:
    def test_missing_overrides_with_red(self) -> None:
        assert auth_status_override(AuthState.MISSING) == (
            "Missing Credentials", AUTH_MISSING_COLOR,
        )

    def test_unverified_overrides_with_amber(self) -> None:
        assert auth_status_override(AuthState.UNVERIFIED) == (
            "Not Verified", AUTH_UNVERIFIED_COLOR,
        )

    def test_not_required_and_verified_return_none(self) -> None:
        """上書き不要 → None(通常の ModelStatus 表示に委譲)。"""
        assert auth_status_override(AuthState.NOT_REQUIRED) is None
        assert auth_status_override(AuthState.VERIFIED) is None

    def test_missing_text_matches_model_status_value(self) -> None:
        """インスタンス由来(MISSING_CREDENTIALS)と静的判定で表記が揃うこと。"""
        assert AUTH_MISSING_TEXT == ModelStatus.MISSING_CREDENTIALS.value

    def test_unverified_text_fixed(self) -> None:
        assert AUTH_UNVERIFIED_TEXT == "Not Verified"


class TestPaletteAuthColors:
    def test_missing_credentials_status_has_red(self) -> None:
        """ModelStatus.MISSING_CREDENTIALS に専用色(赤)が定義されている
        (以前は未定義でグレー fallback だった)。"""
        assert STATUS_COLORS[ModelStatus.MISSING_CREDENTIALS] == "#dc2626"

    def test_auth_colors_match_status_palette(self) -> None:
        """未入力 = 失敗系の赤 / 未検証 = 進行中の琥珀(STATUS_COLORS と同系統)。"""
        assert AUTH_MISSING_COLOR == "#dc2626"
        assert AUTH_UNVERIFIED_COLOR == "#d97706"
