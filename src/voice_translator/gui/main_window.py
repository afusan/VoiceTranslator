"""MainWindow: アプリのルートウィンドウ(customtkinter)。

役割: SettingsPanel と ControlPanel を内包し、AppController を介して連携させる。
"""

from __future__ import annotations

import customtkinter as ctk

from voice_translator.common.app_controller import AppController

from .control_panel import ControlPanel
from .settings_panel import SettingsPanel


class MainWindow(ctk.CTk):
    """アプリ全体のルートウィンドウ。"""

    def __init__(self, controller: AppController) -> None:
        super().__init__()
        self._controller = controller

        self.title("Voice Translator (MVP)")
        self.geometry("820x720")

        self._settings = SettingsPanel(self, controller)
        self._settings.pack(fill="both", expand=False, padx=10, pady=(10, 5))

        self._control = ControlPanel(self, controller, settings_panel=self._settings)
        self._control.pack(fill="both", expand=True, padx=10, pady=(5, 10))

        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def _on_close(self) -> None:
        try:
            self._controller.stop_pipeline()
        finally:
            self.destroy()
