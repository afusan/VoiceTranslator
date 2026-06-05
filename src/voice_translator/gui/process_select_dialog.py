"""ProcessSelectDialog: per-process キャプチャ用のプロセス選択ダイアログ(段階 3)。

役割: ProcTap backend(プロセス単位キャプチャ)の取得元 PID を、レベルメータでの
試聴付きで選択する。SettingsPanel の「プロセス選択…」ボタンから呼ばれる。

構成:
- 列挙テーブル(プロセス名 + PID、ラジオ選択)
- 「↻ 更新」ボタン
- 「▶ 試聴開始」/「■ 停止」トグル
- レベルメータ(`CTkProgressBar`)
- OK / Cancel

ロジックは `ProcessSelectController` に切り出し、ダイアログ本体はそれを呼んで描画する
だけ。これにより GUI 不要のロジック単体テストが書けるようにする(設計判断:
`docs/design/feature-proctap-process-list/Plan.md` 2-1〜2-5 参照)。

試聴経路:
- 本番の `ProcessAudioCapture`(WASAPI Process Loopback)は使わず、pycaw の
  `IAudioMeterInformation.GetPeakValue()` を 30fps poll するのみ。
- WASAPI ストリームを開かないので超軽量。本番パイプラインと完全に独立。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Callable, Optional

import customtkinter as ctk

from voice_translator.capture import process_enumerator as pe
from voice_translator.common.types import CaptureSource

if TYPE_CHECKING:
    pass


_POLL_INTERVAL_MS = 33  # ~30fps
_DEFAULT_DECAY = 0.85   # VU メータ風: 急上昇・緩降下


# ============================================================
# ロジック層(GUI 不要でテスト可能)
# ============================================================
class ProcessSelectController:
    """プロセス選択ダイアログの状態とロジックを保持する。

    役割: 列挙 / 選択 / 試聴 ON-OFF / peak decay の状態機械を 1 つに集約し、
    UI から GUI 非依存に呼べるようにする。テストは本クラスを直接生成して
    enumerator / meter_getter を差し替えで検証できる。

    引数:
        enumerator: `enumerate_active_processes` 互換の関数(差替えポイント)
        meter_getter: `get_session_meter(pid)` 互換の関数(差替えポイント)
        decay: peak の VU メータ風 decay 係数(0.0〜1.0、デフォルト 0.85)
    """

    def __init__(
        self,
        *,
        enumerator: Optional[Callable[[], list[CaptureSource]]] = None,
        meter_getter: Optional[Callable[[int], object]] = None,
        decay: float = _DEFAULT_DECAY,
    ) -> None:
        self._enumerate = enumerator if enumerator is not None else pe.enumerate_active_processes
        self._get_meter = meter_getter if meter_getter is not None else pe.get_session_meter
        self._decay = decay
        self._sources: list[CaptureSource] = []
        self._selected_pid: int | None = None
        self._auditioning: bool = False
        self._current_meter: object | None = None
        self._current_peak: float = 0.0

    # ---- 公開状態 -----------------------------------------------------------
    @property
    def sources(self) -> list[CaptureSource]:
        return list(self._sources)

    @property
    def selected_pid(self) -> int | None:
        return self._selected_pid

    @property
    def is_auditioning(self) -> bool:
        return self._auditioning

    @property
    def current_peak(self) -> float:
        return self._current_peak

    # ---- 操作 ---------------------------------------------------------------
    def refresh(self) -> None:
        """列挙し直す。選択中 PID が新リストに無ければ未選択に戻し、試聴も停止。"""
        self._sources = list(self._enumerate())
        if self._selected_pid is not None and not any(
            self._to_pid(s) == self._selected_pid for s in self._sources
        ):
            self.set_selected_pid(None)

    def set_selected_pid(self, pid: int | None) -> None:
        """選択 PID を更新。変わった場合は試聴を停止(別行を選ぶたびに poll をリセット)。"""
        if pid == self._selected_pid:
            return
        was_auditioning = self._auditioning
        self._selected_pid = pid
        if was_auditioning:
            self.stop_audition()

    def start_audition(self) -> bool:
        """試聴開始。選択 PID が無い・メータが取れないときは False を返す。"""
        if self._selected_pid is None:
            return False
        meter = self._get_meter(self._selected_pid)
        if meter is None:
            return False
        self._current_meter = meter
        self._auditioning = True
        self._current_peak = 0.0
        return True

    def stop_audition(self) -> None:
        """試聴停止。peak は decay 経由でゼロに落とすのではなく即時 0 に。"""
        self._auditioning = False
        self._current_meter = None
        self._current_peak = 0.0

    def tick(self) -> float:
        """poll の 1 ティック。最新 peak を取って decay 適用、現在の表示値を返す。

        試聴 OFF の場合は decay のみ適用(残光のフェードアウト)。
        meter 例外時は peak=0 として decay 継続。
        """
        if not self._auditioning or self._current_meter is None:
            self._current_peak = self._current_peak * self._decay
            if self._current_peak < 1e-4:
                self._current_peak = 0.0
            return self._current_peak

        try:
            raw_peak = float(self._current_meter.GetPeakValue())
        except Exception:
            raw_peak = 0.0
        self._current_peak = max(raw_peak, self._current_peak * self._decay)
        return self._current_peak

    # ---- ヘルパ -------------------------------------------------------------
    @staticmethod
    def _to_pid(src: CaptureSource) -> int | None:
        try:
            return int(src.source_id)
        except (TypeError, ValueError):
            return None


# ============================================================
# GUI 層
# ============================================================
class ProcessSelectDialog(ctk.CTkToplevel):
    """プロセス選択 + 試聴メータ表示ダイアログ。

    使い方:
        dlg = ProcessSelectDialog(parent, initial_pid=current_pid)
        dlg.wait_window()
        if dlg.result_pid is not None:
            ... # OK が押された
        else:
            ... # Cancel / 閉じる
    """

    def __init__(
        self,
        parent,
        *,
        initial_pid: int | None = None,
        controller: ProcessSelectController | None = None,
    ) -> None:
        super().__init__(parent)
        self._ctrl = controller if controller is not None else ProcessSelectController()
        self._poll_after_id: str | None = None
        self.result_pid: int | None = None
        # ラジオ選択用の StringVar(値は str(pid))
        self._selection_var = ctk.StringVar(value=str(initial_pid) if initial_pid else "")

        self.title("プロセス選択 — ProcTap")
        self.geometry("520x520")
        self.transient(parent)
        try:
            self.grab_set()
        except Exception:  # noqa: BLE001
            pass

        self._build_widgets()
        self.protocol("WM_DELETE_WINDOW", self._on_cancel)

        # 初期 enumerate + 初期選択を controller に反映
        self._do_refresh()
        if initial_pid is not None:
            self._ctrl.set_selected_pid(initial_pid)
            self._selection_var.set(str(initial_pid))
        # poll 開始(試聴 OFF でも decay 表示のために回す。負荷は微小)
        self._schedule_poll()

    # ----------------------------------------------------------
    def _build_widgets(self) -> None:
        ctk.CTkLabel(
            self, text="音声出力中のプロセス", font=("", 16, "bold"),
        ).grid(row=0, column=0, columnspan=3, sticky="w", padx=12, pady=(12, 4))

        ctk.CTkLabel(
            self,
            text=(
                "現在音を出している(または出す準備のできた)プロセスを表示しています。\n"
                "選択して「試聴開始」で当該プロセスの音量が右のメータで確認できます。"
            ),
            text_color="#94a3b8", wraplength=480, justify="left", anchor="w",
        ).grid(row=1, column=0, columnspan=3, sticky="ew", padx=12, pady=(0, 8))

        # 更新ボタン(列挙し直し)
        self._refresh_btn = ctk.CTkButton(
            self, text="↻ 更新", width=90, command=self._do_refresh,
        )
        self._refresh_btn.grid(row=2, column=2, sticky="e", padx=12, pady=(0, 4))

        # プロセス一覧(スクロール可能)
        self._list_frame = ctk.CTkScrollableFrame(self, height=240)
        self._list_frame.grid(
            row=3, column=0, columnspan=3, sticky="nsew", padx=12, pady=(0, 8),
        )
        # 一覧の行ウィジェット(refresh 時に毎回作り直す)
        self._row_widgets: list[ctk.CTkRadioButton] = []

        # 試聴トグル + メータ
        self._audition_btn = ctk.CTkButton(
            self, text="▶ 試聴開始", width=120, command=self._on_audition_toggle,
        )
        self._audition_btn.grid(row=4, column=0, sticky="w", padx=12, pady=4)

        self._peak_bar = ctk.CTkProgressBar(self)
        self._peak_bar.set(0.0)
        self._peak_bar.grid(row=4, column=1, columnspan=2, sticky="ew", padx=12, pady=4)

        # OK / Cancel
        btn_frame = ctk.CTkFrame(self, fg_color="transparent")
        btn_frame.grid(row=5, column=0, columnspan=3, sticky="e", padx=12, pady=(8, 12))
        ctk.CTkButton(btn_frame, text="Cancel", width=90, command=self._on_cancel).pack(
            side="right", padx=(8, 0)
        )
        ctk.CTkButton(btn_frame, text="OK", width=90, command=self._on_ok).pack(
            side="right"
        )

        self.columnconfigure(1, weight=1)
        self.rowconfigure(3, weight=1)

    # ----------------------------------------------------------
    def _do_refresh(self) -> None:
        self._ctrl.refresh()
        # 既存行ウィジェットを除去
        for w in self._row_widgets:
            try:
                w.destroy()
            except Exception:  # noqa: BLE001
                pass
        self._row_widgets.clear()

        if not self._ctrl.sources:
            empty_label = ctk.CTkLabel(
                self._list_frame, text="(該当プロセスなし — 音を鳴らしてから ↻ 更新)",
                text_color="#94a3b8",
            )
            empty_label.pack(anchor="w", padx=4, pady=8)
            self._row_widgets.append(empty_label)  # type: ignore[arg-type]
            self._selection_var.set("")
            return

        # 現在の選択が新リストに無ければ未選択に
        current = self._selection_var.get()
        if current and not any(s.source_id == current for s in self._ctrl.sources):
            self._selection_var.set("")
            self._ctrl.set_selected_pid(None)

        for src in self._ctrl.sources:
            rb = ctk.CTkRadioButton(
                self._list_frame, text=src.display_name,
                variable=self._selection_var, value=src.source_id,
                command=self._on_selection_change,
            )
            rb.pack(anchor="w", padx=4, pady=2)
            self._row_widgets.append(rb)

    def _on_selection_change(self) -> None:
        val = self._selection_var.get()
        pid = int(val) if val else None
        self._ctrl.set_selected_pid(pid)
        # 試聴ボタンのラベルを controller の状態で同期(set_selected_pid が試聴停止する場合あり)
        self._sync_audition_button()

    def _on_audition_toggle(self) -> None:
        if self._ctrl.is_auditioning:
            self._ctrl.stop_audition()
        else:
            self._ctrl.start_audition()
        self._sync_audition_button()

    def _sync_audition_button(self) -> None:
        if self._ctrl.is_auditioning:
            self._audition_btn.configure(text="■ 停止")
        else:
            self._audition_btn.configure(text="▶ 試聴開始")

    # ----------------------------------------------------------
    def _schedule_poll(self) -> None:
        try:
            self._poll_after_id = self.after(_POLL_INTERVAL_MS, self._tick_poll)
        except Exception:  # noqa: BLE001 - widget 破棄済み
            pass

    def _tick_poll(self) -> None:
        peak = self._ctrl.tick()
        try:
            self._peak_bar.set(min(1.0, max(0.0, peak)))
        except Exception:  # noqa: BLE001
            return
        self._schedule_poll()

    def _cancel_poll(self) -> None:
        if self._poll_after_id is not None:
            try:
                self.after_cancel(self._poll_after_id)
            except Exception:  # noqa: BLE001
                pass
            self._poll_after_id = None

    # ----------------------------------------------------------
    def _on_ok(self) -> None:
        pid = self._ctrl.selected_pid
        self.result_pid = pid
        self._close()

    def _on_cancel(self) -> None:
        self.result_pid = None
        self._close()

    def _close(self) -> None:
        self._cancel_poll()
        try:
            self._ctrl.stop_audition()
        except Exception:  # noqa: BLE001
            pass
        try:
            self.grab_release()
        except Exception:  # noqa: BLE001
            pass
        self.destroy()
