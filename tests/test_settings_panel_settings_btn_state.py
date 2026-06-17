"""SettingsPanel 設定ボタン enabled/disabled 配線の smoke テスト。

確認する契約:
- visible_fields() が空リストを返す backend(silero/mms)の設定ボタンは
  パネル構築時から disabled になる
- backend を「設定なし」→「設定あり」に切り替えたとき設定ボタンが normal になる
- TTS を (なし) から mms に変更したとき設定ボタンは disabled のまま
  (designReview 観点5: _apply_tts_none_visual と _sync_settings_btn_state の
   呼び出し順序依存のリグレッション防止)

ヘッドレス環境では pytest.skip する(既存パターンと同様)。
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest


def _make_root():
    """customtkinter のルート(ヘッドレス環境では skip)。"""
    import customtkinter as ctk

    try:
        root = ctk.CTk()
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"GUI 表示環境が無いため skip: {e}")
    root.withdraw()
    return root


@pytest.fixture()
def root():
    r = _make_root()
    yield r
    try:
        r.destroy()
    except Exception:  # noqa: BLE001
        pass


def _make_controller(
    *,
    vad_choice: str = "silero",
    tts_choice: str = "mms",
) -> MagicMock:
    """SettingsPanel に注入する AppController モック。

    vad_choice / tts_choice は get_setting の初期応答に使い、
    set_setting で更新できるよう _settings 辞書で管理する。
    """
    from voice_translator.common.types import LayerKind, ModelStatus

    _settings: dict[tuple, str] = {
        ("backends", "vad"): vad_choice,
        ("backends", "tts"): tts_choice,
    }

    controller = MagicMock()

    def get_setting(*keys, default=None):
        # backends.vad / backends.tts は更新可能な辞書から返す
        if keys[0] == "backends" and len(keys) >= 2:
            key = ("backends", keys[1])
            if key in _settings:
                return _settings[key]
            return default if default is not None else ""
        if keys[0] == "languages":
            return default if default is not None else "auto"
        if keys[0] == "log" and len(keys) > 1 and keys[1] == "directory":
            return "./logs"
        if keys[0] == "devices":
            return None
        if keys[0] == "ui":
            return False  # 開
        return default

    def set_setting(*keys, **kwargs):
        # set_setting("backends", layer.value, internal_value) の形で呼ばれる
        if keys[0] == "backends" and len(keys) >= 3:
            _settings[("backends", keys[1])] = keys[2]

    controller.get_setting.side_effect = get_setting
    controller.set_setting.side_effect = set_setting

    def list_backends(layer):
        if layer == LayerKind.VAD:
            return ["silero", "webrtcvad"]
        if layer == LayerKind.TTS:
            return ["mms", "sapi"]
        if layer == LayerKind.CAPTURE:
            return ["soundcard"]
        if layer == LayerKind.OUTPUT:
            return ["soundcard"]
        return ["faster_whisper"]

    controller.list_backends.side_effect = list_backends
    controller.catalog.is_backend_available.return_value = True
    controller.list_capture_sources.return_value = []
    controller.list_output_devices.return_value = []
    controller.get_all_model_statuses.return_value = {
        layer: ModelStatus.INIT for layer in LayerKind
    }
    controller.get_supported_input_languages.return_value = []
    controller.get_supported_target_languages.return_value = []
    controller.get_supported_output_languages.return_value = []
    controller.supports_auto_detect.return_value = False
    controller.get_layer_device.return_value = None
    controller.get_backend_capability_hint.return_value = None
    controller.get_absorbed_roles.return_value = {}
    controller.output_mode = "audio"
    controller.is_running = False

    return controller


class TestSettingsBtnInitialState:
    """構築時の設定ボタン状態。"""

    def test_vad_silero_initial_disabled(self, root) -> None:
        """silero は設定項目ゼロ → 構築後に設定ボタンが disabled。"""
        from voice_translator.common.types import LayerKind
        from voice_translator.gui.settings_panel import SettingsPanel

        panel = SettingsPanel(root, _make_controller(vad_choice="silero"))
        btn = panel._settings_btns[LayerKind.VAD]  # noqa: SLF001
        assert str(btn.cget("state")) == "disabled"

    def test_tts_mms_initial_disabled(self, root) -> None:
        """mms は設定項目ゼロ → 構築後に設定ボタンが disabled。"""
        from voice_translator.common.types import LayerKind
        from voice_translator.gui.settings_panel import SettingsPanel

        panel = SettingsPanel(root, _make_controller(tts_choice="mms"))
        btn = panel._settings_btns[LayerKind.TTS]  # noqa: SLF001
        assert str(btn.cget("state")) == "disabled"


class TestSettingsBtnOnBackendChange:
    """backend 切替後の設定ボタン状態。"""

    def test_vad_change_to_webrtcvad_enables_btn(self, root) -> None:
        """VAD を silero → webrtcvad に変更すると設定ボタンが normal になる。"""
        from voice_translator.common.types import LayerKind
        from voice_translator.gui.settings_panel import SettingsPanel

        controller = _make_controller(vad_choice="silero")
        panel = SettingsPanel(root, controller)
        btn = panel._settings_btns[LayerKind.VAD]  # noqa: SLF001
        # 初期は disabled
        assert str(btn.cget("state")) == "disabled"

        # webrtcvad に変更
        panel._on_backend_change(LayerKind.VAD, "webrtcvad")  # noqa: SLF001

        assert str(btn.cget("state")) == "normal"

    def test_tts_none_to_mms_keeps_disabled(self, root) -> None:
        """TTS を (なし) から mms に変更しても設定ボタンは disabled のまま。

        designReview 観点5: _apply_tts_none_visual(TTS btn を normal 化するタイミングがある)の
        後に _sync_settings_btn_state が呼ばれることで disabled を保つことを確認する。
        """
        from voice_translator.common.types import LayerKind
        from voice_translator.gui.settings_panel import SettingsPanel

        # TTS = (なし) で構築
        controller = _make_controller(tts_choice="none")
        panel = SettingsPanel(root, controller)
        btn = panel._settings_btns[LayerKind.TTS]  # noqa: SLF001
        # TTS=(なし) では _apply_tts_none_visual が TTS 行の設定ボタンを disabled にする
        assert str(btn.cget("state")) == "disabled"

        # mms に変更(has_settings(TTS, "mms") == False なので disabled のまま)
        panel._on_backend_change(LayerKind.TTS, "mms")  # noqa: SLF001

        assert str(btn.cget("state")) == "disabled"
