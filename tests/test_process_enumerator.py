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
