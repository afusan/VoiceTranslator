"""NotificationBanner widget の単体テスト。

ヘッドレス環境では skip。表示状態と auto-dismiss を中心に検証。
"""

from __future__ import annotations

import pytest


def _make_root():
    import customtkinter as ctk

    try:
        root = ctk.CTk()
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"GUI 表示環境が無いため skip: {e}")
    root.withdraw()
    return root


@pytest.fixture()
def root():
    r = _make_root()
    yield r
    try:
        r.destroy()
    except Exception:  # noqa: BLE001
        pass


def test_initial_is_hidden(root) -> None:
    from voice_translator.gui.notification_banner import NotificationBanner

    banner = NotificationBanner(root)
    assert banner.is_visible is False


def test_show_error_makes_visible(root) -> None:
    from voice_translator.gui.notification_banner import NotificationBanner

    banner = NotificationBanner(root)
    # auto-dismiss を即発火させないよう duration_ms=0(永続)で表示
    banner.show_error("boom", duration_ms=0)
    assert banner.is_visible is True
    assert "boom" in banner._msg_label.cget("text")  # noqa: SLF001


def test_dismiss_hides(root) -> None:
    from voice_translator.gui.notification_banner import NotificationBanner

    banner = NotificationBanner(root)
    banner.show_error("x", duration_ms=0)
    assert banner.is_visible is True
    banner.dismiss()
    assert banner.is_visible is False


def test_show_overwrites_previous_message(root) -> None:
    """既に表示中に別の show_xxx を呼ぶと、新しい内容で上書きされる。"""
    from voice_translator.gui.notification_banner import NotificationBanner

    banner = NotificationBanner(root)
    banner.show_error("first", duration_ms=0)
    banner.show_info("second", duration_ms=0)
    assert banner.is_visible is True
    assert "second" in banner._msg_label.cget("text")  # noqa: SLF001


def test_dismiss_is_idempotent(root) -> None:
    from voice_translator.gui.notification_banner import NotificationBanner

    banner = NotificationBanner(root)
    banner.dismiss()  # 既に非表示
    banner.dismiss()
    assert banner.is_visible is False


def test_auto_dismiss_timer_scheduled(root) -> None:
    """duration_ms > 0 のとき after() で auto-dismiss がスケジュールされる。"""
    from voice_translator.gui.notification_banner import NotificationBanner

    banner = NotificationBanner(root)
    banner.show_error("x", duration_ms=100)
    # 内部 id が設定されている
    assert banner._auto_dismiss_id is not None  # noqa: SLF001
    # 二度目の show で前タイマがキャンセル → 新タイマに置き換わる
    old_id = banner._auto_dismiss_id  # noqa: SLF001
    banner.show_warning("y", duration_ms=200)
    assert banner._auto_dismiss_id is not None  # noqa: SLF001
    assert banner._auto_dismiss_id != old_id  # noqa: SLF001


def test_three_severity_variants(root) -> None:
    """error / warning / info すべて呼べる。"""
    from voice_translator.gui.notification_banner import NotificationBanner

    banner = NotificationBanner(root)
    banner.show_error("e", duration_ms=0)
    assert banner.is_visible
    banner.show_warning("w", duration_ms=0)
    assert banner.is_visible
    banner.show_info("i", duration_ms=0)
    assert banner.is_visible
