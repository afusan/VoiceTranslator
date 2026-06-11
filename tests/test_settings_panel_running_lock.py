"""SettingsPanel の動作中バックエンド行ロックの配線テスト(shim 方式)。

固定する契約:
- 動作中(running=True)はバックエンド行のプルダウン / 設定ボタンをすべて disable
  (選択を変えても動作に反映されず「何で動いているのか」が表示と食い違うため)
- 停止(running=False)で normal に戻し、編成表示(吸収 / TTS=(なし))の disable を
  再適用する(_apply_absorbed_visuals に委譲)
- 編成表示の復帰処理・TTS=(なし) 解除処理も動作中は widget を normal に戻さない
  (`_interactive_state` が "disabled" を返す)
"""

from __future__ import annotations

from unittest.mock import MagicMock, PropertyMock

import customtkinter as ctk
import pytest

from voice_translator.common.types import LayerKind


def _bind(shim, *method_names: str):
    from voice_translator.gui.settings_panel import SettingsPanel

    for name in method_names:
        setattr(shim, name, getattr(SettingsPanel, name).__get__(shim))


_DEFAULT_COLOR = ("gray10", "#DCE4EE")


@pytest.fixture()
def stub_panel():
    from voice_translator.gui.settings_panel import SettingsPanel

    shim = MagicMock(spec=SettingsPanel)
    _bind(shim, "_apply_running_lock_visual", "_interactive_state")
    shim._controller = MagicMock(name="controller")
    shim._controller.is_running = False

    rows = {}
    widgets = {}
    for layer in (LayerKind.ASR, LayerKind.TRANSLATOR):
        row_label = MagicMock(spec=ctk.CTkLabel, name=f"{layer.value}_label")
        option = MagicMock(spec=ctk.CTkOptionMenu, name=f"{layer.value}_option")
        status = MagicMock(spec=ctk.CTkLabel, name=f"{layer.value}_status")
        button = MagicMock(spec=ctk.CTkButton, name=f"{layer.value}_btn")
        rows[layer] = [row_label, option, status, button]
        widgets[layer] = (row_label, option, status, button)
    shim._backend_rows = rows
    return shim, widgets


class TestRunningLockVisual:
    def test_running_disables_all_backend_rows(self, stub_panel) -> None:
        shim, widgets = stub_panel

        shim._apply_running_lock_visual(True)

        for _, option, _, button in widgets.values():
            option.configure.assert_called_with(state="disabled")
            button.configure.assert_called_with(state="disabled")
        # 動作中はラベル・ステータスには触らない
        for row_label, _, status, _ in widgets.values():
            row_label.configure.assert_not_called()
            status.configure.assert_not_called()
        shim._apply_absorbed_visuals.assert_not_called()

    def test_stop_reenables_and_reapplies_overrides(self, stub_panel) -> None:
        """停止時は normal に戻したあと、吸収 / TTS=(なし) の disable を再適用する。"""
        shim, widgets = stub_panel

        shim._apply_running_lock_visual(False)

        for _, option, _, button in widgets.values():
            option.configure.assert_called_with(state="normal")
            button.configure.assert_called_with(state="normal")
        shim._apply_absorbed_visuals.assert_called_once()


class TestInteractiveState:
    def _shim(self) -> MagicMock:
        from voice_translator.gui.settings_panel import SettingsPanel

        shim = MagicMock(spec=SettingsPanel)
        _bind(shim, "_interactive_state")
        shim._controller = MagicMock(name="controller")
        return shim

    def test_disabled_while_running(self) -> None:
        shim = self._shim()
        shim._controller.is_running = True
        assert shim._interactive_state() == "disabled"

    def test_normal_when_idle(self) -> None:
        shim = self._shim()
        shim._controller.is_running = False
        assert shim._interactive_state() == "normal"

    def test_controller_failure_degrades_to_normal(self) -> None:
        """問い合わせ失敗は「停止中」扱い(UI を不必要にロックしない)。"""
        shim = self._shim()
        type(shim._controller).is_running = PropertyMock(
            side_effect=RuntimeError("boom")
        )
        assert shim._interactive_state() == "normal"


class TestRunningGuardsOtherVisualPaths:
    """動作中は編成復帰 / TTS=(なし) 解除でも widget を normal に戻さない。"""

    def test_absorbed_restore_keeps_disabled_while_running(self) -> None:
        from voice_translator.gui.settings_panel import SettingsPanel

        shim = MagicMock(spec=SettingsPanel)
        _bind(
            shim,
            "_apply_absorbed_visuals",
            "_absorbed_roles",
            "_skipped_roles",
            "_restore_text_color",
            "_interactive_state",
        )
        shim._controller = MagicMock(name="controller")
        shim._controller.output_mode = "audio"
        shim._controller.is_running = True
        shim._controller.get_absorbed_roles.return_value = {}
        shim._default_row_text_color = _DEFAULT_COLOR
        option = MagicMock(spec=ctk.CTkOptionMenu, name="option")
        button = MagicMock(spec=ctk.CTkButton, name="button")
        shim._backend_rows = {LayerKind.TRANSLATOR: [option, button]}
        shim._status_labels = {}
        shim._status_overridden = {LayerKind.TRANSLATOR}  # 吸収が解除された直後

        shim._apply_absorbed_visuals()

        option.configure.assert_called_with(state="disabled")
        button.configure.assert_called_with(state="disabled")

    def test_tts_none_release_keeps_disabled_while_running(self) -> None:
        from voice_translator.gui.settings_panel import SettingsPanel

        shim = MagicMock(spec=SettingsPanel)
        _bind(shim, "_apply_tts_none_visual", "_restore_text_color", "_interactive_state")
        shim._controller = MagicMock(name="controller")
        shim._controller.get_setting.return_value = "sapi"  # TTS=(なし) ではない
        shim._controller.is_running = True
        shim._default_row_text_color = _DEFAULT_COLOR
        out_option = MagicMock(spec=ctk.CTkOptionMenu, name="out_option")
        shim._backend_rows = {LayerKind.OUTPUT: [out_option], LayerKind.TTS: []}
        shim._status_labels = {}

        shim._apply_tts_none_visual()

        out_option.configure.assert_called_with(state="disabled")
