"""段階 3 で導入した「PROCESS kind の入力ソース」ライフサイクル周りのテスト。

検証:
- A-7 確定方針: `save_settings` / `load_settings` で PROCESS kind の `devices.input` を
  空文字に正規化する(永続化しない / 起動時もセーフティで空)。
- ControlPanel._sync_ready_state が「PROCESS kind かつ未選択」で Start を disable する。
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from voice_translator.common.app_controller import AppController
from voice_translator.common.types import CaptureKind, LayerKind, ModelStatus


# ============================================================
# A-7: save / load で PROCESS source を空扱いに正規化
# ============================================================
class TestVolatileInputNormalization:
    def _make_shim(self, *, kind: CaptureKind, input_value: str = "1234"):
        """AppController の `_strip_volatile_inputs_before_save` / `_normalize_volatile_inputs_after_load`
        だけを叩ける薄い shim を作る。`_config` は MagicMock で、get/set を観察する。
        """
        shim = MagicMock(spec=AppController)
        # bind unbound method
        shim._strip_volatile_inputs_before_save = (
            AppController._strip_volatile_inputs_before_save.__get__(shim)
        )
        shim._normalize_volatile_inputs_after_load = (
            AppController._normalize_volatile_inputs_after_load.__get__(shim)
        )
        shim._clear_process_input_if_applicable = (
            AppController._clear_process_input_if_applicable.__get__(shim)
        )

        # 配下の ConfigStore モック
        config_data = {
            ("backends", "capture"): "proctap" if kind == CaptureKind.PROCESS else "soundcard",
            ("devices", "input"): input_value,
        }
        set_calls: list[tuple] = []

        def fake_get(*keys, default=None):
            return config_data.get(keys, default)

        def fake_set(*keys_and_value):
            *keys, value = keys_and_value
            config_data[tuple(keys)] = value
            set_calls.append((tuple(keys), value))

        shim._config = MagicMock()
        shim._config.get.side_effect = fake_get
        shim._config.set.side_effect = fake_set
        # get_capture_kind は backend 名に応じて kind を返す
        shim.get_capture_kind.side_effect = lambda name: (
            CaptureKind.PROCESS if name == "proctap" else CaptureKind.DEVICE
        )
        return shim, config_data, set_calls

    def test_save_clears_input_when_process_kind(self):
        shim, data, calls = self._make_shim(kind=CaptureKind.PROCESS, input_value="42")
        shim._strip_volatile_inputs_before_save()
        # devices.input が "" に書き換えられている
        assert data[("devices", "input")] == ""
        assert (("devices", "input"), "") in calls

    def test_save_keeps_input_when_device_kind(self):
        shim, data, calls = self._make_shim(kind=CaptureKind.DEVICE, input_value="mic1")
        shim._strip_volatile_inputs_before_save()
        # DEVICE kind は触らない
        assert data[("devices", "input")] == "mic1"
        assert not calls  # set は呼ばれない

    def test_load_normalizes_stale_pid_in_process_kind(self):
        shim, data, calls = self._make_shim(kind=CaptureKind.PROCESS, input_value="999")
        shim._normalize_volatile_inputs_after_load()
        assert data[("devices", "input")] == ""

    def test_load_keeps_input_when_device_kind(self):
        shim, data, calls = self._make_shim(kind=CaptureKind.DEVICE, input_value="speakers")
        shim._normalize_volatile_inputs_after_load()
        assert data[("devices", "input")] == "speakers"

    def test_no_backend_name_is_noop(self):
        shim, data, calls = self._make_shim(kind=CaptureKind.PROCESS, input_value="7")
        # backend 名を空に上書き
        shim._config.get.side_effect = lambda *keys, default=None: (
            "" if keys == ("backends", "capture") else (
                "7" if keys == ("devices", "input") else default
            )
        )
        shim._strip_volatile_inputs_before_save()
        # 何も触らない
        assert not calls


# ============================================================
# ControlPanel: PROCESS kind かつ未選択で Start disable
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
    # ControlPanel は 3 秒周期の `after` を起動するので、root.destroy() の前に
    # 全 pending after を明示的にキャンセルする。これをやらないと後続テストの
    # tcl interp が時々おかしくなる(`test_pipeline_e2e` 等が flaky 化する)。
    try:
        pending = r.tk.call("after", "info")
        if isinstance(pending, str) and pending:
            for after_id in pending.split():
                try:
                    r.after_cancel(after_id)
                except Exception:  # noqa: BLE001
                    pass
    except Exception:  # noqa: BLE001
        pass
    try:
        r.destroy()
    except Exception:  # noqa: BLE001
        pass


class _StubController:
    """ControlPanel が必要とする最小限の AppController スタブ。"""

    def __init__(self, *, capture_backend="proctap", input_value="",
                 capture_kind=CaptureKind.PROCESS, output_mode="audio"):
        self._settings = {
            ("backends", "capture"): capture_backend,
            ("devices", "input"): input_value,
            ("ui", "collapsed", "status_text"): False,
        }
        self._capture_kind = capture_kind
        self._statuses = {layer: ModelStatus.LOADED for layer in LayerKind}
        self.output_mode = output_mode
        # P2: ControlPanel が __init__ で登録する listener の記録(event 名 → callbacks)
        self.listeners: dict[str, list] = {}

    # ---- ControlPanel が使う API ----
    def get_setting(self, *keys, default=None):
        return self._settings.get(tuple(keys), default)

    def set_setting(self, *keys_and_value):
        *keys, value = keys_and_value
        self._settings[tuple(keys)] = value
        # 本物の AppController と同様に settings イベントを流す
        for cb in self.listeners.get("settings", []):
            cb(tuple(keys))

    def get_capture_kind(self, backend_name):
        return self._capture_kind

    def get_all_model_statuses(self):
        return dict(self._statuses)

    def get_status_snapshot(self):
        return [], []

    def get_layer_device(self, layer):
        return None

    # ---- P2: listener 登録 API ----
    def _add_listener(self, event: str, cb):
        self.listeners.setdefault(event, []).append(cb)
        return None

    def add_status_listener(self, cb):
        return self._add_listener("status", cb)

    def add_text_ready_listener(self, cb):
        return self._add_listener("text_ready", cb)

    def add_utterance_done_listener(self, cb):
        return self._add_listener("utterance_done", cb)

    def add_fatal_listener(self, cb):
        return self._add_listener("fatal", cb)

    def add_warn_listener(self, cb):
        return self._add_listener("warn", cb)

    def add_settings_listener(self, cb):
        return self._add_listener("settings", cb)

    @property
    def is_running(self):
        return False

    @property
    def is_loading(self):
        return False


class TestControlPanelStartDisabled:
    def _make_panel(self, root, controller):
        from voice_translator.gui.control_panel import ControlPanel
        return ControlPanel(root, controller, banner=None)

    def test_start_disabled_when_process_kind_and_no_pid(self, root):
        ctrl = _StubController(capture_backend="proctap", input_value="",
                               capture_kind=CaptureKind.PROCESS)
        panel = self._make_panel(root, ctrl)
        # 状態取得
        btn_text = panel._toggle_btn.cget("text")  # noqa: SLF001
        btn_state = panel._toggle_btn.cget("state")  # noqa: SLF001
        assert btn_text == "プロセス未選択"
        assert str(btn_state) == "disabled"

    def test_pid_selection_via_settings_event_enables_start(self, root):
        """P2(契約 §11.5): `devices.input` の書き込み(settings イベント)だけで
        「プロセス未選択(disable)」→「▶ 開始(normal)」へ遷移する。
        旧 `SettingsPanel → refresh_ready_state()` 直叩きの置き換え経路。"""
        ctrl = _StubController(capture_backend="proctap", input_value="",
                               capture_kind=CaptureKind.PROCESS)
        panel = self._make_panel(root, ctrl)
        assert panel._toggle_btn.cget("text") == "プロセス未選択"  # noqa: SLF001

        # PID 選択完了相当: set_setting が settings イベントを流す
        ctrl.set_setting("devices", "input", "1234")
        root.update()  # after(0, ...) を反映

        assert panel._toggle_btn.cget("text") == "▶ 開始"  # noqa: SLF001
        assert str(panel._toggle_btn.cget("state")) == "normal"  # noqa: SLF001

    def test_non_device_settings_event_does_not_resync(self, root):
        """devices 以外の settings イベントでは ready 再計算をスケジュールしない。"""
        ctrl = _StubController(capture_backend="proctap", input_value="",
                               capture_kind=CaptureKind.PROCESS)
        panel = self._make_panel(root, ctrl)
        # devices 以外のキーを書く → イベントは流れるが ready は触らない
        ctrl.set_setting("ui", "collapsed", "status_text", True)
        root.update()
        assert panel._toggle_btn.cget("text") == "プロセス未選択"  # noqa: SLF001

    def test_start_enabled_when_process_kind_pid_present(self, root):
        ctrl = _StubController(capture_backend="proctap", input_value="1234",
                               capture_kind=CaptureKind.PROCESS)
        panel = self._make_panel(root, ctrl)
        btn_text = panel._toggle_btn.cget("text")  # noqa: SLF001
        btn_state = panel._toggle_btn.cget("state")  # noqa: SLF001
        assert btn_text == "▶ 開始"
        assert str(btn_state) == "normal"

    def test_device_kind_unaffected_when_input_empty(self, root):
        # DEVICE kind なら input 未設定でも Start は触らない(従来挙動)
        ctrl = _StubController(capture_backend="soundcard", input_value="",
                               capture_kind=CaptureKind.DEVICE)
        panel = self._make_panel(root, ctrl)
        btn_text = panel._toggle_btn.cget("text")  # noqa: SLF001
        # DEVICE は未選択でも開始可(soundcard が default 入力で動くため)
        assert btn_text == "▶ 開始"

    def test_refresh_ready_state_window_is_removed(self, root):
        """P2: `refresh_ready_state` 公開窓は撤去済み。

        旧テスト test_refresh_ready_state_reflects_pid_selection が守っていた
        「PID 選択(set_setting)後に Start が enable になる」契約(2026-06-06
        修正のレグレッション防止)は、settings イベント経由の
        test_pid_selection_via_settings_event_enables_start がより強い形で温存
        (今は set_setting 単独で再評価が走る)。
        """
        ctrl = _StubController(capture_backend="proctap", input_value="",
                               capture_kind=CaptureKind.PROCESS)
        panel = self._make_panel(root, ctrl)
        assert not hasattr(panel, "refresh_ready_state")
