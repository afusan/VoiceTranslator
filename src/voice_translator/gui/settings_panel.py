"""SettingsPanel: 設定操作UI(customtkinter)。

役割: バックエンド/デバイス/言語ペア/ログ出力先 のプルダウン+入力欄と、
設定の保存/読込ボタンを提供する。レイヤ別のモデルステータス(英語表示)も併記する。
"""

from __future__ import annotations

import customtkinter as ctk

from voice_translator.common.app_controller import AppController
from voice_translator.common.types import LayerKind, ModelStatus

from .layer_settings_dialog import LayerSettingsDialog

# GUIで切替対象とするレイヤと表示ラベル
_LAYER_LABELS: list[tuple[LayerKind, str]] = [
    (LayerKind.CAPTURE, "音声取得"),
    (LayerKind.VAD, "VAD"),
    (LayerKind.ASR, "ASR(書き起こし)"),
    (LayerKind.TRANSLATOR, "翻訳"),
    (LayerKind.TTS, "TTS(音声合成)"),
    (LayerKind.OUTPUT, "音声出力"),
]

_LANG_CHOICES: list[str] = [
    "auto", "en", "ja", "zh", "ko", "es", "fr", "de", "it", "pt", "ru", "ar",
    "hi", "th", "vi", "id", "tr",
]

# ModelStatus → 色マップ(customtkinter は色名そのまま使える)
_STATUS_COLORS: dict[ModelStatus, str] = {
    ModelStatus.INIT: "#64748b",            # slate gray (まだロード起動前)
    ModelStatus.NOT_DOWNLOADED: "#dc2626",  # red
    ModelStatus.LOADING: "#d97706",         # amber
    ModelStatus.LOADED: "#16a34a",          # green
}


class SettingsPanel(ctk.CTkFrame):
    """設定操作のパネル + レイヤ別モデルステータス表示。"""

    def __init__(self, master, controller: AppController) -> None:
        super().__init__(master)
        self._controller = controller

        self._backend_vars: dict[LayerKind, ctk.StringVar] = {}
        self._status_labels: dict[LayerKind, ctk.CTkLabel] = {}
        self._capture_var = ctk.StringVar(value="(未選択)")
        self._output_var = ctk.StringVar(value="(未選択)")
        self._src_var = ctk.StringVar(value=str(controller.get_setting("languages", "src", default="auto")))
        self._tgt_var = ctk.StringVar(value=str(controller.get_setting("languages", "tgt", default="ja")))
        self._log_dir_var = ctk.StringVar(value=str(controller.get_setting("log", "directory", default="./logs")))

        self._build_widgets()
        self._populate_devices_into_dropdowns()
        self._sync_all_status_labels()

    # ============================================================
    def _build_widgets(self) -> None:
        ctk.CTkLabel(self, text="設定", font=("", 16, "bold")).grid(
            row=0, column=0, columnspan=4, sticky="w", padx=10, pady=(8, 4)
        )

        row = 1
        # レイヤ実装の選択 + モデルステータス + 設定ボタン
        # ("バックエンド" 表記はユーザ向けには冗長なので外し、ラベル単体に統一)
        for layer, label in _LAYER_LABELS:
            ctk.CTkLabel(self, text=f"{label}:").grid(
                row=row, column=0, sticky="w", padx=10, pady=2
            )
            names = self._controller.list_backends(layer) or ["(未登録)"]
            current = str(self._controller.get_setting("backends", layer.value, default=names[0]))
            var = ctk.StringVar(value=current)
            option = ctk.CTkOptionMenu(
                self,
                values=names,
                variable=var,
                command=lambda v, lyr=layer: self._on_backend_change(lyr, v),
            )
            option.grid(row=row, column=1, sticky="ew", padx=10, pady=2)
            self._backend_vars[layer] = var

            status_label = ctk.CTkLabel(self, text="-", text_color="#64748b", anchor="w")
            status_label.grid(row=row, column=2, sticky="ew", padx=(4, 4), pady=2)
            self._status_labels[layer] = status_label

            # レイヤ別「設定」ボタン → LayerSettingsDialog
            ctk.CTkButton(
                self, text="設定", width=60,
                command=lambda lyr=layer: self._open_layer_settings(lyr),
            ).grid(row=row, column=3, sticky="e", padx=(0, 10), pady=2)

            row += 1

        # レイヤ実装グループとデバイス選択グループの境界線(視覚的な区切り)
        separator = ctk.CTkFrame(self, height=2, fg_color="#475569")
        separator.grid(
            row=row, column=0, columnspan=4, sticky="ew", padx=10, pady=(8, 8)
        )
        row += 1

        # 入力デバイス
        ctk.CTkLabel(self, text="入力デバイス:").grid(
            row=row, column=0, sticky="w", padx=10, pady=2
        )
        self._capture_dropdown = ctk.CTkOptionMenu(
            self, values=["(列挙中)"], variable=self._capture_var, command=self._on_capture_changed
        )
        self._capture_dropdown.grid(row=row, column=1, columnspan=3, sticky="ew", padx=10, pady=2)
        row += 1

        # 出力デバイス
        ctk.CTkLabel(self, text="出力デバイス:").grid(
            row=row, column=0, sticky="w", padx=10, pady=2
        )
        self._output_dropdown = ctk.CTkOptionMenu(
            self, values=["(列挙中)"], variable=self._output_var, command=self._on_output_changed
        )
        self._output_dropdown.grid(row=row, column=1, columnspan=3, sticky="ew", padx=10, pady=2)
        row += 1

        # src 言語
        ctk.CTkLabel(self, text="入力言語 (src):").grid(
            row=row, column=0, sticky="w", padx=10, pady=2
        )
        ctk.CTkOptionMenu(
            self, values=_LANG_CHOICES, variable=self._src_var,
            command=lambda v: self._controller.set_setting("languages", "src", v),
        ).grid(row=row, column=1, columnspan=3, sticky="ew", padx=10, pady=2)
        row += 1

        # tgt 言語
        ctk.CTkLabel(self, text="出力言語 (tgt):").grid(
            row=row, column=0, sticky="w", padx=10, pady=2
        )
        ctk.CTkOptionMenu(
            self,
            values=[c for c in _LANG_CHOICES if c != "auto"],
            variable=self._tgt_var,
            command=lambda v: self._controller.set_setting("languages", "tgt", v),
        ).grid(row=row, column=1, columnspan=3, sticky="ew", padx=10, pady=2)
        row += 1

        # ログ出力先
        ctk.CTkLabel(self, text="ログ出力先:").grid(
            row=row, column=0, sticky="w", padx=10, pady=2
        )
        log_frame = ctk.CTkFrame(self)
        log_frame.grid(row=row, column=1, columnspan=3, sticky="ew", padx=10, pady=2)
        log_frame.columnconfigure(0, weight=1)
        log_entry = ctk.CTkEntry(log_frame, textvariable=self._log_dir_var)
        log_entry.grid(row=0, column=0, sticky="ew")
        log_entry.bind(
            "<FocusOut>",
            lambda _e: self._controller.set_setting("log", "directory", self._log_dir_var.get()),
        )
        row += 1

        # 保存/再読込
        btn_frame = ctk.CTkFrame(self)
        btn_frame.grid(row=row, column=0, columnspan=4, sticky="ew", padx=10, pady=(8, 8))
        ctk.CTkButton(btn_frame, text="設定を保存", command=self._on_save).pack(
            side="left", padx=4
        )
        ctk.CTkButton(btn_frame, text="設定を再読込", command=self._on_reload).pack(
            side="left", padx=4
        )
        ctk.CTkButton(btn_frame, text="デバイス再列挙", command=self._populate_devices_into_dropdowns).pack(
            side="left", padx=4
        )

        self.columnconfigure(1, weight=1)
        self.columnconfigure(2, weight=0)
        self.columnconfigure(3, weight=0)

    # ----------------------------------------------------------
    def _open_layer_settings(self, layer: LayerKind) -> None:
        """指定レイヤの設定ダイアログを開く(モーダル風)。"""
        LayerSettingsDialog(self, self._controller, layer)

    # ============================================================
    # ステータス更新(AppController から呼ばれる)
    # ============================================================
    def on_status_change(self, layer: LayerKind, status: ModelStatus) -> None:
        """AppController からのコールバック(別スレッド可)。UIへ反映する。"""
        # tkinter はメインスレッドからの更新が必須
        self.after(0, lambda: self._apply_status(layer, status))

    def _apply_status(self, layer: LayerKind, status: ModelStatus) -> None:
        label = self._status_labels.get(layer)
        if label is None:
            return
        text = self._format_status_text(layer, status)
        label.configure(text=text, text_color=_STATUS_COLORS.get(status, "#64748b"))

    def _format_status_text(self, layer: LayerKind, status: ModelStatus) -> str:
        """Loaded のときだけ device 情報を併記する("Loaded (cuda)" 等)。

        device 概念を持たないレイヤ(Capture/VAD/TTS/Output)は status のみ表示。
        """
        if status != ModelStatus.LOADED:
            return status.value
        device = self._controller.get_layer_device(layer)
        if not device:
            return status.value
        return f"{status.value} ({device})"

    def _sync_all_status_labels(self) -> None:
        """初期化時/再読込後に全レイヤのステータスを一括反映する。"""
        for layer, status in self._controller.get_all_model_statuses().items():
            self._apply_status(layer, status)

    # ============================================================
    def _on_backend_change(self, layer: LayerKind, value: str) -> None:
        self._controller.set_setting("backends", layer.value, value)
        # set_setting 側でステータスは更新されるが、ラベル反映は明示的に行う
        self._sync_all_status_labels()

    def _populate_devices_into_dropdowns(self) -> None:
        try:
            sources = self._controller.list_capture_sources()
        except Exception as e:  # noqa: BLE001
            sources = []
            self._capture_dropdown.configure(values=[f"(取得失敗: {e})"])
        try:
            devices = self._controller.list_output_devices()
        except Exception as e:  # noqa: BLE001
            devices = []
            self._output_dropdown.configure(values=[f"(取得失敗: {e})"])

        if sources:
            self._capture_dropdown.configure(values=[s.display_name for s in sources])
            self._capture_id_map = {s.display_name: s.source_id for s in sources}
            current_id = self._controller.get_setting("devices", "input")
            for s in sources:
                if s.source_id == current_id:
                    self._capture_var.set(s.display_name)
                    break
            else:
                self._capture_var.set(sources[0].display_name)
                self._controller.set_setting("devices", "input", sources[0].source_id)
        else:
            self._capture_id_map = {}

        if devices:
            self._output_dropdown.configure(values=[d.display_name for d in devices])
            self._output_id_map = {d.display_name: d.device_id for d in devices}
            current_id = self._controller.get_setting("devices", "output")
            for d in devices:
                if d.device_id == current_id:
                    self._output_var.set(d.display_name)
                    break
            else:
                self._output_var.set(devices[0].display_name)
                self._controller.set_setting("devices", "output", devices[0].device_id)
        else:
            self._output_id_map = {}

    def _on_capture_changed(self, display_name: str) -> None:
        device_id = self._capture_id_map.get(display_name)
        if device_id:
            self._controller.set_setting("devices", "input", device_id)

    def _on_output_changed(self, display_name: str) -> None:
        device_id = self._output_id_map.get(display_name)
        if device_id:
            self._controller.set_setting("devices", "output", device_id)

    def _on_save(self) -> None:
        try:
            self._controller.save_settings()
        except Exception as e:  # noqa: BLE001
            self._show_message(f"保存失敗: {e}")
        else:
            self._show_message("設定を保存しました")

    def _on_reload(self) -> None:
        try:
            self._controller.load_settings()
        except Exception as e:  # noqa: BLE001
            self._show_message(f"読込失敗: {e}")
        else:
            self._populate_devices_into_dropdowns()
            self._sync_all_status_labels()
            self._show_message("設定を再読込しました")

    def _show_message(self, msg: str) -> None:
        print(f"[SettingsPanel] {msg}")
