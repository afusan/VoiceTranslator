"""ConsentDialog: クラウド backend 選択時の同意ダイアログ(customtkinter)。

役割: SettingsPanel の backend プルダウンで `is_cloud=True` の項目が選ばれたときに
発火する。送信先 / 送信データ / 利用規約 / 「今後表示しない」チェックボックスを表示し、
ユーザの同意/キャンセルを受け取る。

prePlan 論点 2 のひな形通り:
- 同意済み(consents.<backend>: true)なら発火しない(=戻り値 True で即時通過)
- suppress_dialogs フラグ ON なら発火しない(=戻り値 True で即時通過)
- 「同意して使用」→ consents.<backend>: true を永続化、True を返す
- 「キャンセル」→ 何も保存せず False を返す(呼び出し側でプルダウンを元に戻す)
- 「今後表示しない」ON で同意 → 加えて consents.suppress_dialogs: true を永続化

呼び出し例:
    if ConsentDialog.maybe_show(parent, controller, backend_name="openai_whisper", ...):
        # 同意を得た、または既存同意あり
        controller.set_setting("backends", "asr", "openai_whisper")
    else:
        # キャンセルされた、プルダウン値を戻す
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import customtkinter as ctk

from voice_translator.gui.i18n import tr

if TYPE_CHECKING:
    from voice_translator.common.app_controller import AppController


class ConsentDialog(ctk.CTkToplevel):
    """クラウド送信同意ダイアログ。1 回作って `show_modal()` で結果を取る。"""

    def __init__(
        self,
        parent,
        *,
        backend_name: str,
        service_name: str,
        terms_url: str | None = None,
        data_sent_summary: str | None = None,
    ) -> None:
        super().__init__(parent)
        self._result: bool = False
        self._suppress: bool = False

        self.title(tr("dialog.consent.title"))
        self.geometry("520x420")
        self.transient(parent)
        try:
            self.grab_set()
        except Exception:  # noqa: BLE001
            pass

        self._build_widgets(
            backend_name=backend_name,
            service_name=service_name,
            terms_url=terms_url,
            data_sent_summary=data_sent_summary,
        )
        # 閉じる ✕ ボタンは「キャンセル」と同じ扱い
        self.protocol("WM_DELETE_WINDOW", self._on_cancel)

    # ----------------------------------------------------------
    def _build_widgets(
        self,
        *,
        backend_name: str,
        service_name: str,
        terms_url: str | None,
        data_sent_summary: str,
    ) -> None:
        ctk.CTkLabel(
            self, text=tr("dialog.consent.heading"),
            font=("", 16, "bold"),
        ).grid(row=0, column=0, sticky="w", padx=12, pady=(12, 4))

        summary = (
            data_sent_summary if data_sent_summary is not None
            else tr("dialog.consent.default_data_summary")
        )
        msg = tr(
            "dialog.consent.body",
            backend=backend_name,
            service=service_name,
            summary=summary,
        )
        ctk.CTkLabel(
            self, text=msg, justify="left", wraplength=480, anchor="w",
        ).grid(row=1, column=0, sticky="ew", padx=12, pady=(0, 8))

        if terms_url:
            ctk.CTkLabel(
                self, text=tr("dialog.consent.terms", url=terms_url),
                text_color="#3b82f6", anchor="w",
            ).grid(row=2, column=0, sticky="w", padx=12, pady=(0, 8))

        # 「今後表示しない」チェック
        self._suppress_var = ctk.StringVar(value="0")
        ctk.CTkCheckBox(
            self,
            text=tr("dialog.consent.suppress"),
            variable=self._suppress_var, onvalue="1", offvalue="0",
        ).grid(row=3, column=0, sticky="w", padx=12, pady=(8, 0))

        # ボタン
        btn_frame = ctk.CTkFrame(self, fg_color="transparent")
        btn_frame.grid(row=4, column=0, sticky="e", padx=12, pady=(12, 12))
        ctk.CTkButton(
            btn_frame, text=tr("dialog.consent.cancel"), width=120, command=self._on_cancel,
        ).pack(side="right", padx=(8, 0))
        ctk.CTkButton(
            btn_frame, text=tr("dialog.consent.accept"), width=140, command=self._on_accept,
        ).pack(side="right")

        self.columnconfigure(0, weight=1)

    # ----------------------------------------------------------
    def _on_accept(self) -> None:
        self._result = True
        self._suppress = (self._suppress_var.get() == "1")
        self._dismiss()

    def _on_cancel(self) -> None:
        self._result = False
        self._dismiss()

    def _dismiss(self) -> None:
        try:
            self.grab_release()
        except Exception:  # noqa: BLE001
            pass
        self.destroy()

    # ----------------------------------------------------------
    @property
    def result(self) -> bool:
        return self._result

    @property
    def suppress(self) -> bool:
        return self._suppress

    # ----------------------------------------------------------
    @classmethod
    def maybe_show(
        cls,
        parent,
        controller: "AppController",
        *,
        backend_name: str,
        service_name: str,
        terms_url: str | None = None,
        data_sent_summary: str | None = None,
    ) -> bool:
        """既存同意 / suppress 設定をチェックし、必要なら同意ダイアログを開く。

        戻り値:
        - True: 同意あり(既存 or 新規)。呼び出し側は backend 切替を進めてよい
        - False: ユーザがキャンセル。呼び出し側はプルダウン等の表示を元に戻す
        """
        # 既存同意があれば即座に True
        existing = controller.get_setting("consents", backend_name, default=False)
        if existing:
            return True
        # 一括 OFF が立っているなら確認なしで True(prePlan 論点 2)
        suppress = controller.get_setting("consents", "suppress_dialogs", default=False)
        if suppress:
            controller.set_setting("consents", backend_name, True)
            return True

        # ダイアログを開いて待つ
        dlg = cls(
            parent,
            backend_name=backend_name,
            service_name=service_name,
            terms_url=terms_url,
            data_sent_summary=data_sent_summary,
        )
        # モーダル風に待機(Tk の wait_window)
        try:
            parent.wait_window(dlg)
        except Exception:  # noqa: BLE001
            pass

        if not dlg.result:
            return False
        # 同意を永続化
        controller.set_setting("consents", backend_name, True)
        if dlg.suppress:
            controller.set_setting("consents", "suppress_dialogs", True)
        return True
