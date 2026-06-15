"""LanguageSelectDialog: 検索付きの言語選択ダイアログ。

役割: 候補言語が多数(MMS-TTS ∩ NLLB で 100 超)になっても選びやすいよう、検索ボックス +
絞り込みリストで 1 言語を選ばせる。SettingsPanel の言語行の「🔍」ボタンから呼ばれ、
選択結果(内部コード)を `result_code` に残す。`OptionMenu` は検索非対応なので別ウィジェット。

絞り込みの判断は `gui/logic/language_filter.py` の純関数に委譲し、本体は
「入力収集 → logic 呼び出し → リスト再描画」だけを行う(UI 規約)。

使い方:
    dlg = LanguageSelectDialog(parent, codes=candidate_codes, initial="jpn")
    dlg.wait_window()
    if dlg.result_code is not None:
        ...  # 言語が選ばれた
"""

from __future__ import annotations

from typing import Sequence

import customtkinter as ctk

from voice_translator.common.languages import format_language
from voice_translator.gui.logic.language_filter import filter_languages


class LanguageSelectDialog(ctk.CTkToplevel):
    """検索ボックス + 絞り込みリストで言語を 1 つ選ぶダイアログ。

    クリックで即確定(言語ピッカーなので OK ボタンは設けない)。Cancel / 閉じるで
    `result_code` は None のまま。
    """

    def __init__(
        self,
        parent,
        *,
        codes: Sequence[str],
        initial: str | None = None,
        title: str = "言語を選択",
    ) -> None:
        super().__init__(parent)
        self._codes: list[str] = list(codes)
        self.result_code: str | None = None
        self._row_widgets: list[ctk.CTkButton] = []

        self.title(title)
        self.geometry("360x460")
        self.transient(parent)
        try:
            self.grab_set()
        except Exception:  # noqa: BLE001
            pass

        self._query_var = ctk.StringVar(value="")
        self._build_widgets(initial)
        self.protocol("WM_DELETE_WINDOW", self._on_cancel)
        self._refresh_list()

    # ----------------------------------------------------------
    def _build_widgets(self, initial: str | None) -> None:
        ctk.CTkLabel(self, text="言語を検索(コード / 英語名):", anchor="w").grid(
            row=0, column=0, sticky="ew", padx=12, pady=(12, 2)
        )
        self._search = ctk.CTkEntry(
            self, textvariable=self._query_var, placeholder_text="例: swahili / swh",
        )
        self._search.grid(row=1, column=0, sticky="ew", padx=12, pady=(0, 8))
        self._query_var.trace_add("write", lambda *_: self._refresh_list())

        self._list_frame = ctk.CTkScrollableFrame(self)
        self._list_frame.grid(row=2, column=0, sticky="nsew", padx=12, pady=(0, 8))

        ctk.CTkButton(self, text="Cancel", width=90, command=self._on_cancel).grid(
            row=3, column=0, sticky="e", padx=12, pady=(0, 12)
        )

        self.columnconfigure(0, weight=1)
        self.rowconfigure(2, weight=1)

        try:
            self._search.focus_set()
        except Exception:  # noqa: BLE001
            pass

    # ----------------------------------------------------------
    def _refresh_list(self) -> None:
        """現在のクエリで候補を絞り込み、リストを再描画する。"""
        for w in self._row_widgets:
            try:
                w.destroy()
            except Exception:  # noqa: BLE001
                pass
        self._row_widgets.clear()

        matches = filter_languages(self._codes, self._query_var.get())
        if not matches:
            empty = ctk.CTkButton(
                self._list_frame, text="(一致なし)", state="disabled",
                fg_color="transparent",
            )
            empty.pack(anchor="w", fill="x", padx=2, pady=1)
            self._row_widgets.append(empty)
            return

        for code in matches:
            btn = ctk.CTkButton(
                self._list_frame,
                text=format_language(code),
                anchor="w",
                fg_color="transparent",
                command=lambda c=code: self._on_pick(c),
            )
            btn.pack(anchor="w", fill="x", padx=2, pady=1)
            self._row_widgets.append(btn)

    # ----------------------------------------------------------
    def _on_pick(self, code: str) -> None:
        self.result_code = code
        self._close()

    def _on_cancel(self) -> None:
        self.result_code = None
        self._close()

    def _close(self) -> None:
        try:
            self.grab_release()
        except Exception:  # noqa: BLE001
            pass
        self.destroy()
