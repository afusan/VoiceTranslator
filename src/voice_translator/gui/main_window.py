"""MainWindow: アプリのルートウィンドウ(customtkinter)。

役割: NotificationBanner / SettingsPanel(折り畳み付き)/ ControlPanel を内包し、
AppController を介して連携させる。アプリ起動時に `auto_load=True` が指定されている
backend のレイヤだけを先行ロードする(Phase B: 既定では全レイヤが auto_load=False
なので、起動はモデルロード無しで完了する)。

UI 改修(2026-05-30):
- NotificationBanner を最上部に配置。起動失敗等を目立つ形で出す(`show_error` 等)。
- SettingsPanel 全体を CollapsibleSection で囲い、見出しクリックで折り畳み可能に。
- 開閉状態は ConfigStore の `ui.collapsed.settings_panel` に永続化。
"""

from __future__ import annotations

import customtkinter as ctk

from voice_translator.common.app_controller import AppController

from .collapsible_section import CollapsibleSection
from .control_panel import ControlPanel
from .notification_banner import NotificationBanner
from .settings_panel import SettingsPanel


# ConfigStore のキー: 折り畳み状態の永続化用
_CFG_COLLAPSED_SETTINGS = ("ui", "collapsed", "settings_panel")


class MainWindow(ctk.CTk):
    """アプリ全体のルートウィンドウ。"""

    def __init__(self, controller: AppController) -> None:
        super().__init__()
        self._controller = controller

        self.title("Voice Translator (MVP)")
        self.geometry("820x720")

        # 1) 通知バナー(最上部、初期非表示)。ControlPanel から参照されるので先に作る。
        self._banner = NotificationBanner(self)
        # 表示時の `before` 用に SettingsPanel collapsible への参照を後で渡す

        # 2) SettingsPanel を CollapsibleSection で包む
        settings_initially_open = not bool(
            controller.get_setting(*_CFG_COLLAPSED_SETTINGS, default=False)
        )
        self._settings_section = CollapsibleSection(
            self, title="設定",
            initially_open=settings_initially_open,
            on_toggle=self._on_settings_toggle,
        )
        self._settings_section.pack(fill="both", expand=False, padx=10, pady=(10, 5))
        self._settings = SettingsPanel(
            self._settings_section.body, controller, banner=self._banner,
        )
        self._settings.pack(fill="both", expand=True)

        # banner の `before` を SettingsSection に向けておく(バナー表示時に上に出る)
        self._banner._before_widget = self._settings_section  # noqa: SLF001 - 内部値の遅延注入

        # 3) ControlPanel(動作系)。banner を渡して起動失敗時に show_error させる。
        self._control = ControlPanel(
            self, controller,
            settings_panel=self._settings,
            banner=self._banner,
        )
        self._control.pack(fill="both", expand=True, padx=10, pady=(5, 10))

        self.protocol("WM_DELETE_WINDOW", self._on_close)

        # Phase B: auto_load=True のレイヤだけ起動時にバックグラウンドロード。
        # 既定 OFF なので通常はこの呼び出しは即時 on_done する(対象なし)。
        # ユーザは詳細ダイアログから個別に auto_load を ON にできる。
        self._controller.load_auto_load_layers_async()

    def _on_settings_toggle(self, is_open: bool) -> None:
        """設定セクションの開閉状態を ConfigStore に永続化。"""
        try:
            self._controller.set_setting(*_CFG_COLLAPSED_SETTINGS, not is_open)
        except Exception:  # noqa: BLE001
            pass

    def _on_close(self) -> None:
        try:
            self._controller.stop_pipeline()
        finally:
            self.destroy()
