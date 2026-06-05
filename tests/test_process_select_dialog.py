"""ProcessSelectDialog / ProcessSelectController の small テスト。

ロジックは Controller を直接テスト(GUI 不要)。
ダイアログ統合は ctk.CTk() が立ち上がる環境でのみ実行(headless は skip)。
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from voice_translator.common.types import CaptureKind, CaptureSource
from voice_translator.gui.process_select_dialog import ProcessSelectController


# ============================================================
# 共通ヘルパ
# ============================================================
def _src(pid: int, name: str = "app.exe") -> CaptureSource:
    return CaptureSource(
        source_id=str(pid),
        display_name=f"{name} ({pid})",
        kind=CaptureKind.PROCESS,
    )


@dataclass
class _Meter:
    peak: float = 0.0
    fail: bool = False

    def GetPeakValue(self) -> float:  # noqa: N802
        if self.fail:
            raise OSError("meter blew up")
        return self.peak


# ============================================================
# refresh / 選択
# ============================================================
class TestRefreshAndSelection:
    def test_refresh_loads_sources(self):
        ctrl = ProcessSelectController(
            enumerator=lambda: [_src(1, "a"), _src(2, "b")],
            meter_getter=lambda pid: _Meter(),
        )
        ctrl.refresh()
        assert [s.source_id for s in ctrl.sources] == ["1", "2"]

    def test_refresh_clears_selection_when_pid_disappears(self):
        sources_seq = [[_src(10, "x"), _src(20, "y")], [_src(20, "y")]]
        idx = {"i": 0}

        def enum():
            r = sources_seq[idx["i"]]
            idx["i"] = min(idx["i"] + 1, len(sources_seq) - 1)
            return r

        ctrl = ProcessSelectController(enumerator=enum, meter_getter=lambda pid: _Meter())
        ctrl.refresh()
        ctrl.set_selected_pid(10)
        assert ctrl.selected_pid == 10
        # 2 回目の refresh で 10 が消える
        ctrl.refresh()
        assert ctrl.selected_pid is None

    def test_refresh_keeps_selection_when_still_present(self):
        ctrl = ProcessSelectController(
            enumerator=lambda: [_src(7), _src(8)], meter_getter=lambda pid: _Meter(),
        )
        ctrl.refresh()
        ctrl.set_selected_pid(8)
        ctrl.refresh()  # 列挙は同じ
        assert ctrl.selected_pid == 8

    def test_set_selected_pid_stops_audition(self):
        ctrl = ProcessSelectController(
            enumerator=lambda: [_src(1), _src(2)], meter_getter=lambda pid: _Meter(peak=0.5),
        )
        ctrl.refresh()
        ctrl.set_selected_pid(1)
        ctrl.start_audition()
        assert ctrl.is_auditioning is True
        # 別行に変更 → 試聴停止
        ctrl.set_selected_pid(2)
        assert ctrl.is_auditioning is False


# ============================================================
# 試聴 ON/OFF
# ============================================================
class TestAudition:
    def test_start_without_selection_returns_false(self):
        ctrl = ProcessSelectController(
            enumerator=lambda: [_src(1)], meter_getter=lambda pid: _Meter(),
        )
        ctrl.refresh()
        # 未選択
        assert ctrl.start_audition() is False
        assert ctrl.is_auditioning is False

    def test_start_when_meter_none_returns_false(self):
        ctrl = ProcessSelectController(
            enumerator=lambda: [_src(1)], meter_getter=lambda pid: None,
        )
        ctrl.refresh()
        ctrl.set_selected_pid(1)
        assert ctrl.start_audition() is False
        assert ctrl.is_auditioning is False

    def test_start_success(self):
        ctrl = ProcessSelectController(
            enumerator=lambda: [_src(1)], meter_getter=lambda pid: _Meter(peak=0.3),
        )
        ctrl.refresh()
        ctrl.set_selected_pid(1)
        assert ctrl.start_audition() is True
        assert ctrl.is_auditioning is True

    def test_stop_resets_state(self):
        ctrl = ProcessSelectController(
            enumerator=lambda: [_src(1)], meter_getter=lambda pid: _Meter(peak=0.8),
        )
        ctrl.refresh()
        ctrl.set_selected_pid(1)
        ctrl.start_audition()
        ctrl.tick()
        ctrl.stop_audition()
        assert ctrl.is_auditioning is False
        assert ctrl.current_peak == 0.0


# ============================================================
# tick (peak + decay)
# ============================================================
class TestTickAndDecay:
    def test_tick_when_off_decays_residual(self):
        ctrl = ProcessSelectController(
            enumerator=lambda: [_src(1)], meter_getter=lambda pid: _Meter(peak=0.9), decay=0.5,
        )
        # 試聴 OFF のまま tick: peak は 0 のまま(残光なし)
        ctrl.refresh()
        ctrl.set_selected_pid(1)
        assert ctrl.tick() == 0.0

    def test_tick_when_on_reads_peak(self):
        meter = _Meter(peak=0.4)
        ctrl = ProcessSelectController(
            enumerator=lambda: [_src(1)], meter_getter=lambda pid: meter, decay=0.5,
        )
        ctrl.refresh()
        ctrl.set_selected_pid(1)
        ctrl.start_audition()
        assert ctrl.tick() == pytest.approx(0.4)

    def test_tick_decay_holds_recent_peak(self):
        meter = _Meter(peak=0.9)
        ctrl = ProcessSelectController(
            enumerator=lambda: [_src(1)], meter_getter=lambda pid: meter, decay=0.5,
        )
        ctrl.refresh()
        ctrl.set_selected_pid(1)
        ctrl.start_audition()
        ctrl.tick()             # peak=0.9
        meter.peak = 0.1        # 急減
        v = ctrl.tick()         # max(0.1, 0.9*0.5=0.45) → 0.45
        assert v == pytest.approx(0.45)

    def test_tick_handles_meter_exception_as_zero(self):
        meter = _Meter(peak=0.7, fail=True)
        ctrl = ProcessSelectController(
            enumerator=lambda: [_src(1)], meter_getter=lambda pid: meter, decay=0.5,
        )
        ctrl.refresh()
        ctrl.set_selected_pid(1)
        ctrl.start_audition()
        ctrl.tick()  # peak は 0 として扱われる
        assert ctrl.current_peak == 0.0


# ============================================================
# GUI 統合(headless 環境では skip)
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


class TestDialogIntegration:
    def _make_dialog(self, root, *, initial_pid=None, sources=None, meter=None):
        from voice_translator.gui.process_select_dialog import (
            ProcessSelectController,
            ProcessSelectDialog,
        )

        ctrl = ProcessSelectController(
            enumerator=lambda: sources or [],
            meter_getter=lambda pid: meter,
        )
        return ProcessSelectDialog(root, initial_pid=initial_pid, controller=ctrl)

    def test_dialog_opens_and_lists_sources(self, root):
        dlg = self._make_dialog(root, sources=[_src(1, "a"), _src(2, "b")])
        # row widgets: 2 件のラジオが生成される
        assert len(dlg._row_widgets) == 2  # noqa: SLF001
        # poll を確実にキャンセルしてから閉じる(`destroy()` 直呼びだと after キューが残る)
        dlg._on_cancel()  # noqa: SLF001

    def test_dialog_empty_state(self, root):
        dlg = self._make_dialog(root, sources=[])
        # 「該当プロセスなし」ラベル 1 件
        assert len(dlg._row_widgets) == 1  # noqa: SLF001
        dlg._on_cancel()  # noqa: SLF001

    def test_dialog_ok_returns_selected_pid(self, root):
        dlg = self._make_dialog(root, sources=[_src(42, "x")])
        dlg._selection_var.set("42")  # noqa: SLF001
        dlg._on_selection_change()    # noqa: SLF001
        dlg._on_ok()                  # noqa: SLF001
        assert dlg.result_pid == 42

    def test_dialog_cancel_returns_none(self, root):
        dlg = self._make_dialog(root, sources=[_src(1)])
        dlg._selection_var.set("1")   # noqa: SLF001
        dlg._on_selection_change()    # noqa: SLF001
        dlg._on_cancel()              # noqa: SLF001
        assert dlg.result_pid is None
