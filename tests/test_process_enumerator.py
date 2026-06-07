"""process_enumerator の small テスト(永続 Peak Worker 版)。

pycaw / psutil / comtypes は monkeypatch で完全置換し、列挙ロジック + Worker の
スレッド動作を検証する。
"""

from __future__ import annotations

import sys
import threading
import time
import types
from dataclasses import dataclass

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


def _install_fake_comtypes(monkeypatch):
    """comtypes を fake module で差し替え、CoInitialize/CoUninitialize を観測。"""
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


@pytest.fixture(autouse=True)
def _fresh_worker(monkeypatch):
    """各テストの前にグローバルワーカを破棄し、テスト後も破棄する(状態を持ち越さない)。

    autouse=True で全テスト対象。テスト内で `_get_worker()` が新規ワーカを起動する。
    """
    pe.dispose()
    yield
    pe.dispose()


# ============================================================
# _enumerate_in_com_thread(純ロジック)
# ============================================================
class TestEnumerateLogic:
    def test_returns_capture_source_list_with_process_kind(self, monkeypatch):
        monkeypatch.setattr(pe, "_list_active_sessions", lambda: [_make_info(1234, "chrome.exe")])
        monkeypatch.setattr(pe, "_resolve_process_name", lambda pid, hint: hint or "unknown")

        result = pe._enumerate_in_com_thread()

        assert len(result) == 1
        src = result[0]
        assert isinstance(src, CaptureSource)
        assert src.kind == CaptureKind.PROCESS
        assert src.source_id == "1234"
        assert src.display_name == "chrome.exe (1234)"

    def test_dedupes_same_pid(self, monkeypatch):
        monkeypatch.setattr(pe, "_list_active_sessions", lambda: [
            _make_info(2000, "discord.exe"),
            _make_info(2000, "discord.exe"),
        ])
        monkeypatch.setattr(pe, "_resolve_process_name", lambda pid, hint: hint or "unknown")

        result = pe._enumerate_in_com_thread()

        assert len(result) == 1
        assert result[0].source_id == "2000"

    def test_keeps_distinct_pids(self, monkeypatch):
        monkeypatch.setattr(pe, "_list_active_sessions", lambda: [
            _make_info(100, "chrome.exe"),
            _make_info(101, "chrome.exe"),
        ])
        monkeypatch.setattr(pe, "_resolve_process_name", lambda pid, hint: hint or "unknown")

        result = pe._enumerate_in_com_thread()
        assert [s.source_id for s in result] == ["100", "101"]

    def test_empty_when_no_active_sessions(self, monkeypatch):
        monkeypatch.setattr(pe, "_list_active_sessions", lambda: [])
        assert pe._enumerate_in_com_thread() == []

    def test_uses_resolve_process_name(self, monkeypatch):
        monkeypatch.setattr(pe, "_list_active_sessions", lambda: [_make_info(500, None)])
        monkeypatch.setattr(pe, "_resolve_process_name", lambda pid, hint: "resolved.exe")

        result = pe._enumerate_in_com_thread()
        assert result[0].display_name == "resolved.exe (500)"


# ============================================================
# 公開 API: enumerate_active_processes (永続ワーカ経由)
# ============================================================
class TestEnumerateActiveProcesses:
    def test_runs_through_worker_thread(self, monkeypatch):
        _install_fake_comtypes(monkeypatch)
        captured_threads: list[str] = []

        def fake_list_sessions():
            captured_threads.append(threading.current_thread().name)
            return [_make_info(42, "x.exe")]

        monkeypatch.setattr(pe, "_list_active_sessions", fake_list_sessions)
        monkeypatch.setattr(pe, "_resolve_process_name", lambda pid, hint: hint or "unknown")

        result = pe.enumerate_active_processes()
        assert len(result) == 1
        # 別スレッド(=ワーカ)で実行されている
        assert captured_threads[0] != threading.current_thread().name


# ============================================================
# _resolve_process_name(psutil)
# ============================================================
class TestResolveProcessName:
    def test_uses_psutil_when_available(self, monkeypatch):
        class FakeProc:
            def __init__(self, pid):
                self._pid = pid

            def name(self):
                return f"proc_{self._pid}.exe"

        fake_module = types.ModuleType("psutil")
        fake_module.Process = FakeProc
        monkeypatch.setitem(sys.modules, "psutil", fake_module)

        assert pe._resolve_process_name(42, hint="ignored.exe") == "proc_42.exe"

    def test_falls_back_to_hint_on_psutil_failure(self, monkeypatch):
        class FakeProc:
            def __init__(self, pid):
                raise PermissionError("denied")

        fake_module = types.ModuleType("psutil")
        fake_module.Process = FakeProc
        monkeypatch.setitem(sys.modules, "psutil", fake_module)

        assert pe._resolve_process_name(42, hint="from_pycaw.exe") == "from_pycaw.exe"

    def test_falls_back_to_unknown_when_both_fail(self, monkeypatch):
        class FakeProc:
            def __init__(self, pid):
                raise PermissionError("denied")

        fake_module = types.ModuleType("psutil")
        fake_module.Process = FakeProc
        monkeypatch.setitem(sys.modules, "psutil", fake_module)

        assert pe._resolve_process_name(42, hint=None) == "unknown"


# ============================================================
# _list_active_sessions(pycaw)
# ============================================================
class TestListActiveSessions:
    def _install_fake_pycaw(self, monkeypatch, sessions):
        class FakeAudioUtilities:
            @staticmethod
            def GetAllSessions():  # noqa: N802
                return sessions

        fake_pycaw_module = types.ModuleType("pycaw")
        fake_pycaw_inner = types.ModuleType("pycaw.pycaw")
        fake_pycaw_inner.AudioUtilities = FakeAudioUtilities

        class _DummyMeterIface:
            pass
        fake_pycaw_inner.IAudioMeterInformation = _DummyMeterIface

        monkeypatch.setitem(sys.modules, "pycaw", fake_pycaw_module)
        monkeypatch.setitem(sys.modules, "pycaw.pycaw", fake_pycaw_inner)

    def test_excludes_expired_only_accepts_inactive_and_active(self, monkeypatch):
        """2026-06-08 仕様変更: Active(1) + Inactive(0) を採用、Expired(2) のみ除外。

        旧仕様(Active のみ)では Win11 の audio engine sleep や「無音時 Stop」実装の
        アプリで列挙がほぼ空になっていた(別環境で実観測)。Sndvol の表示集合に合わせる
        ことで、proc-tap で実用的にプロセスを選べるようにする。
        """
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

        # state=0 Inactive(採用), 1 Active(採用), 2 Expired(除外)
        sessions = [FakeSession(100, 0), FakeSession(200, 1), FakeSession(300, 2)]
        self._install_fake_pycaw(monkeypatch, sessions)
        result = pe._list_active_sessions()
        assert [info.pid for info in result] == [100, 200]

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
        class FakeAudioUtilities:
            @staticmethod
            def GetAllSessions():  # noqa: N802
                raise OSError("COM init failed")

        fake_pycaw_inner = types.ModuleType("pycaw.pycaw")
        fake_pycaw_inner.AudioUtilities = FakeAudioUtilities
        fake_pycaw_inner.IAudioMeterInformation = type("_M", (), {})
        monkeypatch.setitem(sys.modules, "pycaw", types.ModuleType("pycaw"))
        monkeypatch.setitem(sys.modules, "pycaw.pycaw", fake_pycaw_inner)

        assert pe._list_active_sessions() == []


# ============================================================
# _PeakWorker: 永続スレッド + 内部 poll
# ============================================================
class TestPeakWorkerLifecycle:
    def test_co_initialize_called_once_on_start(self, monkeypatch):
        calls = _install_fake_comtypes(monkeypatch)
        monkeypatch.setattr(pe, "_list_active_sessions", lambda: [])
        # ワーカ起動 + 列挙 1 回
        worker = pe._get_worker()
        worker.submit(lambda: None)
        # 少し待って poll loop が回ったことを確実にする
        time.sleep(0.05)
        # 起動時に init 1 回(まだ uninit は呼ばれない、dispose まで)
        assert calls.count("init") == 1
        assert calls.count("uninit") == 0

    def test_dispose_calls_co_uninitialize(self, monkeypatch):
        calls = _install_fake_comtypes(monkeypatch)
        monkeypatch.setattr(pe, "_list_active_sessions", lambda: [])
        pe._get_worker()
        pe.dispose()
        assert calls.count("init") == 1
        assert calls.count("uninit") == 1

    def test_multiple_submits_reuse_thread(self, monkeypatch):
        _install_fake_comtypes(monkeypatch)
        captured_threads: list[str] = []

        def task():
            captured_threads.append(threading.current_thread().name)
            return 1

        worker = pe._get_worker()
        worker.submit(task)
        worker.submit(task)
        worker.submit(task)
        # 3 回呼んでもスレッド名は同じ(= 永続)
        assert len(set(captured_threads)) == 1


class TestPeakWorkerAudition:
    def test_start_audition_when_meter_found(self, monkeypatch):
        _install_fake_comtypes(monkeypatch)
        meter = _FakeMeter(peak=0.4)
        info = _make_info(7777, "game.exe", meter=meter)
        monkeypatch.setattr(pe, "_list_active_sessions", lambda: [info])
        monkeypatch.setattr(pe, "_query_meter", lambda raw: raw.meter)

        ok = pe.start_audition(7777)
        assert ok is True
        # 内部 poll が回って peak が更新される(<= 5fps なので 0.3s 待てば充分)
        time.sleep(0.3)
        assert pe.latest_peak() == pytest.approx(0.4)
        assert pe.is_auditioning() is True

    def test_start_audition_when_meter_missing_returns_false(self, monkeypatch):
        _install_fake_comtypes(monkeypatch)
        info = _make_info(1, "x.exe")
        monkeypatch.setattr(pe, "_list_active_sessions", lambda: [info])
        monkeypatch.setattr(pe, "_query_meter", lambda raw: None)  # メータ取得失敗

        ok = pe.start_audition(1)
        assert ok is False
        assert pe.is_auditioning() is False

    def test_start_audition_unknown_pid_returns_false(self, monkeypatch):
        _install_fake_comtypes(monkeypatch)
        monkeypatch.setattr(pe, "_list_active_sessions", lambda: [_make_info(1, "x.exe")])
        monkeypatch.setattr(pe, "_query_meter", lambda raw: _FakeMeter())

        assert pe.start_audition(99999) is False
        assert pe.is_auditioning() is False

    def test_stop_audition_resets_peak(self, monkeypatch):
        _install_fake_comtypes(monkeypatch)
        meter = _FakeMeter(peak=0.8)
        info = _make_info(3, "x.exe", meter=meter)
        monkeypatch.setattr(pe, "_list_active_sessions", lambda: [info])
        monkeypatch.setattr(pe, "_query_meter", lambda raw: raw.meter)

        pe.start_audition(3)
        time.sleep(0.3)
        assert pe.latest_peak() > 0
        pe.stop_audition()
        assert pe.latest_peak() == 0.0
        assert pe.is_auditioning() is False

    def test_peak_meter_exception_is_swallowed(self, monkeypatch):
        _install_fake_comtypes(monkeypatch)

        class FailingMeter:
            def GetPeakValue(self):  # noqa: N802
                raise OSError("meter gone")

        info = _make_info(5, "x.exe")
        monkeypatch.setattr(pe, "_list_active_sessions", lambda: [info])
        monkeypatch.setattr(pe, "_query_meter", lambda raw: FailingMeter())

        pe.start_audition(5)
        time.sleep(0.3)
        # 例外を吸って peak は 0 に維持される(クラッシュしない)
        assert pe.latest_peak() == 0.0
        assert pe.is_auditioning() is True

    def test_submit_propagates_exception(self, monkeypatch):
        _install_fake_comtypes(monkeypatch)

        def boom():
            raise ValueError("bad")

        worker = pe._get_worker()
        with pytest.raises(ValueError, match="bad"):
            worker.submit(boom)

    def test_submit_timeout_raises_timeout_error(self, monkeypatch):
        _install_fake_comtypes(monkeypatch)

        def slow():
            time.sleep(0.5)
            return "too late"

        worker = pe._get_worker()
        with pytest.raises(TimeoutError):
            worker.submit(slow, timeout=0.05)
