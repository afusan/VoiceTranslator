"""SettingsPanel の編成表示(_apply_absorbed_visuals)の配線テスト(shim 方式)。

「動かないレイヤ」の実態表示を固定する:
- 複合 backend に吸収 → 「(〜側で実行: <backend>)」+ グレー
- 編成対象外(text_only の TTS/Output)→ 「(なし)」
- 解除時は既定色 + 実ステータスに復帰。ctk が受け付けない `text_color=None` を
  使わないこと(過去に None 渡しの ValueError が握りつぶされ、グレー表示が残る不具合)。
"""

from __future__ import annotations

from unittest.mock import MagicMock

import customtkinter as ctk
import pytest

from voice_translator.common.types import LayerKind, ModelStatus
from voice_translator.gui.logic.backend_display import SKIPPED_STATUS_TEXT
from voice_translator.gui.logic.palette import DISABLED_TEXT


_DEFAULT_COLOR = ("gray10", "#DCE4EE")


def _bind(shim, *method_names: str):
    from voice_translator.gui.settings_panel import SettingsPanel

    for name in method_names:
        setattr(shim, name, getattr(SettingsPanel, name).__get__(shim))


@pytest.fixture()
def stub_panel():
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
    shim._controller.is_running = False
    shim._default_row_text_color = _DEFAULT_COLOR

    row_label = MagicMock(spec=ctk.CTkLabel, name="row_label")
    status_label = MagicMock(spec=ctk.CTkLabel, name="status_label")
    option_menu = MagicMock(spec=ctk.CTkOptionMenu, name="option_menu")
    cfg_button = MagicMock(spec=ctk.CTkButton, name="cfg_button")
    shim._backend_rows = {
        LayerKind.TRANSLATOR: [row_label, option_menu, status_label, cfg_button]
    }
    shim._status_labels = {LayerKind.TRANSLATOR: status_label}
    shim._status_overridden = set()
    return shim, row_label, status_label, option_menu, cfg_button


class TestAbsorbVisual:
    def test_absorbed_layer_clears_status_text(self, stub_panel) -> None:
        """吸収中のステータス欄は空表示(無効化されたプルダウンで伝わるため文言なし)。"""
        shim, row_label, status_label, _, _ = stub_panel
        shim._controller.get_absorbed_roles.return_value = {
            LayerKind.TRANSLATOR: LayerKind.ASR
        }

        shim._apply_absorbed_visuals()

        status_label.configure.assert_called_with(
            text="", text_color=DISABLED_TEXT,
        )
        row_label.configure.assert_called_with(text_color=DISABLED_TEXT)
        assert shim._status_overridden == {LayerKind.TRANSLATOR}

    def test_absorbed_layer_disables_option_menu_and_button(self, stub_panel) -> None:
        """吸収中はコンボボックスと設定ボタンを disabled にする(選択値は保持)。"""
        shim, _, _, option_menu, cfg_button = stub_panel
        shim._controller.get_absorbed_roles.return_value = {
            LayerKind.TRANSLATOR: LayerKind.ASR
        }
        shim._controller.get_setting.return_value = "faster_whisper_translate"

        shim._apply_absorbed_visuals()

        option_menu.configure.assert_called_with(state="disabled")
        cfg_button.configure.assert_called_with(state="disabled")


class TestSkippedVisual:
    def test_text_only_marks_tts_output_as_none(self, stub_panel) -> None:
        """text_only では TTS / Output のステータス欄が「(なし)」になる。"""
        shim, _, _, _, _ = stub_panel
        shim._controller.get_absorbed_roles.return_value = {}
        shim._controller.output_mode = "text_only"
        tts_label = MagicMock(spec=ctk.CTkLabel, name="tts_status")
        out_label = MagicMock(spec=ctk.CTkLabel, name="out_status")
        shim._status_labels = {LayerKind.TTS: tts_label, LayerKind.OUTPUT: out_label}
        shim._backend_rows = {}

        shim._apply_absorbed_visuals()

        tts_label.configure.assert_called_with(
            text=SKIPPED_STATUS_TEXT, text_color=DISABLED_TEXT
        )
        out_label.configure.assert_called_with(
            text=SKIPPED_STATUS_TEXT, text_color=DISABLED_TEXT
        )
        assert shim._status_overridden == {LayerKind.TTS, LayerKind.OUTPUT}


class TestTtsNoneVisualScope:
    """_apply_tts_none_visual はステータスラベルに触らない(状態色を消さない)。"""

    def _shim_with_rows(self, *, tts_setting: str):
        from voice_translator.gui.settings_panel import SettingsPanel

        shim = MagicMock(spec=SettingsPanel)
        _bind(shim, "_apply_tts_none_visual", "_restore_text_color", "_interactive_state")
        shim._controller = MagicMock(name="controller")
        shim._controller.get_setting.return_value = tts_setting
        shim._controller.is_running = False
        shim._default_row_text_color = _DEFAULT_COLOR

        out_row_label = MagicMock(spec=ctk.CTkLabel, name="out_row_label")
        out_status = MagicMock(spec=ctk.CTkLabel, name="out_status")
        out_option = MagicMock(spec=ctk.CTkOptionMenu, name="out_option")
        tts_row_label = MagicMock(spec=ctk.CTkLabel, name="tts_row_label")
        tts_status = MagicMock(spec=ctk.CTkLabel, name="tts_status")
        shim._backend_rows = {
            LayerKind.OUTPUT: [out_row_label, out_option, out_status],
            LayerKind.TTS: [tts_row_label, tts_status],
        }
        shim._status_labels = {
            LayerKind.OUTPUT: out_status, LayerKind.TTS: tts_status,
        }
        return shim, out_row_label, out_status, out_option, tts_status

    def test_status_labels_untouched_on_restore(self) -> None:
        """TTS=実 backend のとき: 行ラベル色と widget 状態は戻すが、ステータス欄は触らない
        (直前に _apply_status が塗った Loaded(緑)等の状態色を消さないため)。"""
        shim, out_row_label, out_status, out_option, tts_status = (
            self._shim_with_rows(tts_setting="sapi")
        )

        shim._apply_tts_none_visual()

        out_status.configure.assert_not_called()
        tts_status.configure.assert_not_called()
        out_row_label.configure.assert_called_with(text_color=_DEFAULT_COLOR)
        out_option.configure.assert_called_with(state="normal")

    def test_status_labels_untouched_on_none(self) -> None:
        """TTS=(なし) のときも同様(「(なし)」表示は編成表示側の管轄)。"""
        shim, out_row_label, out_status, out_option, tts_status = (
            self._shim_with_rows(tts_setting="none")
        )

        shim._apply_tts_none_visual()

        out_status.configure.assert_not_called()
        tts_status.configure.assert_not_called()
        out_row_label.configure.assert_called_with(text_color=DISABLED_TEXT)
        out_option.configure.assert_called_with(state="disabled")


class TestRestoreVisual:
    def test_unabsorbed_layer_restores_default_color_not_none(self, stub_panel) -> None:
        """復帰時は保存済み既定色で戻す(None を渡すと ctk が拒否して残留する)。"""
        shim, row_label, status_label, _, _ = stub_panel
        shim._status_overridden = {LayerKind.TRANSLATOR}
        shim._controller.get_absorbed_roles.return_value = {}
        shim._controller.get_model_status.return_value = ModelStatus.LOADED

        shim._apply_absorbed_visuals()

        # 行ラベルは既定色で復元(None は使わない)
        row_label.configure.assert_called_with(text_color=_DEFAULT_COLOR)
        for call in row_label.configure.call_args_list:
            assert call.kwargs.get("text_color") is not None
        # ステータス欄は実状態の再描画に委譲される
        shim._apply_status.assert_called_once_with(
            LayerKind.TRANSLATOR, ModelStatus.LOADED
        )
        assert shim._status_overridden == set()

    def test_unabsorbed_layer_reenables_option_menu_and_button(
        self, stub_panel,
    ) -> None:
        """復帰時はコンボボックスと設定ボタンを normal に戻す。"""
        shim, _, _, option_menu, cfg_button = stub_panel
        shim._status_overridden = {LayerKind.TRANSLATOR}
        shim._controller.get_absorbed_roles.return_value = {}
        shim._controller.get_model_status.return_value = ModelStatus.LOADED

        shim._apply_absorbed_visuals()

        option_menu.configure.assert_called_with(state="normal")
        cfg_button.configure.assert_called_with(state="normal")
        # TTS=(なし) 連動の再適用も走る(復帰直後の表示崩れ防止)
        shim._apply_tts_none_visual.assert_called_once()
