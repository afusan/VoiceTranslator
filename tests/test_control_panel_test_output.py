"""ControlPanel の「🔊 出力テスト」ボタンのテスト。

検証:
- ボタン状態が `output_mode` / `devices.output` に応じて切り替わる
- 通常時のクリックで `controller.test_output_playback` が呼ばれる
- 失敗時のエラーメッセージが「操作イベント」に積まれる
- 動作中(`is_running=True`)はボタンが disable される
"""

from __future__ import annotations

import threading
import time

import pytest

from voice_translator.common.types import CaptureKind, LayerKind, ModelStatus


# ============================================================
# 共通: GUI 環境セットアップ
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
    # 周期 after の後始末(他テストの flaky 化を防ぐ)
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


# ============================================================
# Stub Controller
# ============================================================
class _StubController:
    """ControlPanel が必要とする最小限の AppController スタブ。"""

    def __init__(
        self, *,
        output_mode: str = "audio",
        output_device: str = "hp",
        capture_kind: CaptureKind = CaptureKind.DEVICE,
        is_running: bool = False,
        playback_raises: Exception | None = None,
    ) -> None:
        self._settings: dict[tuple, object] = {
            ("backends", "capture"): "soundcard",
            ("devices", "input"): "mic",
            ("devices", "output"): output_device,
            ("ui", "collapsed", "status_text"): False,
        }
        self._capture_kind = capture_kind
        self._statuses = {layer: ModelStatus.LOADED for layer in LayerKind}
        self.output_mode = output_mode
        self._is_running = is_running
        self._playback_raises = playback_raises
        self._callbacks: dict = {}
        # ボタン押下で呼ばれた回数 + 引数を観察
        self.test_output_calls: list[str] = []

    def get_setting(self, *keys, default=None):
        return self._settings.get(tuple(keys), default)

    def set_setting(self, *keys_and_value):
        *keys, value = keys_and_value
        self._settings[tuple(keys)] = value

    def get_capture_kind(self, backend_name):
        return self._capture_kind

    def get_all_model_statuses(self):
        return dict(self._statuses)

    def get_status_snapshot(self):
        return [], []

    def get_layer_device(self, layer):
        return None

    def set_callbacks(self, **kwargs):
        self._callbacks.update(kwargs)

    @property
    def is_running(self):
        return self._is_running

    @property
    def is_loading(self):
        return False

    def test_output_playback(self, text: str = "テスト音声") -> None:
        self.test_output_calls.append(text)
        if self._playback_raises is not None:
            raise self._playback_raises


def _make_panel(root, controller):
    from voice_translator.gui.control_panel import ControlPanel
    return ControlPanel(root, controller, settings_panel=None, banner=None)


# ============================================================
# ボタン状態
# ============================================================
class TestTestButtonState:
    def test_normal_state_when_audio_mode_and_device_selected(self, root) -> None:
        ctrl = _StubController(output_mode="audio", output_device="hp")
        panel = _make_panel(root, ctrl)
        assert panel._test_btn.cget("text") == "🔊 出力テスト"  # noqa: SLF001
        assert str(panel._test_btn.cget("state")) == "normal"  # noqa: SLF001

    def test_disabled_in_text_only_mode(self, root) -> None:
        ctrl = _StubController(output_mode="text_only", output_device="hp")
        panel = _make_panel(root, ctrl)
        assert panel._test_btn.cget("text") == "🔊 (TTS なし)"  # noqa: SLF001
        assert str(panel._test_btn.cget("state")) == "disabled"  # noqa: SLF001

    def test_disabled_when_output_device_unselected(self, root) -> None:
        ctrl = _StubController(output_mode="audio", output_device="")
        panel = _make_panel(root, ctrl)
        assert panel._test_btn.cget("text") == "🔊 出力未選択"  # noqa: SLF001
        assert str(panel._test_btn.cget("state")) == "disabled"  # noqa: SLF001


# ============================================================
# ボタン押下: コールパス
# ============================================================
class _SyncThread:
    """tkinter mainloop が無いテスト環境用: Thread.start() を同期実行に変える。

    本番の ControlPanel は worker スレッドから `self.after(0, ...)` で UI に戻すが、
    テスト環境では `mainloop` が回っていないと別スレッドからの `after` 登録が
    `RuntimeError: main thread is not in main loop` で失敗する。
    Thread を同期に倒して「worker は呼び出し直後に終わる + after もメインスレッドから
    登録される」状態にすれば、`root.update()` だけでフローが完結する。
    """

    def __init__(self, *, target=None, args=(), kwargs=None, **_ignored):
        self._target = target
        self._args = args
        self._kwargs = kwargs or {}

    def start(self) -> None:
        if self._target is not None:
            self._target(*self._args, **self._kwargs)


@pytest.fixture()
def sync_thread(monkeypatch: pytest.MonkeyPatch):
    """control_panel モジュールの `threading.Thread` を _SyncThread に差し替える。"""
    from voice_translator.gui import control_panel as cp
    monkeypatch.setattr(cp.threading, "Thread", _SyncThread)


class TestTestButtonClick:
    def _flush_after(self, root, *, ticks: int = 20) -> None:
        """after(0, ...) で予約されたコールバックを反映する。"""
        for _ in range(ticks):
            root.update()
            time.sleep(0.005)
            try:
                pending = root.tk.call("after", "info")
            except Exception:  # noqa: BLE001
                pending = ""
            if not pending:
                return

    def test_click_invokes_controller_test_playback(
        self, root, sync_thread,
    ) -> None:
        ctrl = _StubController(output_mode="audio", output_device="hp")
        panel = _make_panel(root, ctrl)
        panel._on_test_output_clicked()  # noqa: SLF001
        self._flush_after(root)
        assert ctrl.test_output_calls == ["テスト音声"]
        # 完了で再度 normal に戻る
        assert str(panel._test_btn.cget("state")) == "normal"  # noqa: SLF001

    def test_click_failure_logs_status_event(
        self, root, sync_thread,
    ) -> None:
        ctrl = _StubController(
            output_mode="audio", output_device="hp",
            playback_raises=RuntimeError("simulated failure"),
        )
        panel = _make_panel(root, ctrl)
        panel._on_test_output_clicked()  # noqa: SLF001
        self._flush_after(root)
        # 失敗イベントが status textbox 用ログに積まれていること
        events = list(panel._gui_event_log)  # noqa: SLF001
        assert any("出力テスト失敗" in ev for ev in events)
        assert any("simulated failure" in ev for ev in events)


# ============================================================
# 並行性 / 二重押し防止
# ============================================================
class TestTestButtonReentry:
    def test_does_nothing_during_starting_state(self, root) -> None:
        """starting / running / stopping のとき(state != idle)はクリックを無視。"""
        ctrl = _StubController(output_mode="audio", output_device="hp")
        panel = _make_panel(root, ctrl)
        panel._state = "starting"  # noqa: SLF001
        panel._on_test_output_clicked()  # noqa: SLF001
        # ワーカが起動しないので test_output_playback は呼ばれない
        time.sleep(0.05)
        root.update()
        assert ctrl.test_output_calls == []
