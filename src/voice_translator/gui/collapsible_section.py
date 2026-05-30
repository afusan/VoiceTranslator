"""CollapsibleSection: 子 widget を折り畳める汎用セクション widget。

役割: 「▼ タイトル」「▶ タイトル」のヘッダボタンを持ち、クリックで `body` フレームの
表示/非表示を切り替える。MainWindow の SettingsPanel や ControlPanel の status_text
など、画面を広く使いたいときに畳める領域に被せて使う。

開閉状態は `on_toggle` callback 経由で外部に通知し、ConfigStore 等に永続化できる。
widget 自身は永続化機構を持たない(責務分離)。
"""

from __future__ import annotations

from typing import Callable

import customtkinter as ctk


# ヘッダボタンの矢印記号(open / closed の状態表示)
_ARROW_OPEN = "▼"
_ARROW_CLOSED = "▶"


class CollapsibleSection(ctk.CTkFrame):
    """折り畳み可能なセクション widget。

    使い方:
        section = CollapsibleSection(parent, title="設定", initially_open=True,
                                     on_toggle=lambda is_open: ...)
        # 子 widget は section.body の中に配置する
        ctk.CTkLabel(section.body, text="hello").pack()

    レイアウト:
        - row 0: ヘッダ(クリックでトグル)
        - row 1: body フレーム(子 widget はここに pack/grid する)
    body の表示/非表示は `grid_remove()` / `grid()` で行うため、ヘッダ位置は jitter しない。
    """

    def __init__(
        self,
        master,
        title: str,
        *,
        initially_open: bool = True,
        on_toggle: Callable[[bool], None] | None = None,
    ) -> None:
        super().__init__(master, fg_color="transparent")
        self._title = title
        self._is_open = bool(initially_open)
        self._on_toggle = on_toggle

        # ヘッダ: 左寄せボタン。fg_color transparent + 余白少なめでセクション見出しらしく。
        self._header_btn = ctk.CTkButton(
            self,
            text=self._header_text(),
            command=self._handle_toggle,
            anchor="w",
            height=28,
            fg_color="transparent",
            hover_color="#1f2937",
            corner_radius=4,
        )
        self._header_btn.grid(row=0, column=0, sticky="ew", padx=0, pady=(0, 2))

        # body フレーム: 子要素はここに置く(.body 経由でアクセス)
        self.body = ctk.CTkFrame(self, fg_color="transparent")
        if self._is_open:
            self.body.grid(row=1, column=0, sticky="nsew")

        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)  # body は伸縮可能

    # ============================================================
    # 公開 API
    # ============================================================
    @property
    def is_open(self) -> bool:
        """現在の開閉状態。"""
        return self._is_open

    def toggle(self) -> None:
        """開閉を切り替える(on_toggle callback も発火)。"""
        if self._is_open:
            self.close()
        else:
            self.open()

    def open(self) -> None:
        """開ける(冪等。既に開いていれば no-op)。"""
        if self._is_open:
            return
        self._is_open = True
        self._header_btn.configure(text=self._header_text())
        self.body.grid(row=1, column=0, sticky="nsew")
        self._fire_callback()

    def close(self) -> None:
        """閉じる(冪等。既に閉じていれば no-op)。"""
        if not self._is_open:
            return
        self._is_open = False
        self._header_btn.configure(text=self._header_text())
        self.body.grid_remove()
        self._fire_callback()

    # ============================================================
    # 内部
    # ============================================================
    def _header_text(self) -> str:
        arrow = _ARROW_OPEN if self._is_open else _ARROW_CLOSED
        return f"{arrow} {self._title}"

    def _handle_toggle(self) -> None:
        """ヘッダクリックハンドラ。toggle() 経由で状態遷移 + コールバック発火。"""
        self.toggle()

    def _fire_callback(self) -> None:
        """`on_toggle` を発火(失敗しても本体は止めない)。"""
        if self._on_toggle is None:
            return
        try:
            self._on_toggle(self._is_open)
        except Exception:  # noqa: BLE001 - listener の破綻で UI を止めない
            pass
