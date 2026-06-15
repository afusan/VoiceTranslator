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

試聴経路(2026-06-05 リファクタ後):
- 本番の `ProcessAudioCapture`(WASAPI Process Loopback)は使わず、pycaw の
  `IAudioMeterInformation.GetPeakValue()` を **永続 COM ワーカスレッド** が 5fps で内部
  poll し、最新値を atomic に保持する。
- GUI スレッドはワーカが保持する `latest_peak()` を atomic 読みするだけ。スレッド
  境界をまたがず軽量。WASAPI ストリームを開かないので本番パイプラインと完全独立。
- 詳細は `capture/process_enumerator.py` の `_PeakWorker` 参照。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Callable, Optional, Protocol

import customtkinter as ctk

from voice_translator.capture import process_enumerator as pe
from voice_translator.common.types import CaptureSource
from voice_translator.gui.i18n import tr

if TYPE_CHECKING:
    pass


_POLL_INTERVAL_MS = 100  # 10fps の GUI 再描画(ワーカ側 5fps poll より頻度高め = フィルタ安定)
_DEFAULT_DECAY = 0.85    # VU メータ風: 急上昇・緩降下


# ============================================================
# Peak 供給インタフェース(差し替え可能)
# ============================================================
class _PeakProvider(Protocol):
    """試聴 peak 供給の Protocol。本番は `process_enumerator` モジュール、テストは fake。

    モジュール直の関数(`pe.enumerate_active_processes` 等)を関数ポインタで束ねた
    duck-typed な provider として使う。`ProcessSelectController` はこの Protocol だけに
    依存し、`process_enumerator` の永続ワーカ実装に直結しない。
    """

    def enumerate(self) -> list[CaptureSource]: ...
    def start_audition(self, pid: int) -> bool: ...
    def stop_audition(self) -> None: ...
    def latest_peak(self) -> float: ...


class _DefaultProvider:
    """本番経路。`process_enumerator` モジュールの公開 API を Protocol に束ねる。"""

    def enumerate(self) -> list[CaptureSource]:
        return pe.enumerate_active_processes()

    def start_audition(self, pid: int) -> bool:
        return pe.start_audition(pid)

    def stop_audition(self) -> None:
        pe.stop_audition()

    def latest_peak(self) -> float:
        return pe.latest_peak()


# ============================================================
# ロジック層(GUI 不要でテスト可能)
# ============================================================
class ProcessSelectController:
    """プロセス選択ダイアログの状態とロジックを保持する。

    役割: 列挙 / 選択 / 試聴 ON-OFF / peak decay の状態機械を 1 つに集約し、
    UI から GUI 非依存に呼べるようにする。テストは本クラスを直接生成して
    `provider`(`_PeakProvider`) を差し替えで検証できる。

    引数:
        provider: peak 供給 Protocol 実装。未指定なら `_DefaultProvider`(本番経路)。
        decay: peak の VU メータ風 decay 係数(0.0〜1.0、デフォルト 0.85)
    """

    def __init__(
        self,
        *,
        provider: Optional[_PeakProvider] = None,
        decay: float = _DEFAULT_DECAY,
    ) -> None:
        self._provider: _PeakProvider = provider if provider is not None else _DefaultProvider()
        self._decay = decay
        self._sources: list[CaptureSource] = []
        self._selected_pid: int | None = None
        self._auditioning: bool = False
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
        self._sources = list(self._provider.enumerate())
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
        """試聴開始。選択 PID が無い・メータが取れないときは False を返す。

        provider.start_audition(pid) を呼んでワーカ側 poll を始動させる。GUI 側は
        以降 `latest_peak()`(via tick)を atomic 読みするだけ。
        """
        if self._selected_pid is None:
            return False
        if not self._provider.start_audition(self._selected_pid):
            return False
        self._auditioning = True
        self._current_peak = 0.0
        return True

    def stop_audition(self) -> None:
        """試聴停止。ワーカ側 poll を止めて、表示 peak も即時 0。"""
        if self._auditioning:
            self._provider.stop_audition()
        self._auditioning = False
        self._current_peak = 0.0

    def tick(self) -> float:
        """poll の 1 ティック。最新 peak を atomic 読みして decay 適用、表示値を返す。

        試聴 OFF の場合は decay のみ適用(残光のフェードアウト)。
        provider 例外時は peak=0 として decay 継続。
        """
        if not self._auditioning:
            self._current_peak = self._current_peak * self._decay
            if self._current_peak < 1e-4:
                self._current_peak = 0.0
            return self._current_peak

        try:
            raw_peak = float(self._provider.latest_peak())
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

        self.title(tr("dialog.process_select.title"))
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
            self, text=tr("dialog.process_select.heading"), font=("", 16, "bold"),
        ).grid(row=0, column=0, columnspan=3, sticky="w", padx=12, pady=(12, 4))

        ctk.CTkLabel(
            self,
            text=tr("dialog.process_select.description"),
            text_color="#94a3b8", wraplength=480, justify="left", anchor="w",
        ).grid(row=1, column=0, columnspan=3, sticky="ew", padx=12, pady=(0, 8))

        # 更新ボタン(列挙し直し)
        self._refresh_btn = ctk.CTkButton(
            self, text=tr("dialog.process_select.refresh"), width=90, command=self._do_refresh,
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
            self, text=tr("dialog.process_select.audition_start"), width=120,
            command=self._on_audition_toggle,
        )
        self._audition_btn.grid(row=4, column=0, sticky="w", padx=12, pady=4)

        self._peak_bar = ctk.CTkProgressBar(self)
        self._peak_bar.set(0.0)
        self._peak_bar.grid(row=4, column=1, columnspan=2, sticky="ew", padx=12, pady=4)

        # OK / Cancel
        btn_frame = ctk.CTkFrame(self, fg_color="transparent")
        btn_frame.grid(row=5, column=0, columnspan=3, sticky="e", padx=12, pady=(8, 12))
        ctk.CTkButton(btn_frame, text=tr("common.cancel"), width=90, command=self._on_cancel).pack(
            side="right", padx=(8, 0)
        )
        ctk.CTkButton(btn_frame, text=tr("common.ok"), width=90, command=self._on_ok).pack(
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
                self._list_frame, text=tr("dialog.process_select.no_process"),
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
            self._audition_btn.configure(text=tr("dialog.process_select.audition_stop"))
        else:
            self._audition_btn.configure(text=tr("dialog.process_select.audition_start"))

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
