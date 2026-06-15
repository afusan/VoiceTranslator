"""NotificationBanner: 起動失敗等の通知を画面上部に目立つ形で出す widget。

役割: 「ボタン押したけど何も起きない」事故(status_label に出ているが見落とされる)を
防ぐため、エラー / 警告 / 情報を色付きバナーでウィンドウ最上部に表示する。
auto-dismiss + 手動 ✕ ボタンを併設。

責務:
- 1 件ずつ表示(複数同時は出さない、最新で上書き)
- show_error / show_warning / show_info の 3 段階で色分け
- duration_ms=0 で永続表示(ユーザが ✕ するまで消えない)
"""

from __future__ import annotations

import customtkinter as ctk


# 色テーマ(severity 別の背景色 / 文字色)。視認性を優先し背景はやや濃いめ。
_THEMES = {
    "error":   {"bg": "#7f1d1d", "fg": "#fee2e2"},  # dark red / pale red
    "warning": {"bg": "#92400e", "fg": "#fef3c7"},  # dark amber / pale amber
    "info":    {"bg": "#1e3a8a", "fg": "#dbeafe"},  # dark blue / pale blue
}

# 既定の auto-dismiss 時間(ms)。0 で永続。
_DEFAULT_DURATIONS = {
    "error": 12_000,   # エラーは少し長めに見せる
    "warning": 8_000,
    "info": 5_000,
}


class NotificationBanner(ctk.CTkFrame):
    """ウィンドウ上部に出る通知バナー。

    使い方:
        banner = NotificationBanner(parent)
        # 必要なときだけ表示(pack 自体は show_xxx 内で行う)
        banner.show_error("起動失敗: 入力デバイスと出力デバイスが同じです")

    親 widget は pack ベース想定。バナーは自分で `pack(fill="x", side="top", before=...)`
    して表示し、`pack_forget()` で隠す。表示順は親から渡された `before_widget` で固定。
    """

    def __init__(self, master, *, before_widget=None) -> None:
        super().__init__(master, fg_color="transparent")
        self._before_widget = before_widget
        self._visible = False
        self._auto_dismiss_id: str | None = None

        # 内側の content frame(背景色を持つ)
        self._content = ctk.CTkFrame(self, fg_color="transparent", corner_radius=4)
        self._content.pack(fill="x", padx=0, pady=0)

        self._msg_label = ctk.CTkLabel(
            self._content, text="", anchor="w", justify="left",
            wraplength=720,  # 長いメッセージは折り返す
        )
        self._msg_label.pack(side="left", fill="x", expand=True, padx=10, pady=6)

        self._dismiss_btn = ctk.CTkButton(
            self._content, text="✕", width=28, height=28,
            command=self.dismiss,
            fg_color="transparent",
            hover_color="#1f2937",
            corner_radius=4,
        )
        self._dismiss_btn.pack(side="right", padx=6, pady=4)

    def set_before_widget(self, widget) -> None:
        """バナーを表示する際の `before` 対象を差し替える(Panel 再構築時に貼り替える)。"""
        self._before_widget = widget

    # ============================================================
    # 公開 API
    # ============================================================
    @property
    def is_visible(self) -> bool:
        return self._visible

    def show_error(self, message: str, *, duration_ms: int | None = None) -> None:
        """エラーバナーを表示(赤系)。duration_ms 未指定なら既定 12 秒。0 で永続。"""
        self._show("error", message, duration_ms)

    def show_warning(self, message: str, *, duration_ms: int | None = None) -> None:
        """警告バナー(琥珀系)。"""
        self._show("warning", message, duration_ms)

    def show_info(self, message: str, *, duration_ms: int | None = None) -> None:
        """情報バナー(青系)。"""
        self._show("info", message, duration_ms)

    def dismiss(self) -> None:
        """バナーを閉じる(冪等)。"""
        self._cancel_auto_dismiss()
        if not self._visible:
            return
        try:
            self.pack_forget()
        except Exception:  # noqa: BLE001
            pass
        self._visible = False

    # ============================================================
    # 内部
    # ============================================================
    def _show(self, severity: str, message: str, duration_ms: int | None) -> None:
        theme = _THEMES.get(severity, _THEMES["info"])
        if duration_ms is None:
            duration_ms = _DEFAULT_DURATIONS.get(severity, 5_000)

        # 表示色 + メッセージを更新
        self._content.configure(fg_color=theme["bg"])
        self._msg_label.configure(text=message, text_color=theme["fg"])
        self._dismiss_btn.configure(text_color=theme["fg"])

        # 表示(既に表示中なら pack はスキップして上書きのみ)
        if not self._visible:
            pack_kwargs = dict(fill="x", side="top", padx=10, pady=(8, 0))
            if self._before_widget is not None:
                try:
                    self.pack(before=self._before_widget, **pack_kwargs)
                except Exception:  # noqa: BLE001 - widget 破棄済み等
                    self.pack(**pack_kwargs)
            else:
                self.pack(**pack_kwargs)
            self._visible = True

        # auto-dismiss タイマ
        self._cancel_auto_dismiss()
        if duration_ms and duration_ms > 0:
            self._auto_dismiss_id = self.after(duration_ms, self.dismiss)

    def _cancel_auto_dismiss(self) -> None:
        if self._auto_dismiss_id is None:
            return
        try:
            self.after_cancel(self._auto_dismiss_id)
        except Exception:  # noqa: BLE001
            pass
        self._auto_dismiss_id = None
