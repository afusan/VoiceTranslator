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
# _list_active_sessions(全 Render エンドポイント走査、2026-06-08 改)
# ============================================================
class TestListActiveSessions:
    """全エンドポイント走査の検証。

    fake スタック:
      AudioUtilities.GetDeviceEnumerator() -> FakeDeviceEnumerator
        .EnumAudioEndpoints(eRender=0, ACTIVE=0x1) -> FakeCollection(devices)
          .GetCount() / .Item(i) -> FakeDevice(sessions)
            .Activate(IAudioSessionManager2._iid_, ctx, None) -> FakeMgr (cast no-op)
              .GetSessionEnumerator() -> FakeSessionEnumerator
                .GetCount() / .GetSession(j) -> FakeCtl
                  .QueryInterface(IAudioSessionControl2) -> FakeCtl2 (GetProcessId)
                  .GetState() -> 0/1/2
    """

    def _install_fake_pycaw(
        self, monkeypatch, devices: list[list[tuple[int, int]]],
    ):
        """`devices` は [[(pid, state), ...], [(pid, state), ...]]。
        外側 list = エンドポイント、内側 tuple = (pid, state)。
        """
        class FakeCtl2:
            def __init__(self, pid):
                self._pid = pid

            def GetProcessId(self):  # noqa: N802
                return self._pid

        class FakeCtl:
            def __init__(self, pid, state):
                self._pid = pid
                self._state = state

            def GetState(self):  # noqa: N802
                return self._state

            def QueryInterface(self, iface):  # noqa: N802
                return FakeCtl2(self._pid)

        class FakeSessionEnumerator:
            def __init__(self, ctls):
                self._ctls = ctls

            def GetCount(self):  # noqa: N802
                return len(self._ctls)

            def GetSession(self, j):  # noqa: N802
                return self._ctls[j]

        class FakeMgr:
            def __init__(self, ctls):
                self._enum = FakeSessionEnumerator(ctls)

            def GetSessionEnumerator(self):  # noqa: N802
                return self._enum

        class FakeDevice:
            def __init__(self, sessions):
                self._ctls = [FakeCtl(pid, state) for pid, state in sessions]

            def Activate(self, _iid, _ctx, _params):  # noqa: N802
                return FakeMgr(self._ctls)

        class FakeCollection:
            def __init__(self, devs):
                self._devs = devs

            def GetCount(self):  # noqa: N802
                return len(self._devs)

            def Item(self, i):  # noqa: N802
                return self._devs[i]

        class FakeDeviceEnumerator:
            def __init__(self, devs):
                self._devs = devs

            def EnumAudioEndpoints(self, dataflow, state):  # noqa: N802
                return FakeCollection(self._devs)

        class FakeAudioUtilities:
            @staticmethod
            def GetDeviceEnumerator():  # noqa: N802
                return FakeDeviceEnumerator(
                    [FakeDevice(eps) for eps in devices]
                )

        # pycaw モジュール本体
        fake_pycaw_module = types.ModuleType("pycaw")
        fake_pycaw_inner = types.ModuleType("pycaw.pycaw")
        fake_pycaw_inner.AudioUtilities = FakeAudioUtilities
        fake_pycaw_inner.IAudioMeterInformation = type("_M", (), {})

        # comtypes との連携用ダミー interface。`._iid_` 属性を持つだけでよい。
        class _FakeIface:
            _iid_ = "fake-iid"
        fake_pycaw_inner.IAudioSessionControl2 = _FakeIface
        fake_pycaw_inner.IAudioSessionManager2 = _FakeIface

        monkeypatch.setitem(sys.modules, "pycaw", fake_pycaw_module)
        monkeypatch.setitem(sys.modules, "pycaw.pycaw", fake_pycaw_inner)

        # comtypes を **完全に fake で差し替える**(sys.modules 経由)。
        # 実物の comtypes は import / 属性アクセス時に CoInitializeEx を裏で
        # 呼ぶことがあり、これがメインスレッドで MTA に初期化済の状態だと
        # `RPC_E_CHANGED_MODE`(WinError -2147417850)で失敗してテストが落ちる。
        # 本番コードは `_PeakWorker` 内で呼ばれるので問題にならないが、テストは
        # メインスレッドで `_list_active_sessions` を直接呼ぶため、ここで comtypes
        # を触らないようにダミーモジュールに差し替える。
        fake_comtypes = types.ModuleType("comtypes")
        fake_comtypes.cast = lambda x, _t: x
        fake_comtypes.POINTER = lambda _t: object
        fake_comtypes.CLSCTX_INPROC_SERVER = 1
        monkeypatch.setitem(sys.modules, "comtypes", fake_comtypes)

    def test_excludes_expired_only_accepts_inactive_and_active(self, monkeypatch):
        """Active(1) + Inactive(0) を採用、Expired(2) のみ除外。"""
        # 1 エンドポイントに 3 セッション(Inactive / Active / Expired)
        self._install_fake_pycaw(
            monkeypatch,
            devices=[
                [(100, 0), (200, 1), (300, 2)],
            ],
        )
        result = pe._list_active_sessions()
        assert [info.pid for info in result] == [100, 200]

    def test_excludes_system_session_pid_0(self, monkeypatch):
        """PID 0(システムセッション)は除外。"""
        self._install_fake_pycaw(
            monkeypatch,
            devices=[
                [(0, 1), (123, 1)],
            ],
        )
        result = pe._list_active_sessions()
        assert [info.pid for info in result] == [123]

    def test_collects_sessions_from_all_endpoints(self, monkeypatch):
        """全 Render エンドポイントから集める(2026-06-08 改).

        旧仕様(GetAllSessions のみ)では Device 0 のセッションしか取れず、
        実環境で Device 1 に居る Chrome/Firefox が取りこぼされていた。
        """
        self._install_fake_pycaw(
            monkeypatch,
            devices=[
                # Device 0(デフォルト): システムセッションのみ
                [(0, 0)],
                # Device 1: 実際にアプリが鳴っているデバイス
                [(16820, 0), (20136, 0)],
                # Device 2: 別の出力デバイス(空)
                [],
            ],
        )
        result = pe._list_active_sessions()
        assert sorted(info.pid for info in result) == [16820, 20136]

    def test_dedupes_same_pid_across_endpoints(self, monkeypatch):
        """同じ PID が複数エンドポイントに居る場合は最初の 1 件のみ採用。"""
        self._install_fake_pycaw(
            monkeypatch,
            devices=[
                [(500, 0)],
                [(500, 1)],   # 同じ PID、別エンドポイント
            ],
        )
        result = pe._list_active_sessions()
        assert [info.pid for info in result] == [500]

    def test_excludes_self_pid(self, monkeypatch):
        """自プロセス PID は除外する(フィードバックループ防止)。

        本アプリは TTS 音声を Output デバイスに出すため、自プロセスも WASAPI
        セッションを持つことがある。これをユーザに選ばせると「翻訳音声 → 自分の
        音を再キャプチャ → 再翻訳」の無限ループに陥るため、列挙時点で除外する。
        """
        import os
        my_pid = os.getpid()
        self._install_fake_pycaw(
            monkeypatch,
            devices=[
                [(my_pid, 0), (1234, 0)],
            ],
        )
        result = pe._list_active_sessions()
        # 自プロセスは除外、他プロセスのみ採用
        assert [info.pid for info in result] == [1234]

    def test_device_enumerator_failure_returns_empty(self, monkeypatch):
        """GetDeviceEnumerator が例外を投げたら空リストで返す。"""
        class FakeAudioUtilities:
            @staticmethod
            def GetDeviceEnumerator():  # noqa: N802
                raise OSError("COM init failed")

        fake_pycaw_inner = types.ModuleType("pycaw.pycaw")
        fake_pycaw_inner.AudioUtilities = FakeAudioUtilities
        fake_pycaw_inner.IAudioMeterInformation = type("_M", (), {})
        fake_pycaw_inner.IAudioSessionControl2 = type("_C", (), {"_iid_": "x"})
        fake_pycaw_inner.IAudioSessionManager2 = type("_M2", (), {"_iid_": "y"})
        monkeypatch.setitem(sys.modules, "pycaw", types.ModuleType("pycaw"))
        monkeypatch.setitem(sys.modules, "pycaw.pycaw", fake_pycaw_inner)
        # comtypes も fake で差し替え(`_install_fake_pycaw` 同様の理由 — メイン
        # スレッドで実 comtypes に触ると CoInitializeEx の MTA/STA 競合で落ちる)
        fake_comtypes = types.ModuleType("comtypes")
        fake_comtypes.cast = lambda x, _t: x
        fake_comtypes.POINTER = lambda _t: object
        fake_comtypes.CLSCTX_INPROC_SERVER = 1
        monkeypatch.setitem(sys.modules, "comtypes", fake_comtypes)

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
