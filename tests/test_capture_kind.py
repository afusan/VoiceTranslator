"""CaptureKind 概念導入(ProcTap 取り込み段階 1)のテスト。

- CaptureSource に kind フィールドが追加され既定が DEVICE
- AudioCaptureBackend.capture_kind() 既定は DEVICE
- SoundcardCaptureBackend.capture_kind() = DEVICE
- SoundcardCaptureBackend.list_sources() の各 CaptureSource.kind = DEVICE
- AppController.get_capture_kind の挙動
- SettingsPanel の CAPTURE プルダウン表示が `<kind label> (<backend>)`
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from voice_translator.common.types import CaptureKind, CaptureSource


class TestCaptureKindEnum:
    def test_values(self) -> None:
        assert CaptureKind.DEVICE.value == "device"
        assert CaptureKind.PROCESS.value == "process"


class TestCaptureSourceKindField:
    def test_default_kind_is_device(self) -> None:
        s = CaptureSource(source_id="x", display_name="X")
        assert s.kind == CaptureKind.DEVICE

    def test_explicit_kind_process(self) -> None:
        s = CaptureSource(
            source_id="pid-1234", display_name="chrome (1234)",
            kind=CaptureKind.PROCESS,
        )
        assert s.kind == CaptureKind.PROCESS


class TestAudioCaptureBackendDefault:
    def test_default_capture_kind_is_device(self) -> None:
        """`AudioCaptureBackend.capture_kind()` の既定が DEVICE。

        サブクラスがオーバーライドしなくても従来挙動を維持できる(後方互換)。
        """
        from voice_translator.capture.backend import AudioCaptureBackend

        # 抽象基底だが classmethod は呼べる
        assert AudioCaptureBackend.capture_kind() == CaptureKind.DEVICE


class TestSoundcardBackendDeclaresDevice:
    def test_capture_kind_is_device(self) -> None:
        from voice_translator.capture.soundcard_backend import SoundcardCaptureBackend

        assert SoundcardCaptureBackend.capture_kind() == CaptureKind.DEVICE

    def test_list_sources_kind_is_device(self, monkeypatch) -> None:
        """list_sources の各 CaptureSource に kind=DEVICE が乗る。"""
        import voice_translator.capture.soundcard_backend as sc_module

        class _FakeMic:
            def __init__(self, mid: str, name: str, loopback: bool) -> None:
                self.id = mid
                self.name = name
                self.isloopback = loopback

        fake_mics = [
            _FakeMic("m1", "Microphone", False),
            _FakeMic("m2", "Speakers", True),
        ]
        monkeypatch.setattr(
            sc_module.sc, "all_microphones",
            lambda include_loopback=True: fake_mics,
        )

        backend = sc_module.SoundcardCaptureBackend()
        sources = backend.list_sources()
        assert len(sources) == 2
        for s in sources:
            assert s.kind == CaptureKind.DEVICE


class TestAppControllerGetCaptureKind:
    def _make_controller(self, *, registered: dict[str, type] | None = None):
        from voice_translator.common.app_controller import AppController
        from voice_translator.common.backend_registry import BackendRegistry
        from voice_translator.common.config_store import ConfigStore
        from voice_translator.common.types import LayerKind

        config = ConfigStore(path="dummy", data={})
        registry = BackendRegistry()
        if registered:
            for name, cls in registered.items():
                registry.register(
                    LayerKind.CAPTURE, name, lambda c=cls: c(), backend_cls=cls,
                )
        return AppController(registry=registry, config=config)

    def test_returns_kind_from_backend_class(self) -> None:
        from voice_translator.capture.soundcard_backend import SoundcardCaptureBackend

        ctrl = self._make_controller(registered={"soundcard": SoundcardCaptureBackend})
        assert ctrl.get_capture_kind("soundcard") == CaptureKind.DEVICE

    def test_unknown_backend_falls_back_to_device(self) -> None:
        ctrl = self._make_controller()
        assert ctrl.get_capture_kind("not-registered") == CaptureKind.DEVICE

    def test_exception_in_capture_kind_falls_back(self) -> None:
        from voice_translator.capture.backend import AudioCaptureBackend

        class BrokenBackend(AudioCaptureBackend):
            @classmethod
            def capture_kind(cls) -> CaptureKind:
                raise RuntimeError("boom")

            def list_sources(self):  # pragma: no cover
                return []

            def start(self, source_id: str) -> None:  # pragma: no cover
                pass

            def stop(self) -> None:  # pragma: no cover
                pass

            def read_chunk(self, timeout: float = 0.1):  # pragma: no cover
                return None

        ctrl = self._make_controller(registered={"broken": BrokenBackend})
        assert ctrl.get_capture_kind("broken") == CaptureKind.DEVICE


# ============================================================
# SettingsPanel: CAPTURE プルダウンの kind 主体表記
# ============================================================
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


def _make_controller_mock(
    *,
    capture_backends: list[str],
    current_capture: str,
    capture_kind_map: dict[str, CaptureKind] | None = None,
):
    """SettingsPanel に注入する controller モック(kind を含む)。"""
    from voice_translator.common.types import LayerKind, ModelStatus

    ctrl = MagicMock()
    capture_kind_map = capture_kind_map or {n: CaptureKind.DEVICE for n in capture_backends}

    def get_setting(*keys, default=None):
        if keys == ("backends", "capture"):
            return current_capture
        if keys == ("backends", "tts"):
            return "sapi"
        if keys[0] == "backends":
            return ""
        if keys[0] == "languages":
            return default if default is not None else "auto"
        if keys[0] == "log" and len(keys) > 1 and keys[1] == "directory":
            return "./logs"
        if keys[0] == "devices":
            return None
        if keys[0] == "ui":
            return False
        return default

    ctrl.get_setting.side_effect = get_setting
    ctrl.list_backends.side_effect = lambda layer: (
        capture_backends if layer == LayerKind.CAPTURE else ["(未登録)"]
    )
    ctrl.list_capture_sources.return_value = []
    ctrl.list_output_devices.return_value = []
    ctrl.get_all_model_statuses.return_value = {
        layer: ModelStatus.INIT for layer in LayerKind
    }
    ctrl.get_supported_input_languages.return_value = []
    ctrl.get_supported_target_languages.return_value = []
    ctrl.get_supported_output_languages.return_value = []
    ctrl.supports_auto_detect.return_value = False
    ctrl.get_layer_device.return_value = None
    ctrl.get_backend_capability_hint.return_value = None
    ctrl.get_capture_kind.side_effect = lambda name: capture_kind_map.get(
        name, CaptureKind.DEVICE,
    )
    ctrl.is_running = False
    return ctrl


class TestSettingsPanelCaptureDisplay:
    def test_dropdown_shows_kind_label_with_backend(self, root) -> None:
        """「音声取得」プルダウンに `デバイス (soundcard)` 形式が並ぶ。"""
        from voice_translator.common.types import LayerKind
        from voice_translator.gui.settings_panel import SettingsPanel

        ctrl = _make_controller_mock(
            capture_backends=["soundcard"],
            current_capture="soundcard",
        )
        panel = SettingsPanel(root, ctrl)
        # CAPTURE 行の dropdown を取り出して values を確認
        import customtkinter as ctk
        widgets = panel._backend_rows[LayerKind.CAPTURE]  # noqa: SLF001
        dropdown = next(w for w in widgets if isinstance(w, ctk.CTkOptionMenu))
        values = list(dropdown.cget("values"))
        assert values == ["デバイス (soundcard)"]
        # 初期 StringVar も同じ表示
        assert panel._backend_vars[LayerKind.CAPTURE].get() == "デバイス (soundcard)"  # noqa: SLF001

    def test_process_kind_is_labeled(self, root) -> None:
        """ProcTap 等 PROCESS kind の backend は「プロセス (proctap)」と表示される。"""
        from voice_translator.common.types import LayerKind
        from voice_translator.gui.settings_panel import SettingsPanel

        ctrl = _make_controller_mock(
            capture_backends=["soundcard", "proctap"],
            current_capture="proctap",
            capture_kind_map={
                "soundcard": CaptureKind.DEVICE,
                "proctap": CaptureKind.PROCESS,
            },
        )
        panel = SettingsPanel(root, ctrl)
        import customtkinter as ctk
        widgets = panel._backend_rows[LayerKind.CAPTURE]  # noqa: SLF001
        dropdown = next(w for w in widgets if isinstance(w, ctk.CTkOptionMenu))
        values = list(dropdown.cget("values"))
        assert "デバイス (soundcard)" in values
        assert "プロセス (proctap)" in values
        assert panel._backend_vars[LayerKind.CAPTURE].get() == "プロセス (proctap)"  # noqa: SLF001

    def test_selecting_display_saves_internal_name(self, root) -> None:
        """ユーザがプルダウンで `デバイス (soundcard)` を選ぶと set_setting に "soundcard" が渡る。"""
        from voice_translator.common.types import LayerKind
        from voice_translator.gui.settings_panel import SettingsPanel

        ctrl = _make_controller_mock(
            capture_backends=["soundcard", "proctap"],
            current_capture="soundcard",
            capture_kind_map={
                "soundcard": CaptureKind.DEVICE,
                "proctap": CaptureKind.PROCESS,
            },
        )
        panel = SettingsPanel(root, ctrl)
        panel._on_backend_change(LayerKind.CAPTURE, "プロセス (proctap)")  # noqa: SLF001
        ctrl.set_setting.assert_any_call("backends", "capture", "proctap")

    def test_helper_display_to_internal_extracts_backend_name(self) -> None:
        # P1: 変換関数は gui/logic/backend_display.py へ移動(詳細テストは
        # tests/test_logic_backend_display.py)
        from voice_translator.gui.logic.backend_display import (
            capture_display_to_internal,
        )

        assert capture_display_to_internal("デバイス (soundcard)") == "soundcard"
        assert capture_display_to_internal("プロセス (proctap)") == "proctap"
        # ネストカッコなど不正形式はそのまま返す(防衛)
        assert capture_display_to_internal("plain_backend") == "plain_backend"
