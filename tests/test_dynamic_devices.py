"""動作中デバイス変更(P4)のテスト。

- AppController.restart_pipeline_async の挙動:
  - 動作中で成功 → stop → start の順に呼ばれ on_restarted
  - 動作中でない → 即 on_restarted、stop/start は呼ばれない
  - stop 失敗 → on_failed、start は試みない
  - start 失敗 → on_failed
  - 多重起動 → 既走行中なら on_failed("既に再開中です")
- SettingsPanel のデバイス変更ハンドラ:
  - 動作中なら restart_pipeline_async を呼ぶ
  - 動作中でないなら呼ばない
  - 完了/失敗時のバナー表示
"""

from __future__ import annotations

import threading
import time
from unittest.mock import MagicMock

import pytest

from voice_translator.common.app_controller import AppController


# ============================================================
# AppController.restart_pipeline_async
# ============================================================
@pytest.fixture()
def stub_controller():
    """AppController を __init__ 経由せず、restart_pipeline_async だけ試せる shim。"""
    shim = MagicMock(spec=AppController)
    shim.restart_pipeline_async = AppController.restart_pipeline_async.__get__(shim)
    shim._logger = MagicMock(name="logger")
    return shim


class TestRestartPipelineAsync:
    def test_not_running_calls_on_restarted_immediately(self, stub_controller) -> None:
        """動作中でないときは即 on_restarted、stop/start は呼ばれない。"""
        stub_controller.is_running = False
        ev = threading.Event()
        stub_controller.restart_pipeline_async(on_restarted=ev.set)
        assert ev.wait(timeout=0.1)
        stub_controller.stop_pipeline.assert_not_called()
        stub_controller.start_pipeline.assert_not_called()

    def test_running_stops_then_starts(self, stub_controller) -> None:
        """動作中: stop → start の順で呼ばれ、on_restarted が呼ばれる。"""
        stub_controller.is_running = True
        calls: list[str] = []
        stub_controller.stop_pipeline.side_effect = lambda: calls.append("stop")
        stub_controller.start_pipeline.side_effect = lambda: calls.append("start")
        done = threading.Event()
        stub_controller.restart_pipeline_async(on_restarted=done.set)
        assert done.wait(timeout=2.0)
        assert calls == ["stop", "start"]

    def test_stop_failure_invokes_on_failed_and_skips_start(self, stub_controller) -> None:
        stub_controller.is_running = True
        stub_controller.stop_pipeline.side_effect = RuntimeError("boom")
        failed = threading.Event()
        captured: list[str] = []

        def _on_failed(msg: str) -> None:
            captured.append(msg)
            failed.set()

        stub_controller.restart_pipeline_async(
            on_restarted=lambda: pytest.fail("on_restarted は呼ばれてはならない"),
            on_failed=_on_failed,
        )
        assert failed.wait(timeout=2.0)
        assert "停止に失敗" in captured[0]
        stub_controller.start_pipeline.assert_not_called()

    def test_start_failure_invokes_on_failed(self, stub_controller) -> None:
        stub_controller.is_running = True
        stub_controller.start_pipeline.side_effect = RuntimeError("device validator")
        failed = threading.Event()
        captured: list[str] = []
        stub_controller.restart_pipeline_async(
            on_restarted=lambda: pytest.fail("on_restarted は呼ばれてはならない"),
            on_failed=lambda m: (captured.append(m), failed.set()),
        )
        assert failed.wait(timeout=2.0)
        assert "再開に失敗" in captured[0]
        stub_controller.stop_pipeline.assert_called_once()

    def test_concurrent_restart_is_rejected(self, stub_controller) -> None:
        """走行中の restart に対し、もう一度呼ぶと on_failed が即発火する。"""
        stub_controller.is_running = True
        # 1 回目はゆっくり動作させる
        gate = threading.Event()

        def _slow_stop() -> None:
            gate.wait(timeout=2.0)

        stub_controller.stop_pipeline.side_effect = _slow_stop
        stub_controller.start_pipeline.side_effect = lambda: None

        first_done = threading.Event()
        stub_controller.restart_pipeline_async(on_restarted=first_done.set)

        # 1 回目がまだ走っているうちに 2 回目を呼ぶ
        time.sleep(0.05)
        second_failed = threading.Event()
        captured: list[str] = []
        stub_controller.restart_pipeline_async(
            on_restarted=lambda: pytest.fail("2 回目の on_restarted は来ないはず"),
            on_failed=lambda m: (captured.append(m), second_failed.set()),
        )
        assert second_failed.wait(timeout=0.5)
        assert "既に再開中" in captured[0]

        # 1 回目を最後まで完了させる
        gate.set()
        assert first_done.wait(timeout=2.0)

    def test_default_callbacks_swallow(self, stub_controller) -> None:
        """callback を渡さなくても例外にならない(動作中でないケース)。"""
        stub_controller.is_running = False
        # 例外なし
        stub_controller.restart_pipeline_async()


# ============================================================
# SettingsPanel: デバイス変更ハンドラ → restart 連携
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


def _make_panel_controller(*, is_running: bool):
    """SettingsPanel に注入する controller モック。"""
    from voice_translator.common.types import LayerKind, ModelStatus

    ctrl = MagicMock()
    ctrl.is_running = is_running

    def get_setting(*keys, default=None):
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
        return default

    ctrl.get_setting.side_effect = get_setting
    ctrl.list_backends.return_value = ["(未登録)"]
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
    return ctrl


class TestSettingsPanelDeviceRestart:
    """P2: 自動 restart は AppController の set_setting 反応系に移管された。

    SettingsPanel のデバイス変更ハンドラは `set_setting` を書くだけで、
    restart_pipeline_async を直接呼ばない。バナーは restart イベントの購読で反映する。
    """

    def test_capture_change_writes_setting_without_direct_restart(self, root) -> None:
        from voice_translator.gui.settings_panel import SettingsPanel

        ctrl = _make_panel_controller(is_running=True)
        panel = SettingsPanel(root, ctrl)
        panel._capture_id_map = {"DevA": "id-a"}  # noqa: SLF001
        panel._on_capture_changed("DevA")  # noqa: SLF001
        ctrl.set_setting.assert_any_call("devices", "input", "id-a")
        # restart は controller 側の責務(SettingsPanel は直接呼ばない)
        ctrl.restart_pipeline_async.assert_not_called()

    def test_output_change_writes_setting_without_direct_restart(self, root) -> None:
        from voice_translator.gui.settings_panel import SettingsPanel

        ctrl = _make_panel_controller(is_running=True)
        panel = SettingsPanel(root, ctrl)
        panel._output_id_map = {"OutA": "out-a"}  # noqa: SLF001
        panel._on_output_changed("OutA")  # noqa: SLF001
        ctrl.set_setting.assert_any_call("devices", "output", "out-a")
        ctrl.restart_pipeline_async.assert_not_called()

    def test_panel_subscribes_restart_events(self, root) -> None:
        """__init__ で add_restart_listener を購読している。"""
        from voice_translator.gui.settings_panel import SettingsPanel

        ctrl = _make_panel_controller(is_running=False)
        SettingsPanel(root, ctrl)
        ctrl.add_restart_listener.assert_called_once()

    def test_set_control_panel_is_removed(self, root) -> None:
        """ControlPanel への逆参照注入窓は P2 で撤去済み。"""
        from voice_translator.gui.settings_panel import SettingsPanel

        ctrl = _make_panel_controller(is_running=False)
        panel = SettingsPanel(root, ctrl)
        assert not hasattr(panel, "set_control_panel")

    # ---- restart イベント → バナー反映 ----
    @staticmethod
    def _event(phase: str, device_key: str = "input", message: str = ""):
        from voice_translator.common.types import PipelineRestartEvent
        return PipelineRestartEvent(
            phase=phase, device_key=device_key, message=message
        )

    def test_started_event_shows_persistent_info_banner(self, root) -> None:
        from voice_translator.gui.settings_panel import SettingsPanel

        ctrl = _make_panel_controller(is_running=True)
        banner = MagicMock()
        panel = SettingsPanel(root, ctrl, banner=banner)
        panel._apply_restart_event(self._event("started", "input"))  # noqa: SLF001
        banner.show_info.assert_called_once()
        args, kwargs = banner.show_info.call_args
        msg = args[0] if args else kwargs.get("message", "")
        assert "入力" in msg
        assert "再開中" in msg
        # duration_ms=0(永続表示)
        assert kwargs.get("duration_ms") == 0

    def test_completed_event_dismisses_banner(self, root) -> None:
        from voice_translator.gui.settings_panel import SettingsPanel

        ctrl = _make_panel_controller(is_running=True)
        banner = MagicMock()
        panel = SettingsPanel(root, ctrl, banner=banner)
        panel._apply_restart_event(self._event("completed"))  # noqa: SLF001
        banner.dismiss.assert_called_once()

    def test_failed_event_shows_error(self, root) -> None:
        from voice_translator.gui.settings_panel import SettingsPanel

        ctrl = _make_panel_controller(is_running=True)
        banner = MagicMock()
        panel = SettingsPanel(root, ctrl, banner=banner)
        panel._apply_restart_event(  # noqa: SLF001
            self._event("failed", "output", "device validator")
        )
        banner.show_error.assert_called_once()
        args, _ = banner.show_error.call_args
        assert "出力" in args[0]
        assert "device validator" in args[0]

    def test_banner_none_safe(self, root) -> None:
        """banner が None でも例外にならない(テスト・縮退環境)。"""
        from voice_translator.gui.settings_panel import SettingsPanel

        ctrl = _make_panel_controller(is_running=True)
        panel = SettingsPanel(root, ctrl, banner=None)
        # 例外を出さない
        panel._apply_restart_event(self._event("started"))  # noqa: SLF001
        panel._apply_restart_event(self._event("completed"))  # noqa: SLF001
        panel._apply_restart_event(self._event("failed", "input", "x"))  # noqa: SLF001


class TestReloadGuardWhileRunning:
    """「設定を再読込」は動作中 / ロード中は拒否する(2026-06-10 ドッグフーディング起票)。

    再読込は全 backend キャッシュを evict するため、動作中に走らせると Coordinator が
    旧インスタンスを掴んだまま表示だけ INIT に戻り、表示と実行状態が食い違う。
    shim 方式(GUI 構築なし)で _on_reload のガード分岐だけを検証する。
    """

    @staticmethod
    def _shim(*, is_running: bool, is_loading: bool = False, banner=True):
        from voice_translator.gui.settings_panel import SettingsPanel

        shim = MagicMock(spec=SettingsPanel)
        shim._on_reload = SettingsPanel._on_reload.__get__(shim)
        shim._reload_blocked = SettingsPanel._reload_blocked.__get__(shim)
        shim._notify_warning = SettingsPanel._notify_warning.__get__(shim)
        shim._controller = MagicMock(name="controller")
        shim._controller.is_running = is_running
        shim._controller.is_loading = is_loading
        shim._banner = MagicMock(name="banner") if banner else None
        shim._show_message = MagicMock(name="show_message")
        return shim

    def test_reload_refused_while_running(self) -> None:
        shim = self._shim(is_running=True)
        shim._on_reload()  # noqa: SLF001
        shim._controller.load_settings.assert_not_called()
        shim._banner.show_warning.assert_called_once()
        msg = shim._banner.show_warning.call_args[0][0]
        assert "動作中" in msg

    def test_reload_refused_while_loading(self) -> None:
        shim = self._shim(is_running=False, is_loading=True)
        shim._on_reload()  # noqa: SLF001
        shim._controller.load_settings.assert_not_called()
        shim._banner.show_warning.assert_called_once()

    def test_reload_allowed_when_idle(self) -> None:
        shim = self._shim(is_running=False)
        shim._on_reload()  # noqa: SLF001
        shim._controller.load_settings.assert_called_once()
        # 後続の再構築 + 完了メッセージまで配線される(shim 上は auto-mock)
        shim._populate_devices_into_dropdowns.assert_called_once()
        shim._sync_all_status_labels.assert_called_once()
        shim._show_message.assert_called_with("設定を再読込しました")

    def test_banner_none_falls_back_to_show_message(self) -> None:
        shim = self._shim(is_running=True, banner=False)
        shim._on_reload()  # noqa: SLF001
        shim._controller.load_settings.assert_not_called()
        shim._show_message.assert_called_once()
