"""入力 backend のデバイス単位分解(P5)のテスト。

- `_refresh_capture_sources_dropdown` が新 backend のソース一覧で更新される
- output 側に影響しない(独立)
- `_on_backend_change(CAPTURE, ...)` で capture プルダウンが自動 refresh
- 既存 source_id を保持 / 非対応なら fallback + ConfigStore 更新
- list_capture_sources が例外でも UI が壊れない
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from voice_translator.common.types import CaptureSource, LayerKind, ModelStatus, OutputDevice


def _make_root():
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
    sources: list[CaptureSource] | None = None,
    devices: list[OutputDevice] | None = None,
    current_input: str | None = None,
    current_output: str | None = None,
    capture_backend_name: str = "soundcard",
) -> MagicMock:
    """SettingsPanel に注入する controller モック。

    `list_capture_sources` / `list_output_devices` を任意に差し替えて UI 連動を観察する。
    """
    ctrl = MagicMock()

    def get_setting(*keys, default=None):
        if keys == ("backends", "capture"):
            return capture_backend_name
        if keys == ("backends", "tts"):
            return "sapi"
        if keys[0] == "backends":
            return ""
        if keys[0] == "languages":
            return default if default is not None else "auto"
        if keys[0] == "log" and len(keys) > 1 and keys[1] == "directory":
            return "./logs"
        if keys == ("devices", "input"):
            return current_input
        if keys == ("devices", "output"):
            return current_output
        if keys[0] == "devices":
            return None
        if keys[0] == "ui":
            return False  # 開
        return default

    ctrl.get_setting.side_effect = get_setting
    ctrl.list_backends.side_effect = lambda layer: (
        ["soundcard", "proctap"] if layer == LayerKind.CAPTURE else ["(未登録)"]
    )
    ctrl.list_capture_sources.return_value = sources or []
    ctrl.list_output_devices.return_value = devices or []
    ctrl.get_all_model_statuses.return_value = {
        layer: ModelStatus.INIT for layer in LayerKind
    }
    ctrl.get_supported_input_languages.return_value = []
    ctrl.get_supported_target_languages.return_value = []
    ctrl.get_supported_output_languages.return_value = []
    ctrl.supports_auto_detect.return_value = False
    ctrl.get_layer_device.return_value = None
    ctrl.get_backend_capability_hint.return_value = None
    ctrl.is_running = False
    return ctrl


class TestRefreshCaptureSourcesDropdown:
    def test_lists_current_backend_sources(self, root) -> None:
        from voice_translator.gui.settings_panel import SettingsPanel

        ctrl = _make_controller(
            sources=[
                CaptureSource("id-a", "Mic A"),
                CaptureSource("id-b", "Mic B"),
            ],
            current_input="id-a",
        )
        panel = SettingsPanel(root, ctrl)
        # 初期化時に呼ばれている: dropdown には Mic A / Mic B が並ぶ
        values = panel._capture_dropdown.cget("values")  # noqa: SLF001
        assert "Mic A" in values
        assert "Mic B" in values

    def test_keeps_existing_source_id(self, root) -> None:
        from voice_translator.gui.settings_panel import SettingsPanel

        ctrl = _make_controller(
            sources=[
                CaptureSource("id-a", "Mic A"),
                CaptureSource("id-b", "Mic B"),
            ],
            current_input="id-b",
        )
        panel = SettingsPanel(root, ctrl)
        assert panel._capture_var.get() == "Mic B"  # noqa: SLF001
        # set_setting("devices","input",...) は呼ばれていない(既存値保持)
        for call in ctrl.set_setting.call_args_list:
            assert call.args[:2] != ("devices", "input")

    def test_falls_back_when_existing_id_missing(self, root) -> None:
        """新 backend のソース一覧に旧 source_id が無いと先頭にフォールバックし ConfigStore も更新。"""
        from voice_translator.gui.settings_panel import SettingsPanel

        ctrl = _make_controller(
            sources=[
                CaptureSource("id-a", "Mic A"),
                CaptureSource("id-b", "Mic B"),
            ],
            current_input="not-existing",
        )
        panel = SettingsPanel(root, ctrl)
        assert panel._capture_var.get() == "Mic A"  # noqa: SLF001
        ctrl.set_setting.assert_any_call("devices", "input", "id-a")

    def test_handles_empty_sources(self, root) -> None:
        from voice_translator.gui.settings_panel import SettingsPanel

        ctrl = _make_controller(sources=[])
        panel = SettingsPanel(root, ctrl)
        values = panel._capture_dropdown.cget("values")  # noqa: SLF001
        assert list(values) == ["(入力デバイスなし)"]
        assert panel._capture_id_map == {}  # noqa: SLF001

    def test_handles_exception(self, root) -> None:
        from voice_translator.gui.settings_panel import SettingsPanel

        ctrl = _make_controller()
        ctrl.list_capture_sources.side_effect = RuntimeError("device API down")
        panel = SettingsPanel(root, ctrl)
        values = panel._capture_dropdown.cget("values")  # noqa: SLF001
        # 「(取得失敗: ...)」表示で UI が落ちない
        assert any("取得失敗" in v for v in values)
        assert panel._capture_id_map == {}  # noqa: SLF001


class TestRefreshIsIndependent:
    """capture / output の refresh が互いに干渉しない。"""

    def test_capture_refresh_does_not_touch_output(self, root) -> None:
        from voice_translator.gui.settings_panel import SettingsPanel

        ctrl = _make_controller(
            sources=[CaptureSource("c1", "Cap A")],
            devices=[OutputDevice("o1", "Out X")],
            current_input="c1",
            current_output="o1",
        )
        panel = SettingsPanel(root, ctrl)
        ctrl.list_capture_sources.reset_mock()
        ctrl.list_output_devices.reset_mock()
        panel._refresh_capture_sources_dropdown()  # noqa: SLF001
        ctrl.list_capture_sources.assert_called_once()
        ctrl.list_output_devices.assert_not_called()

    def test_output_refresh_does_not_touch_capture(self, root) -> None:
        from voice_translator.gui.settings_panel import SettingsPanel

        ctrl = _make_controller(
            sources=[CaptureSource("c1", "Cap A")],
            devices=[OutputDevice("o1", "Out X")],
            current_input="c1",
            current_output="o1",
        )
        panel = SettingsPanel(root, ctrl)
        ctrl.list_capture_sources.reset_mock()
        ctrl.list_output_devices.reset_mock()
        panel._refresh_output_devices_dropdown()  # noqa: SLF001
        ctrl.list_output_devices.assert_called_once()
        ctrl.list_capture_sources.assert_not_called()


class TestOnBackendChangeCaptureRefresh:
    """CAPTURE backend 切替時にソース一覧が自動 refresh される。"""

    def test_capture_backend_change_refreshes_sources(self, root) -> None:
        from voice_translator.gui.settings_panel import SettingsPanel

        # 起動時は soundcard 想定で 1 件、切替後 proctap で別の 2 件
        soundcard_sources = [CaptureSource("sc-1", "Speakers LB")]
        proctap_sources = [
            CaptureSource("pid-1234", "chrome.exe (1234)"),
            CaptureSource("pid-5678", "discord.exe (5678)"),
        ]
        ctrl = _make_controller(
            sources=soundcard_sources,
            current_input="sc-1",
            capture_backend_name="soundcard",
        )
        panel = SettingsPanel(root, ctrl)
        # 初期は Speakers LB
        assert panel._capture_var.get() == "Speakers LB"  # noqa: SLF001

        # 切替後の挙動: list_capture_sources は新 backend のソースを返すモック差替
        ctrl.list_capture_sources.return_value = proctap_sources
        # backends.capture も新値を返すように get_setting を更新
        original = ctrl.get_setting.side_effect

        def new_get_setting(*keys, default=None):
            if keys == ("backends", "capture"):
                return "proctap"
            if keys == ("devices", "input"):
                return "sc-1"  # 既存値(新 backend には無い)
            return original(*keys, default=default)

        ctrl.get_setting.side_effect = new_get_setting

        panel._on_backend_change(LayerKind.CAPTURE, "proctap")  # noqa: SLF001

        # 新ソース一覧が dropdown に反映
        values = panel._capture_dropdown.cget("values")  # noqa: SLF001
        assert "chrome.exe (1234)" in values
        assert "discord.exe (5678)" in values
        # 旧 source_id "sc-1" は新一覧にないので先頭(chrome)に fallback
        assert panel._capture_var.get() == "chrome.exe (1234)"  # noqa: SLF001
        ctrl.set_setting.assert_any_call("devices", "input", "pid-1234")

    def test_non_capture_backend_change_does_not_refresh_capture(self, root) -> None:
        """他レイヤの backend 切替では capture refresh は走らない。"""
        from voice_translator.gui.settings_panel import SettingsPanel

        ctrl = _make_controller(
            sources=[CaptureSource("c1", "Cap A")],
            current_input="c1",
        )
        panel = SettingsPanel(root, ctrl)
        ctrl.list_capture_sources.reset_mock()
        # ASR backend 切替
        panel._on_backend_change(LayerKind.ASR, "openai_whisper")  # noqa: SLF001
        ctrl.list_capture_sources.assert_not_called()
