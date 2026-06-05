"""ProcessSelectDialog / ProcessSelectController の small テスト(provider 版)。

ロジックは Controller を fake `_PeakProvider` で直接テスト(GUI 不要)。
ダイアログ統合は ctk.CTk() が立ち上がる環境でのみ実行(headless は skip)。
"""

from __future__ import annotations

from dataclasses import dataclass, field

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
class _FakeProvider:
    """テスト用 `_PeakProvider` 実装。

    - `sources` フィールドで列挙結果を制御
    - `start_audition` で受け付ける PID 集合 `auditable_pids` を制御
    - `peak_value` フィールドで `latest_peak()` の戻り値を制御
    """

    sources: list[CaptureSource] = field(default_factory=list)
    auditable_pids: set[int] = field(default_factory=set)
    peak_value: float = 0.0
    enumerate_calls: int = 0
    start_calls: list[int] = field(default_factory=list)
    stop_calls: int = 0
    raise_on_peak: bool = False

    def enumerate(self):
        self.enumerate_calls += 1
        return list(self.sources)

    def start_audition(self, pid: int) -> bool:
        self.start_calls.append(pid)
        return pid in self.auditable_pids

    def stop_audition(self) -> None:
        self.stop_calls += 1

    def latest_peak(self) -> float:
        if self.raise_on_peak:
            raise OSError("peak read failed")
        return self.peak_value


# ============================================================
# refresh / 選択
# ============================================================
class TestRefreshAndSelection:
    def test_refresh_loads_sources(self):
        prov = _FakeProvider(sources=[_src(1), _src(2)])
        ctrl = ProcessSelectController(provider=prov)
        ctrl.refresh()
        assert [s.source_id for s in ctrl.sources] == ["1", "2"]
        assert prov.enumerate_calls == 1

    def test_refresh_clears_selection_when_pid_disappears(self):
        prov = _FakeProvider(sources=[_src(10), _src(20)])
        ctrl = ProcessSelectController(provider=prov)
        ctrl.refresh()
        ctrl.set_selected_pid(10)
        # 列挙結果を狭める
        prov.sources = [_src(20)]
        ctrl.refresh()
        assert ctrl.selected_pid is None

    def test_refresh_keeps_selection_when_still_present(self):
        prov = _FakeProvider(sources=[_src(7), _src(8)])
        ctrl = ProcessSelectController(provider=prov)
        ctrl.refresh()
        ctrl.set_selected_pid(8)
        ctrl.refresh()  # 列挙は同じ
        assert ctrl.selected_pid == 8

    def test_set_selected_pid_stops_audition(self):
        prov = _FakeProvider(sources=[_src(1), _src(2)], auditable_pids={1})
        ctrl = ProcessSelectController(provider=prov)
        ctrl.refresh()
        ctrl.set_selected_pid(1)
        ctrl.start_audition()
        assert ctrl.is_auditioning is True
        ctrl.set_selected_pid(2)
        # 別行に変更 → 試聴停止 + provider.stop_audition が呼ばれる
        assert ctrl.is_auditioning is False
        assert prov.stop_calls == 1


# ============================================================
# 試聴 ON/OFF
# ============================================================
class TestAudition:
    def test_start_without_selection_returns_false(self):
        prov = _FakeProvider(sources=[_src(1)], auditable_pids={1})
        ctrl = ProcessSelectController(provider=prov)
        ctrl.refresh()
        # 未選択
        assert ctrl.start_audition() is False
        assert ctrl.is_auditioning is False
        # provider.start_audition は呼ばれない(未選択時の防衛)
        assert prov.start_calls == []

    def test_start_when_provider_rejects_returns_false(self):
        # auditable_pids が空 = provider が常に False を返す
        prov = _FakeProvider(sources=[_src(1)], auditable_pids=set())
        ctrl = ProcessSelectController(provider=prov)
        ctrl.refresh()
        ctrl.set_selected_pid(1)
        assert ctrl.start_audition() is False
        assert ctrl.is_auditioning is False
        assert prov.start_calls == [1]

    def test_start_success(self):
        prov = _FakeProvider(sources=[_src(1)], auditable_pids={1}, peak_value=0.3)
        ctrl = ProcessSelectController(provider=prov)
        ctrl.refresh()
        ctrl.set_selected_pid(1)
        assert ctrl.start_audition() is True
        assert ctrl.is_auditioning is True

    def test_stop_resets_state(self):
        prov = _FakeProvider(sources=[_src(1)], auditable_pids={1}, peak_value=0.8)
        ctrl = ProcessSelectController(provider=prov)
        ctrl.refresh()
        ctrl.set_selected_pid(1)
        ctrl.start_audition()
        ctrl.tick()
        ctrl.stop_audition()
        assert ctrl.is_auditioning is False
        assert ctrl.current_peak == 0.0
        assert prov.stop_calls == 1


# ============================================================
# tick (peak + decay)
# ============================================================
class TestTickAndDecay:
    def test_tick_when_off_decays_residual(self):
        prov = _FakeProvider(peak_value=0.9)
        ctrl = ProcessSelectController(provider=prov, decay=0.5)
        ctrl.refresh()
        # 試聴 OFF のまま tick: peak は 0 のまま(残光なし)
        assert ctrl.tick() == 0.0

    def test_tick_when_on_reads_peak(self):
        prov = _FakeProvider(sources=[_src(1)], auditable_pids={1}, peak_value=0.4)
        ctrl = ProcessSelectController(provider=prov, decay=0.5)
        ctrl.refresh()
        ctrl.set_selected_pid(1)
        ctrl.start_audition()
        assert ctrl.tick() == pytest.approx(0.4)

    def test_tick_decay_holds_recent_peak(self):
        prov = _FakeProvider(sources=[_src(1)], auditable_pids={1}, peak_value=0.9)
        ctrl = ProcessSelectController(provider=prov, decay=0.5)
        ctrl.refresh()
        ctrl.set_selected_pid(1)
        ctrl.start_audition()
        ctrl.tick()  # peak=0.9
        prov.peak_value = 0.1  # 急減
        v = ctrl.tick()  # max(0.1, 0.9*0.5=0.45) → 0.45
        assert v == pytest.approx(0.45)

    def test_tick_handles_provider_exception_as_zero(self):
        prov = _FakeProvider(sources=[_src(1)], auditable_pids={1}, peak_value=0.7,
                             raise_on_peak=True)
        ctrl = ProcessSelectController(provider=prov, decay=0.5)
        ctrl.refresh()
        ctrl.set_selected_pid(1)
        ctrl.start_audition()
        ctrl.tick()  # provider が例外 → raw_peak=0 として扱う
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
    def _make_dialog(self, root, *, initial_pid=None, sources=None, auditable_pids=None):
        from voice_translator.gui.process_select_dialog import ProcessSelectDialog

        prov = _FakeProvider(
            sources=sources or [],
            auditable_pids=auditable_pids or set(),
        )
        ctrl = ProcessSelectController(provider=prov)
        return ProcessSelectDialog(root, initial_pid=initial_pid, controller=ctrl)

    def test_dialog_opens_and_lists_sources(self, root):
        dlg = self._make_dialog(root, sources=[_src(1, "a"), _src(2, "b")])
        assert len(dlg._row_widgets) == 2  # noqa: SLF001
        dlg._on_cancel()  # noqa: SLF001

    def test_dialog_empty_state(self, root):
        dlg = self._make_dialog(root, sources=[])
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
