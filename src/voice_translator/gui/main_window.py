"""MainWindow: アプリのルートウィンドウ(customtkinter)。

役割: SettingsPanel と ControlPanel を内包し、AppController を介して連携させる。
アプリ起動時に `auto_load=True` が指定されている backend のレイヤだけを先行ロードする
(Phase B: 既定では全レイヤが auto_load=False なので、起動はモデルロード無しで完了する)。
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

        # Phase B: auto_load=True のレイヤだけ起動時にバックグラウンドロード。
        # 既定 OFF なので通常はこの呼び出しは即時 on_done する(対象なし)。
        # ユーザは詳細ダイアログから個別に auto_load を ON にできる。
        self._controller.load_auto_load_layers_async()

    def _on_close(self) -> None:
        try:
            self._controller.stop_pipeline()
        finally:
            self.destroy()
