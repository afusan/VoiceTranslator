"""MainWindow: アプリのルートウィンドウ(customtkinter)。

役割: 言語切替バー / NotificationBanner / SettingsPanel / ControlPanel を内包し、
AppController を介して連携させる。アプリ起動時に `auto_load=True` が指定されている
backend のレイヤだけを先行ロードする(Phase B: 既定では全レイヤが auto_load=False
なので、起動はモデルロード無しで完了する)。

UI 改修(2026-06-05 / P1):
- SettingsPanel 全体を 1 セクションで包む方式は廃止。SettingsPanel が内部で
  「バックエンド」「デバイス」「翻訳」の 3 セクションを独立して持つため、
  ここでは裸で配置する。`ui.collapsed.settings_panel` キーは廃止。
- NotificationBanner は最上部に配置。起動失敗等を目立つ形で出す(`show_error` 等)。

言語切替(Phase 4a):
- 起動時に `ui.locale`(既定 ja)を `i18n.set_locale` に反映する。
- 上部の言語スイッチャ(🌐)で切替。切替は **停止中のみ**許可し、SettingsPanel /
  ControlPanel を destroy → 再生成して新ロケールで描画し直す(選択値は ConfigStore から
  復元されるので作り直しでも保たれる)。動作中は警告を出して選択を戻す。
"""

from __future__ import annotations

import customtkinter as ctk

from voice_translator.common.app_controller import AppController

from .control_panel import ControlPanel
from .i18n import (
    available_locales,
    current_locale,
    locale_display_name,
    set_locale,
    tr,
)
from .logic.locale_switch import (
    can_switch_locale,
    resolve_initial_locale,
    resolve_target_locale,
)
from .notification_banner import NotificationBanner
from .settings_panel import SettingsPanel


class MainWindow(ctk.CTk):
    """アプリ全体のルートウィンドウ。"""

    def __init__(self, controller: AppController) -> None:
        super().__init__()
        self._controller = controller

        # 起動時に保存済みロケールを反映(未対応値は ja に縮退。判断は logic)。
        saved = str(self._controller.get_setting("ui", "locale", default="ja"))
        set_locale(resolve_initial_locale(saved, available_locales()))

        self.title("Voice Translator (MVP)")
        self.geometry("820x720")

        # 0) 言語スイッチャ(最上部、永続)。表示名は各言語自身の表記なので切替で変わらない。
        self._build_locale_bar()

        # 1) 通知バナー(初期非表示)。ControlPanel から参照されるので先に作る。
        self._banner = NotificationBanner(self)

        # 2) 切替で作り直す Panel 群(SettingsPanel / ControlPanel)
        self._settings: SettingsPanel | None = None
        self._control: ControlPanel | None = None
        self._build_panels()

        self.protocol("WM_DELETE_WINDOW", self._on_close)

        # Phase B: auto_load=True のレイヤだけ起動時にバックグラウンドロード。
        # 既定 OFF なので通常はこの呼び出しは即時 on_done する(対象なし)。
        self._controller.load_auto_load_layers_async()

    # ----------------------------------------------------------
    def _build_locale_bar(self) -> None:
        bar = ctk.CTkFrame(self, fg_color="transparent")
        bar.pack(fill="x", padx=10, pady=(8, 0))
        ctk.CTkLabel(bar, text="🌐").pack(side="right", padx=(4, 0))
        codes = available_locales()
        self._locale_var = ctk.StringVar(value=locale_display_name(current_locale()))
        self._locale_display_to_code = {locale_display_name(c): c for c in codes}
        ctk.CTkOptionMenu(
            bar,
            values=[locale_display_name(c) for c in codes],
            variable=self._locale_var,
            width=120,
            command=self._on_locale_changed,
        ).pack(side="right")

    def _build_panels(self) -> None:
        """SettingsPanel / ControlPanel を生成して配置する(再構築でも呼ぶ)。"""
        self._settings = SettingsPanel(self, self._controller, banner=self._banner)
        self._settings.pack(fill="both", expand=False, padx=10, pady=(10, 5))
        # banner の `before` を SettingsPanel に向けておく(バナー表示時に上に出る)
        self._banner.set_before_widget(self._settings)

        self._control = ControlPanel(self, self._controller, banner=self._banner)
        self._control.pack(fill="both", expand=True, padx=10, pady=(5, 10))

    def _is_running(self) -> bool:
        """controller の動作状態を取得する(取得失敗は False に縮退 = View の入力収集)。"""
        try:
            return bool(self._controller.is_running)
        except Exception:  # noqa: BLE001
            return False

    def _on_locale_changed(self, display: str) -> None:
        """言語スイッチャ変更: 停止中のみ切替を許可し、Panel を作り直す。

        判断(no-op / display→code / 動作中拒否)は `gui/logic/locale_switch` に委譲し、
        ここは入力収集 → logic → 反映(永続化 + set_locale + 再構築)の配線に徹する。
        """
        locale = resolve_target_locale(display, self._locale_display_to_code, current_locale())
        if locale is None:
            return  # 同一ロケール or 未知の表示名 = no-op
        # 動作中は再構築で表示と実行状態が食い違うため拒否(設定の再読込と同じ方針)。
        if not can_switch_locale(self._is_running()):
            self._banner.show_error(tr("main_window.locale_running_blocked"))
            self._locale_var.set(locale_display_name(current_locale()))
            return
        self._controller.set_setting("ui", "locale", locale)
        set_locale(locale)
        # コールバック中の自己破壊を避けるため after で再構築する。
        self.after(0, self._rebuild_panels)

    def _rebuild_panels(self) -> None:
        """Panel 群を破棄して新ロケールで作り直す。"""
        for panel in (self._settings, self._control):
            if panel is not None:
                try:
                    panel.destroy()
                except Exception:  # noqa: BLE001 - 破棄済み等は無視
                    pass
        self._build_panels()

    def _on_close(self) -> None:
        try:
            self._controller.stop_pipeline()
        finally:
            self.destroy()
