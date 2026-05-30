"""CollapsibleSection widget の単体テスト。

実 Tk ルートを立てて widget を構築 → 状態遷移 / コールバック / body 表示の有無 を検証。
ヘッドレス環境(CI)で動かないので、Tk が立たない場合は skip する。
"""

from __future__ import annotations

import pytest


def _make_root():
    """customtkinter のルートを立てる。ヘッドレス環境では tkinter.TclError で skip。"""
    import customtkinter as ctk

    try:
        root = ctk.CTk()
    except Exception as e:  # noqa: BLE001 - 表示環境が無い場合
        pytest.skip(f"GUI 表示環境が無いため skip: {e}")
    root.withdraw()  # 画面に出さない(テスト中の視覚妨害を避ける)
    return root


@pytest.fixture()
def root():
    r = _make_root()
    yield r
    try:
        r.destroy()
    except Exception:  # noqa: BLE001
        pass


def test_initially_open_state(root) -> None:
    from voice_translator.gui.collapsible_section import CollapsibleSection

    section = CollapsibleSection(root, title="hello", initially_open=True)
    assert section.is_open is True
    assert "▼" in section._header_btn.cget("text")  # noqa: SLF001
    # body は grid 配置されている
    assert section.body.winfo_manager() == "grid"


def test_initially_closed_state(root) -> None:
    from voice_translator.gui.collapsible_section import CollapsibleSection

    section = CollapsibleSection(root, title="hello", initially_open=False)
    assert section.is_open is False
    assert "▶" in section._header_btn.cget("text")  # noqa: SLF001
    # body は grid 配置されていない
    assert section.body.winfo_manager() == ""


def test_toggle_changes_state(root) -> None:
    from voice_translator.gui.collapsible_section import CollapsibleSection

    section = CollapsibleSection(root, title="t", initially_open=True)
    section.toggle()
    assert section.is_open is False
    section.toggle()
    assert section.is_open is True


def test_open_close_are_idempotent(root) -> None:
    from voice_translator.gui.collapsible_section import CollapsibleSection

    section = CollapsibleSection(root, title="t", initially_open=True)
    section.open()  # 既に open
    section.open()
    assert section.is_open is True
    section.close()
    section.close()  # 既に closed
    assert section.is_open is False


def test_callback_fires_on_toggle(root) -> None:
    from voice_translator.gui.collapsible_section import CollapsibleSection

    received: list[bool] = []
    section = CollapsibleSection(
        root, title="t", initially_open=True,
        on_toggle=lambda is_open: received.append(is_open),
    )
    section.toggle()
    section.toggle()
    section.toggle()
    assert received == [False, True, False]


def test_callback_exception_does_not_break(root) -> None:
    """callback で例外が出ても toggle 自体は成功する。"""
    from voice_translator.gui.collapsible_section import CollapsibleSection

    def _broken(_is_open: bool) -> None:
        raise RuntimeError("listener broke")

    section = CollapsibleSection(root, title="t", initially_open=True, on_toggle=_broken)
    section.toggle()  # 例外は内部で握られる
    assert section.is_open is False


def test_body_is_a_frame(root) -> None:
    """body プロパティは CTkFrame を返し、子 widget を入れられる。"""
    import customtkinter as ctk
    from voice_translator.gui.collapsible_section import CollapsibleSection

    section = CollapsibleSection(root, title="t", initially_open=True)
    child = ctk.CTkLabel(section.body, text="child")
    child.pack()
    # 子が body の中に居る
    assert child.master is section.body
