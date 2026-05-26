"""MainWindow: アプリのルートウィンドウ(customtkinter)。

役割: 設定パネル(SettingsPanel)と動作パネル(ControlPanel)を内包し、
AppController を受け取って両者に配る。
"""

from __future__ import annotations

import customtkinter as ctk

from voice_translator.common.app_controller import AppController

from .control_panel import ControlPanel
from .settings_panel import SettingsPanel


class MainWindow(ctk.CTk):
    """アプリ全体のルートウィンドウ。

    役割: タイトル/サイズ設定、SettingsPanel と ControlPanel を縦に配置、
    閉じる時にパイプライン停止を保証する。
    """

    def __init__(self, controller: AppController) -> None:
        super().__init__()
        self._controller = controller

        self.title("Voice Translator (MVP)")
        self.geometry("780x720")

        # 上: 設定、下: 動作
        self._settings = SettingsPanel(self, controller)
        self._settings.pack(fill="both", expand=False, padx=10, pady=(10, 5))

        self._control = ControlPanel(self, controller)
        self._control.pack(fill="both", expand=True, padx=10, pady=(5, 10))

        # ウィンドウクローズ時はパイプラインを止める
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def _on_close(self) -> None:
        try:
            self._controller.stop_pipeline()
        finally:
            self.destroy()
