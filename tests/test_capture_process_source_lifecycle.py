"""段階 3 で導入した「PROCESS kind の入力ソース」ライフサイクル周りのテスト。

検証:
- A-7 確定方針: PROCESS kind の `devices.input`(PID)は**ファイルに永続化しない**。
  - save: 書き出し用コピーからのみ除外し、**in-memory のセッション中選択は維持する**
    (以前は実メモリを空にしてから保存していたため、「設定を保存」のたびに
    プロセス選択が内部で無効化されていた — 2026-06-11 修正)
  - load: 起動 / 再読込直後は in-memory 側も空に正規化(再起動後の PID は無意味)
- ControlPanel._sync_ready_state が「PROCESS kind かつ未選択」で Start を disable する。
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from voice_translator.common.app_controller import AppController
from voice_translator.common.config_store import ConfigStore
from voice_translator.common.types import CaptureKind, LayerKind, ModelStatus


# ============================================================
# A-7: save はコピー除外(メモリ維持)/ load は in-memory 正規化
# ============================================================
class TestVolatileInputNormalization:
    def _make_shim(self, *, kind: CaptureKind, input_value: str = "1234"):
        """save/load の揮発キー処理だけを叩ける薄い shim。`_config` は実 ConfigStore。"""
        shim = MagicMock(spec=AppController)
        for name in (
            "save_settings",
            "_strip_volatile_inputs_for_save",
            "_normalize_volatile_inputs_after_load",
            "_is_process_capture_selected",
        ):
            setattr(shim, name, getattr(AppController, name).__get__(shim))

        backend = "proctap" if kind == CaptureKind.PROCESS else "soundcard"
        cfg = ConfigStore(path="dummy", data={})
        cfg.set("backends", "capture", backend)
        cfg.set("devices", "input", input_value)
        shim._config = cfg
        shim.get_capture_kind.side_effect = lambda name: (
            CaptureKind.PROCESS if name == "proctap" else CaptureKind.DEVICE
        )
        return shim, cfg

    def test_save_strips_only_written_copy_and_keeps_memory(self, tmp_path):
        """PROCESS kind: ファイルには PID を書かないが、セッション中の選択は消えない。"""
        import yaml

        shim, cfg = self._make_shim(kind=CaptureKind.PROCESS, input_value="42")
        cfg._path = tmp_path / "cfg.yaml"  # noqa: SLF001 - 実書き出し先を差し替え

        shim.save_settings()

        # in-memory は維持(これが消えると「保存のたびにプロセス選択が無効化」になる)
        assert cfg.get("devices", "input") == "42"
        # ファイル側は空(A-7: PID を永続化しない)
        written = yaml.safe_load(cfg.path.read_text(encoding="utf-8"))
        assert written["devices"]["input"] == ""

    def test_save_keeps_input_when_device_kind(self, tmp_path):
        import yaml

        shim, cfg = self._make_shim(kind=CaptureKind.DEVICE, input_value="mic1")
        cfg._path = tmp_path / "cfg.yaml"  # noqa: SLF001

        shim.save_settings()

        assert cfg.get("devices", "input") == "mic1"
        written = yaml.safe_load(cfg.path.read_text(encoding="utf-8"))
        assert written["devices"]["input"] == "mic1"

    def test_load_normalizes_stale_pid_in_process_kind(self):
        shim, cfg = self._make_shim(kind=CaptureKind.PROCESS, input_value="999")
        shim._normalize_volatile_inputs_after_load()
        assert cfg.get("devices", "input") == ""

    def test_load_keeps_input_when_device_kind(self):
        shim, cfg = self._make_shim(kind=CaptureKind.DEVICE, input_value="speakers")
        shim._normalize_volatile_inputs_after_load()
        assert cfg.get("devices", "input") == "speakers"

    def test_no_backend_name_is_noop(self):
        """backend 未選択時は判定不能 → 何も除外しない。"""
        shim, cfg = self._make_shim(kind=CaptureKind.PROCESS, input_value="7")
        cfg.set("backends", "capture", "")
        data = {"devices": {"input": "7"}}
        assert shim._strip_volatile_inputs_for_save(data) == {"devices": {"input": "7"}}


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
