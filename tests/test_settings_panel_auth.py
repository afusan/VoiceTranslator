"""SettingsPanel の認証状態上書き表示(_apply_status の auth 連動)の配線テスト(shim 方式)。

固定する契約:
- 認証未完了(MISSING / UNVERIFIED)はインスタンス状態(Init / Loaded)より優先して
  ステータス欄に出す(「Loaded なのに Start で認証エラー」の矛盾防止)
- 認証不要 / 検証済みは従来の ModelStatus 表示
- 編成表示(吸収 / なし)の上書き(_status_overridden)はさらに優先
- controller 問い合わせの失敗は NOT_REQUIRED に縮退(表示を壊さない)
"""

from __future__ import annotations

from unittest.mock import MagicMock

import customtkinter as ctk
import pytest

from voice_translator.common.types import AuthState, LayerKind, ModelStatus
from voice_translator.gui.logic.auth_display import (
    AUTH_MISSING_TEXT,
    AUTH_UNVERIFIED_TEXT,
)
from voice_translator.gui.logic.palette import (
    AUTH_MISSING_COLOR,
    AUTH_UNVERIFIED_COLOR,
    STATUS_COLORS,
)


def _bind(shim, *method_names: str):
    from voice_translator.gui.settings_panel import SettingsPanel

    for name in method_names:
        setattr(shim, name, getattr(SettingsPanel, name).__get__(shim))


@pytest.fixture()
def stub_panel():
    from voice_translator.gui.settings_panel import SettingsPanel

    shim = MagicMock(spec=SettingsPanel)
    _bind(shim, "_apply_status", "_get_auth_state", "_on_settings_event")
    shim._controller = MagicMock(name="controller")
    shim._controller.get_auth_state.return_value = AuthState.NOT_REQUIRED
    shim._controller.get_layer_device.return_value = None
    shim._format_status_text = (
        lambda layer, status: status.value
    )
    status_label = MagicMock(spec=ctk.CTkLabel, name="status_label")
    shim._status_labels = {LayerKind.ASR: status_label}
    shim._status_overridden = set()
    return shim, status_label


class TestAuthOverrideInStatusLabel:
    def test_missing_overrides_init(self, stub_panel) -> None:
        """未ロード(Init)でも鍵未入力なら Missing Credentials(赤)。"""
        shim, label = stub_panel
        shim._controller.get_auth_state.return_value = AuthState.MISSING

        shim._apply_status(LayerKind.ASR, ModelStatus.INIT)

        label.configure.assert_called_with(
            text=AUTH_MISSING_TEXT, text_color=AUTH_MISSING_COLOR,
        )

    def test_unverified_overrides_loaded(self, stub_panel) -> None:
        """鍵あり・未検証は Loaded でも Not Verified(琥珀)。"""
        shim, label = stub_panel
        shim._controller.get_auth_state.return_value = AuthState.UNVERIFIED

        shim._apply_status(LayerKind.ASR, ModelStatus.LOADED)

        label.configure.assert_called_with(
            text=AUTH_UNVERIFIED_TEXT, text_color=AUTH_UNVERIFIED_COLOR,
        )

    def test_verified_falls_through_to_normal_status(self, stub_panel) -> None:
        shim, label = stub_panel
        shim._controller.get_auth_state.return_value = AuthState.VERIFIED

        shim._apply_status(LayerKind.ASR, ModelStatus.LOADED)

        label.configure.assert_called_with(
            text=ModelStatus.LOADED.value,
            text_color=STATUS_COLORS[ModelStatus.LOADED],
        )

    def test_status_overridden_takes_priority_over_auth(self, stub_panel) -> None:
        """編成表示(吸収 / なし)中は auth 上書きも実状態も描画しない。"""
        shim, label = stub_panel
        shim._controller.get_auth_state.return_value = AuthState.MISSING
        shim._status_overridden = {LayerKind.ASR}

        shim._apply_status(LayerKind.ASR, ModelStatus.INIT)

        label.configure.assert_not_called()

    def test_controller_failure_degrades_to_normal_status(self, stub_panel) -> None:
        """controller 問い合わせ失敗は NOT_REQUIRED に縮退して通常表示。"""
        shim, label = stub_panel
        shim._controller.get_auth_state.side_effect = RuntimeError("boom")

        shim._apply_status(LayerKind.ASR, ModelStatus.INIT)

        label.configure.assert_called_with(
            text=ModelStatus.INIT.value,
            text_color=STATUS_COLORS[ModelStatus.INIT],
        )


class TestCredentialsEventWiring:
    def test_credentials_event_triggers_full_refresh(self, stub_panel) -> None:
        """("credentials", <backend>) イベントで全行の再描画を after 経由で予約する。"""
        shim, _ = stub_panel

        shim._on_settings_event(("credentials", "fake_cloud"))

        shim.after.assert_called_once()
        args = shim.after.call_args.args
        assert args[0] == 0
        assert args[1] == shim._sync_all_status_labels

    def test_other_settings_events_are_ignored(self, stub_panel) -> None:
        """backends / devices 等は status イベント経由で再描画されるため何もしない。"""
        shim, _ = stub_panel

        shim._on_settings_event(("backends", "asr"))
        shim._on_settings_event(("devices", "input"))

        shim.after.assert_not_called()
