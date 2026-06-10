"""SettingsPanel の TTS=(なし) 連動テスト(refactor/text-only-via-tts-none)。

- `_tts_display_to_internal` / `_tts_internal_to_display` の変換
- 起動時に `backends.tts = "none"` だと StringVar 初期値が `(なし)`
- TTS プルダウンの選択肢に `(なし)` が末尾追加される
- `(なし)` 選択時に controller.set_setting に `"none"` が渡る
- `(なし)` 選択は同意ダイアログ(クラウド gate)をスキップ
- Output 行は `(なし)` 時に disable される(widget state を確認)
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


def _make_controller(tts_choice: str = "sapi") -> MagicMock:
    """SettingsPanel に注入する AppController モック。"""
    from voice_translator.common.types import LayerKind, ModelStatus

    controller = MagicMock()

    def get_setting(*keys, default=None):
        if keys == ("backends", "tts"):
            return tts_choice
        if keys[0] == "backends":
            return ""
        if keys[0] == "languages":
            return default if default is not None else "auto"
        if keys[0] == "log" and len(keys) > 1 and keys[1] == "directory":
            return "./logs"
        if keys[0] == "devices":
            return None
        if keys[0] == "ui":
            return False  # 開
        return default

    controller.get_setting.side_effect = get_setting

    def list_backends(layer):
        if layer == LayerKind.TTS:
            return ["sapi", "piper"]
        return ["(未登録)"]

    controller.list_backends.side_effect = list_backends
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
    return controller


class TestHelpers:
    """表示↔内部値の変換関数。

    P1: 変換関数は gui/logic/backend_display.py へ移動(詳細テストは
    tests/test_logic_backend_display.py)。ここでは移動先の関数が
    settings_panel から従来どおり使われている前提の代表ケースのみ残す。
    """

    def test_display_to_internal_for_none(self) -> None:
        from voice_translator.gui.logic.backend_display import tts_display_to_internal

        assert tts_display_to_internal("(なし)") == "none"

    def test_display_to_internal_for_real_backend(self) -> None:
        from voice_translator.gui.logic.backend_display import tts_display_to_internal

        assert tts_display_to_internal("sapi") == "sapi"
        assert tts_display_to_internal("piper") == "piper"

    def test_internal_to_display_for_none(self) -> None:
        from voice_translator.gui.logic.backend_display import tts_internal_to_display

        assert tts_internal_to_display("none") == "(なし)"

    def test_internal_to_display_for_real_backend(self) -> None:
        from voice_translator.gui.logic.backend_display import tts_internal_to_display

        assert tts_internal_to_display("sapi") == "sapi"


class TestTtsDropdownChoices:
    """TTS プルダウンに `(なし)` が末尾追加される。"""

    def test_tts_dropdown_includes_none_choice(self, root) -> None:
        from voice_translator.common.types import LayerKind
        from voice_translator.gui.settings_panel import SettingsPanel

        panel = SettingsPanel(root, _make_controller())
        # _backend_rows[TTS] の 2 番目要素が CTkOptionMenu
        tts_widgets = panel._backend_rows[LayerKind.TTS]  # noqa: SLF001
        # _make_controller の list_backends(TTS) = ["sapi", "piper"] + ["(なし)"]
        import customtkinter as ctk
        dropdown = next(w for w in tts_widgets if isinstance(w, ctk.CTkOptionMenu))
        values = dropdown.cget("values")
        assert "sapi" in values
        assert "piper" in values
        assert "(なし)" in values
        # (なし) が末尾に置かれる(実装上の選択順)
        assert values[-1] == "(なし)"


class TestInitialDisplay:
    """起動時の StringVar 初期値が内部値→表示値に変換される。"""

    def test_initial_value_for_sapi(self, root) -> None:
        from voice_translator.common.types import LayerKind
        from voice_translator.gui.settings_panel import SettingsPanel

        panel = SettingsPanel(root, _make_controller(tts_choice="sapi"))
        assert panel._backend_vars[LayerKind.TTS].get() == "sapi"  # noqa: SLF001

    def test_initial_value_for_none(self, root) -> None:
        """内部値 "none" だと StringVar には `(なし)` が入る。"""
        from voice_translator.common.types import LayerKind
        from voice_translator.gui.settings_panel import SettingsPanel

        panel = SettingsPanel(root, _make_controller(tts_choice="none"))
        assert panel._backend_vars[LayerKind.TTS].get() == "(なし)"  # noqa: SLF001


class TestOnBackendChange:
    """`(なし)` 選択時の挙動。"""

    def test_selecting_none_saves_internal_none(self, root) -> None:
        from voice_translator.common.types import LayerKind
        from voice_translator.gui.settings_panel import SettingsPanel

        controller = _make_controller(tts_choice="sapi")
        panel = SettingsPanel(root, controller)
        panel._on_backend_change(LayerKind.TTS, "(なし)")  # noqa: SLF001
        # set_setting に内部値 "none" が渡る
        controller.set_setting.assert_any_call("backends", "tts", "none")

    def test_selecting_none_skips_cloud_consent(self, root) -> None:
        """`(なし)` 選択時はクラウド同意ダイアログを通さない。"""
        from voice_translator.common.types import LayerKind
        from voice_translator.gui.settings_panel import SettingsPanel

        controller = _make_controller(tts_choice="sapi")
        panel = SettingsPanel(root, controller)
        # _gate_cloud_consent をスパイ化(返り値が呼ばれたか観察)
        panel._gate_cloud_consent = MagicMock(return_value=False)  # noqa: SLF001
        panel._on_backend_change(LayerKind.TTS, "(なし)")  # noqa: SLF001
        # 呼ばれない = ガード対象外として保存に進む
        panel._gate_cloud_consent.assert_not_called()  # noqa: SLF001
        controller.set_setting.assert_any_call("backends", "tts", "none")

    def test_selecting_real_backend_goes_through_consent(self, root) -> None:
        from voice_translator.common.types import LayerKind
        from voice_translator.gui.settings_panel import SettingsPanel

        controller = _make_controller(tts_choice="sapi")
        panel = SettingsPanel(root, controller)
        panel._gate_cloud_consent = MagicMock(return_value=True)  # noqa: SLF001
        panel._on_backend_change(LayerKind.TTS, "piper")  # noqa: SLF001
        panel._gate_cloud_consent.assert_called_once()  # noqa: SLF001
        controller.set_setting.assert_any_call("backends", "tts", "piper")


class TestOutputRowGreyedOutByTtsNone:
    """TTS=(なし) のとき Output 行が disable される。"""

    def test_output_dropdown_disabled_when_tts_none(self, root) -> None:
        import customtkinter as ctk
        from voice_translator.common.types import LayerKind
        from voice_translator.gui.settings_panel import SettingsPanel

        panel = SettingsPanel(root, _make_controller(tts_choice="none"))
        # Output 行の dropdown / 設定ボタンが disabled
        output_widgets = panel._backend_rows[LayerKind.OUTPUT]  # noqa: SLF001
        for w in output_widgets:
            if isinstance(w, (ctk.CTkOptionMenu, ctk.CTkButton)):
                assert str(w.cget("state")) == "disabled"

    def test_output_dropdown_enabled_when_tts_real(self, root) -> None:
        import customtkinter as ctk
        from voice_translator.common.types import LayerKind
        from voice_translator.gui.settings_panel import SettingsPanel

        panel = SettingsPanel(root, _make_controller(tts_choice="sapi"))
        output_widgets = panel._backend_rows[LayerKind.OUTPUT]  # noqa: SLF001
        for w in output_widgets:
            if isinstance(w, (ctk.CTkOptionMenu, ctk.CTkButton)):
                assert str(w.cget("state")) == "normal"

    def test_switching_to_none_disables_output_row(self, root) -> None:
        """sapi → (なし) に切替で Output 行が disable に切り替わる。"""
        import customtkinter as ctk
        from voice_translator.common.types import LayerKind
        from voice_translator.gui.settings_panel import SettingsPanel

        controller = _make_controller(tts_choice="sapi")
        panel = SettingsPanel(root, controller)
        # 切替後の get_setting で "none" を返すよう側面を更新
        original_side_effect = controller.get_setting.side_effect

        def new_get_setting(*keys, default=None):
            if keys == ("backends", "tts"):
                return "none"
            return original_side_effect(*keys, default=default)

        controller.get_setting.side_effect = new_get_setting
        panel._on_backend_change(LayerKind.TTS, "(なし)")  # noqa: SLF001
        output_widgets = panel._backend_rows[LayerKind.OUTPUT]  # noqa: SLF001
        for w in output_widgets:
            if isinstance(w, (ctk.CTkOptionMenu, ctk.CTkButton)):
                assert str(w.cget("state")) == "disabled"
