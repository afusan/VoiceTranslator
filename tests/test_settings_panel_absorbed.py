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
from voice_translator.gui.logic.backend_display import (
    SKIPPED_STATUS_TEXT,
    absorbed_status_text,
)
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
        "_lead_backend_name",
        "_restore_text_color",
    )
    shim._controller = MagicMock(name="controller")
    shim._controller.output_mode = "audio"
    shim._default_row_text_color = _DEFAULT_COLOR

    row_label = MagicMock(spec=ctk.CTkLabel, name="row_label")
    status_label = MagicMock(spec=ctk.CTkLabel, name="status_label")
    shim._backend_rows = {LayerKind.TRANSLATOR: [row_label, status_label]}
    shim._status_labels = {LayerKind.TRANSLATOR: status_label}
    shim._status_overridden = set()
    return shim, row_label, status_label


class TestAbsorbVisual:
    def test_absorbed_layer_shows_effective_backend(self, stub_panel) -> None:
        """吸収中は「どの backend が実際に動くか」をステータス欄に出す。"""
        shim, row_label, status_label = stub_panel
        shim._controller.get_absorbed_roles.return_value = {
            LayerKind.TRANSLATOR: LayerKind.ASR
        }
        shim._controller.get_setting.return_value = "faster_whisper_translate"

        shim._apply_absorbed_visuals()

        status_label.configure.assert_called_with(
            text=absorbed_status_text(LayerKind.ASR, "faster_whisper_translate"),
            text_color=DISABLED_TEXT,
        )
        row_label.configure.assert_called_with(text_color=DISABLED_TEXT)
        assert shim._status_overridden == {LayerKind.TRANSLATOR}


class TestSkippedVisual:
    def test_text_only_marks_tts_output_as_none(self, stub_panel) -> None:
        """text_only では TTS / Output のステータス欄が「(なし)」になる。"""
        shim, _, _ = stub_panel
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


class TestRestoreVisual:
    def test_unabsorbed_layer_restores_default_color_not_none(self, stub_panel) -> None:
        """復帰時は保存済み既定色で戻す(None を渡すと ctk が拒否して残留する)。"""
        shim, row_label, status_label = stub_panel
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
