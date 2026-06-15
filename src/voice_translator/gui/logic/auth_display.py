"""auth_display: 認証準備状態(AuthState)のステータス欄表示を決める純関数。

役割: 静的判定された AuthState から、設定パネルの行ステータスに出す上書き表示
(文言 + 色)を計算する。文言は ModelStatus と同じ英語表記で揃える。
認証が未完了の間はインスタンス状態(Init / Loaded 等)より優先して見せる —
「Loaded(緑)なのに Start すると認証エラー」という表示と挙動の矛盾を防ぐため。
"""

from __future__ import annotations

from voice_translator.common.types import AuthState, ModelStatus

from .palette import AUTH_MISSING_COLOR, AUTH_UNVERIFIED_COLOR

# 認証ステータス表示は「翻訳対象の文言」ではなく、ModelStatus 表示(英語 enum value)と
# 揃えるためのミラー。i18n カタログには入れず源を直接参照する(多言語化時に翻訳されて
# enum value とズレるのを防ぐ)。MISSING は enum value のミラー、UNVERIFIED は対応する
# enum value が無いが同じ英語表記で揃える独立文言。
AUTH_MISSING_TEXT = ModelStatus.MISSING_CREDENTIALS.value
AUTH_UNVERIFIED_TEXT = "Not Verified"


def auth_status_override(auth: AuthState) -> tuple[str, str] | None:
    """認証状態によるステータス欄の上書き (text, color)。上書き不要なら None。

    None のときは通常のステータス表示(ModelStatus + STATUS_COLORS)に委譲する。
    """
    if auth == AuthState.MISSING:
        return (AUTH_MISSING_TEXT, AUTH_MISSING_COLOR)
    if auth == AuthState.UNVERIFIED:
        return (AUTH_UNVERIFIED_TEXT, AUTH_UNVERIFIED_COLOR)
    return None
