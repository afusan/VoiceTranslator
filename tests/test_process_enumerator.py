"""process_enumerator の small テスト。

pycaw / psutil は monkeypatch で完全置換し、列挙ロジック単体を検証する。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from voice_translator.capture import process_enumerator as pe
from voice_translator.common.types import CaptureKind, CaptureSource


# ============================================================
# Fake objects
# ============================================================
@dataclass
class _FakeMeter:
    peak: float = 0.0

    def GetPeakValue(self) -> float:  # noqa: N802 - WASAPI API
        return self.peak


@dataclass
class _FakeSession:
    pid: int
    process_name: str | None
    meter: _FakeMeter | None = None


def _make_info(pid: int, name: str | None = "app.exe", meter: _FakeMeter | None = None):
    raw = _FakeSession(pid=pid, process_name=name, meter=meter or _FakeMeter())
    return pe._SessionInfo(pid=pid, process_name=name, raw_session=raw)


# ============================================================
# enumerate_active_processes()
# ============================================================
class TestEnumerateActiveProcesses:
    def test_returns_capture_source_list_with_process_kind(self, monkeypatch):
        monkeypatch.setattr(pe, "_list_active_sessions", lambda: [_make_info(1234, "chrome.exe")])
        monkeypatch.setattr(pe, "_resolve_process_name", lambda pid, hint: hint or "unknown")

        result = pe.enumerate_active_processes()

        assert len(result) == 1
        src = result[0]
        assert isinstance(src, CaptureSource)
        assert src.kind == CaptureKind.PROCESS
        assert src.source_id == "1234"
        assert src.display_name == "chrome.exe (1234)"

    def test_dedupes_same_pid(self, monkeypatch):
        # 同 PID で session が 2 件 → 1 件に集約
        monkeypatch.setattr(pe, "_list_active_sessions", lambda: [
            _make_info(2000, "discord.exe"),
            _make_info(2000, "discord.exe"),
        ])
        monkeypatch.setattr(pe, "_resolve_process_name", lambda pid, hint: hint or "unknown")

        result = pe.enumerate_active_processes()

        assert len(result) == 1
        assert result[0].source_id == "2000"

    def test_keeps_distinct_pids(self, monkeypatch):
        monkeypatch.setattr(pe, "_list_active_sessions", lambda: [
            _make_info(100, "chrome.exe"),
            _make_info(101, "chrome.exe"),  # 別 PID(別タブ等)は別行
        ])
        monkeypatch.setattr(pe, "_resolve_process_name", lambda pid, hint: hint or "unknown")

        result = pe.enumerate_active_processes()

        assert [s.source_id for s in result] == ["100", "101"]

    def test_empty_when_no_active_sessions(self, monkeypatch):
        monkeypatch.setattr(pe, "_list_active_sessions", lambda: [])

        assert pe.enumerate_active_processes() == []

    def test_uses_resolve_process_name(self, monkeypatch):
        # _list_active_sessions が None 名前を返しても _resolve_process_name で補完される
        monkeypatch.setattr(pe, "_list_active_sessions", lambda: [_make_info(500, None)])
        monkeypatch.setattr(pe, "_resolve_process_name", lambda pid, hint: "resolved.exe")

        result = pe.enumerate_active_processes()

        assert result[0].display_name == "resolved.exe (500)"


# ============================================================
# get_session_meter()
# ============================================================
class TestGetSessionMeter:
    def test_returns_meter_for_matching_pid(self, monkeypatch):
        target_meter = _FakeMeter(peak=0.42)
        info = _make_info(777, "game.exe", meter=target_meter)
        monkeypatch.setattr(pe, "_list_active_sessions", lambda: [info])
        monkeypatch.setattr(pe, "_query_meter", lambda raw: raw.meter)

        meter = pe.get_session_meter(777)

        assert meter is not None
        assert meter.GetPeakValue() == pytest.approx(0.42)

    def test_returns_none_for_unknown_pid(self, monkeypatch):
        monkeypatch.setattr(pe, "_list_active_sessions", lambda: [_make_info(1, "x.exe")])
        monkeypatch.setattr(pe, "_query_meter", lambda raw: _FakeMeter())

        assert pe.get_session_meter(99999) is None

    def test_returns_none_when_query_meter_fails(self, monkeypatch):
        monkeypatch.setattr(pe, "_list_active_sessions", lambda: [_make_info(123, "x.exe")])
        monkeypatch.setattr(pe, "_query_meter", lambda raw: None)

        assert pe.get_session_meter(123) is None


# ============================================================
# _resolve_process_name() の動作(psutil をモックして検証)
# ============================================================
class TestResolveProcessName:
    def test_uses_psutil_when_available(self, monkeypatch):
        class FakeProc:
            def __init__(self, pid):
                self._pid = pid

            def name(self):
                return f"proc_{self._pid}.exe"

        # process_enumerator が遅延 import している `psutil` モジュールを差し替える
        import sys
        import types
        fake_module = types.ModuleType("psutil")
        fake_module.Process = FakeProc
        monkeypatch.setitem(sys.modules, "psutil", fake_module)

        assert pe._resolve_process_name(42, hint="ignored.exe") == "proc_42.exe"

    def test_falls_back_to_hint_on_psutil_failure(self, monkeypatch):
        import sys
        import types

        class FakeProc:
            def __init__(self, pid):
                raise PermissionError("denied")

        fake_module = types.ModuleType("psutil")
        fake_module.Process = FakeProc
        monkeypatch.setitem(sys.modules, "psutil", fake_module)

        assert pe._resolve_process_name(42, hint="from_pycaw.exe") == "from_pycaw.exe"

    def test_falls_back_to_unknown_when_both_fail(self, monkeypatch):
        import sys
        import types

        class FakeProc:
            def __init__(self, pid):
                raise PermissionError("denied")

        fake_module = types.ModuleType("psutil")
        fake_module.Process = FakeProc
        monkeypatch.setitem(sys.modules, "psutil", fake_module)

        assert pe._resolve_process_name(42, hint=None) == "unknown"


# ============================================================
# _list_active_sessions() の動作(pycaw 全体をモック)
# ============================================================
class TestListActiveSessions:
    def _install_fake_pycaw(self, monkeypatch, sessions):
        import sys
        import types

        class FakeAudioUtilities:
            @staticmethod
            def GetAllSessions():  # noqa: N802
                return sessions

        # process_enumerator は `from pycaw.pycaw import AudioUtilities` で取得するため、
        # `pycaw.pycaw` モジュールを差し替える。
        fake_pycaw_module = types.ModuleType("pycaw")
        fake_pycaw_inner = types.ModuleType("pycaw.pycaw")
        fake_pycaw_inner.AudioUtilities = FakeAudioUtilities

        # IAudioMeterInformation も後の _query_meter 呼び出しで参照されるため用意。
        class _DummyMeterIface:
            pass
        fake_pycaw_inner.IAudioMeterInformation = _DummyMeterIface

        monkeypatch.setitem(sys.modules, "pycaw", fake_pycaw_module)
        monkeypatch.setitem(sys.modules, "pycaw.pycaw", fake_pycaw_inner)

    def test_filters_inactive_sessions(self, monkeypatch):
        class FakeProc:
            def name(self):
                return "x.exe"

        class FakeCtl:
            def __init__(self, state):
                self._state = state

            def GetState(self):  # noqa: N802
                return self._state

        class FakeSession:
            def __init__(self, pid, state):
                self.ProcessId = pid
                self.Process = FakeProc()
                self._ctl = FakeCtl(state)

        sessions = [
            FakeSession(100, 0),  # Inactive
            FakeSession(200, 1),  # Active
            FakeSession(300, 2),  # Expired
        ]
        self._install_fake_pycaw(monkeypatch, sessions)

        result = pe._list_active_sessions()
        pids = [info.pid for info in result]
        assert pids == [200]

    def test_excludes_system_session_pid_0(self, monkeypatch):
        class FakeCtl:
            def GetState(self):  # noqa: N802
                return 1

        class FakeSession:
            def __init__(self, pid):
                self.ProcessId = pid
                self.Process = None
                self._ctl = FakeCtl()

        sessions = [FakeSession(0), FakeSession(123)]
        self._install_fake_pycaw(monkeypatch, sessions)

        result = pe._list_active_sessions()
        assert [info.pid for info in result] == [123]

    def test_get_all_sessions_failure_returns_empty(self, monkeypatch):
        import sys
        import types

        class FakeAudioUtilities:
            @staticmethod
            def GetAllSessions():  # noqa: N802
                raise OSError("COM init failed")

        fake_pycaw_inner = types.ModuleType("pycaw.pycaw")
        fake_pycaw_inner.AudioUtilities = FakeAudioUtilities
        fake_pycaw_inner.IAudioMeterInformation = type("_M", (), {})
        monkeypatch.setitem(sys.modules, "pycaw", types.ModuleType("pycaw"))
        monkeypatch.setitem(sys.modules, "pycaw.pycaw", fake_pycaw_inner)

        # 例外は呑んで空リストにする(列挙失敗で全 UI が落ちないように)
        assert pe._list_active_sessions() == []


# ============================================================
# COM ワーカースレッド経由実行(GUI スレッドの COM 状態と競合させない)
# ============================================================
class TestRunInComThread:
    def _install_fake_comtypes(self, monkeypatch):
        """`comtypes` を fake module で差し替え、CoInitialize/CoUninitialize を観測。"""
        import sys
        import types

        calls: list[str] = []

        def fake_co_initialize():
            calls.append("init")

        def fake_co_uninitialize():
            calls.append("uninit")

        fake = types.ModuleType("comtypes")
        fake.CoInitialize = fake_co_initialize
        fake.CoUninitialize = fake_co_uninitialize
        monkeypatch.setitem(sys.modules, "comtypes", fake)
        return calls

    def test_runs_func_in_worker_and_returns_result(self, monkeypatch):
        import threading
        self._install_fake_comtypes(monkeypatch)
        captured_thread: list[str] = []

        def work():
            captured_thread.append(threading.current_thread().name)
            return 42

        result = pe._run_in_com_thread(work)
        assert result == 42
        # 呼び出し元(=この pytest スレッド)と別スレッドで実行されている
        assert captured_thread[0] != threading.current_thread().name

    def test_calls_co_initialize_and_uninitialize(self, monkeypatch):
        calls = self._install_fake_comtypes(monkeypatch)
        pe._run_in_com_thread(lambda: 1)
        assert calls == ["init", "uninit"]

    def test_uninitialize_called_even_on_exception(self, monkeypatch):
        calls = self._install_fake_comtypes(monkeypatch)

        def boom():
            raise RuntimeError("boom")

        with pytest.raises(RuntimeError):
            pe._run_in_com_thread(boom)
        assert calls == ["init", "uninit"]

    def test_propagates_func_exception(self, monkeypatch):
        self._install_fake_comtypes(monkeypatch)

        def boom():
            raise ValueError("bad")

        with pytest.raises(ValueError, match="bad"):
            pe._run_in_com_thread(boom)

    def test_timeout_raises_timeout_error(self, monkeypatch):
        import time
        self._install_fake_comtypes(monkeypatch)

        def slow():
            time.sleep(0.5)
            return "too late"

        with pytest.raises(TimeoutError):
            pe._run_in_com_thread(slow, timeout=0.05)

    def test_co_initialize_oserror_is_swallowed(self, monkeypatch):
        """既に別モードで COM 初期化済みのケース。CoInitialize が OSError でも続行。"""
        import sys
        import types

        def fake_co_initialize():
            raise OSError("already in another mode")

        def fake_co_uninitialize():
            pass

        fake = types.ModuleType("comtypes")
        fake.CoInitialize = fake_co_initialize
        fake.CoUninitialize = fake_co_uninitialize
        monkeypatch.setitem(sys.modules, "comtypes", fake)

        assert pe._run_in_com_thread(lambda: "ok") == "ok"


# ============================================================
# MeterProxy: peak 取得が COM ワーカ経由になる
# ============================================================
class TestMeterProxy:
    def test_get_peak_value_goes_through_com_thread(self, monkeypatch):
        import sys
        import types
        import threading

        co_calls: list[str] = []

        def co_init():
            co_calls.append("init")

        def co_uninit():
            co_calls.append("uninit")

        fake_co = types.ModuleType("comtypes")
        fake_co.CoInitialize = co_init
        fake_co.CoUninitialize = co_uninit
        monkeypatch.setitem(sys.modules, "comtypes", fake_co)

        class FakeRaw:
            def __init__(self):
                self.calls: list[str] = []

            def GetPeakValue(self):  # noqa: N802
                self.calls.append(threading.current_thread().name)
                return 0.55

        raw = FakeRaw()
        proxy = pe._MeterProxy(raw)
        value = proxy.GetPeakValue()
        assert value == pytest.approx(0.55)
        # peak 取得は別スレッドで動いた
        assert raw.calls and raw.calls[0] != threading.current_thread().name
        # COM 初期化/解放が走った
        assert co_calls == ["init", "uninit"]
