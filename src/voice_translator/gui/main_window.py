"""MainWindow: アプリのルートウィンドウ(customtkinter)。

役割: NotificationBanner / SettingsPanel / ControlPanel を内包し、
AppController を介して連携させる。アプリ起動時に `auto_load=True` が指定されている
backend のレイヤだけを先行ロードする(Phase B: 既定では全レイヤが auto_load=False
なので、起動はモデルロード無しで完了する)。

UI 改修(2026-06-05 / P1):
- SettingsPanel 全体を 1 セクションで包む方式は廃止。SettingsPanel が内部で
  「バックエンド」「デバイス」「翻訳」の 3 セクションを独立して持つため、
  ここでは裸で配置する。`ui.collapsed.settings_panel` キーは廃止。
- NotificationBanner は最上部に配置。起動失敗等を目立つ形で出す(`show_error` 等)。
"""

from __future__ import annotations

import customtkinter as ctk

from voice_translator.common.app_controller import AppController

from .control_panel import ControlPanel
from .notification_banner import NotificationBanner
from .settings_panel import SettingsPanel


class MainWindow(ctk.CTk):
    """アプリ全体のルートウィンドウ。"""

    def __init__(self, controller: AppController) -> None:
        super().__init__()
        self._controller = controller

        self.title("Voice Translator (MVP)")
        self.geometry("820x720")

        # 1) 通知バナー(最上部、初期非表示)。ControlPanel から参照されるので先に作る。
        self._banner = NotificationBanner(self)

        # 2) SettingsPanel(内部で 3 セクションを自前で折り畳む)
        self._settings = SettingsPanel(self, controller, banner=self._banner)
        self._settings.pack(fill="both", expand=False, padx=10, pady=(10, 5))

        # banner の `before` を SettingsPanel に向けておく(バナー表示時に上に出る)
        self._banner._before_widget = self._settings  # noqa: SLF001 - 内部値の遅延注入

        # 3) ControlPanel(動作系)。banner を渡して起動失敗時に show_error させる。
        # 各 Panel は AppController のイベントを自身で購読するため(P2)、
        # Panel 間の参照注入は不要になった。
        self._control = ControlPanel(self, controller, banner=self._banner)
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
