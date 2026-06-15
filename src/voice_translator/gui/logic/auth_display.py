"""auth_display: 認証準備状態(AuthState)のステータス欄表示を決める純関数。

役割: 静的判定された AuthState から、設定パネルの行ステータスに出す上書き表示
(文言 + 色)を計算する。文言は ModelStatus と同じ英語表記で揃える。
認証が未完了の間はインスタンス状態(Init / Loaded 等)より優先して見せる —
「Loaded(緑)なのに Start すると認証エラー」という表示と挙動の矛盾を防ぐため。
"""

from __future__ import annotations

from voice_translator.common.types import AuthState

from .messages import tr
from .palette import AUTH_MISSING_COLOR, AUTH_UNVERIFIED_COLOR

# ModelStatus.MISSING_CREDENTIALS.value と同一表記(インスタンス由来の表示と揃える)
AUTH_MISSING_TEXT = tr("auth.missing")
AUTH_UNVERIFIED_TEXT = tr("auth.unverified")


def auth_status_override(auth: AuthState) -> tuple[str, str] | None:
    """認証状態によるステータス欄の上書き (text, color)。上書き不要なら None。

    None のときは通常のステータス表示(ModelStatus + STATUS_COLORS)に委譲する。
    """
    if auth == AuthState.MISSING:
        return (AUTH_MISSING_TEXT, AUTH_MISSING_COLOR)
    if auth == AuthState.UNVERIFIED:
        return (AUTH_UNVERIFIED_TEXT, AUTH_UNVERIFIED_COLOR)
    return None
