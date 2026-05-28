"""ControlPanel: 動作開始/停止と直近結果の表示UI(customtkinter)。

役割: Start/Stop トグル、モデル状態の集約観測、直近の翻訳テキスト表示、レイテンシ表示。
モデルロードはアプリ起動時/設定変更時に自動で走るので、開始ボタンは "全レイヤ LOADED" の
ときだけ有効化する。AppController.start_pipeline_async() を使い、UIをブロックしない。
"""

from __future__ import annotations

from collections import deque

import customtkinter as ctk

from voice_translator.common.app_controller import AppController
from voice_translator.common.types import LayerKind, ModelStatus


class ControlPanel(ctk.CTkFrame):
    """動作操作と直近結果を表示するパネル。"""

    HISTORY_SIZE = 5

    def __init__(self, master, controller: AppController, settings_panel=None) -> None:
        super().__init__(master)
        self._controller = controller
        self._settings_panel = settings_panel  # 共有: SettingsPanel.on_status_change を呼ぶため
        self._state: str = "idle"  # idle / loading / running / stopping
        self._latencies: deque[float] = deque(maxlen=10)

        # 各レイヤの現在のステータス(初期値は AppController から取得)
        self._layer_statuses: dict[LayerKind, ModelStatus] = dict(
            self._controller.get_all_model_statuses()
        )

        self._build_widgets()
        # AppController からのコールバックを受け取る
        self._controller.set_callbacks(
            on_utterance_done=self._on_utterance_from_thread,
            on_fatal=self._on_fatal_from_thread,
            on_warn=self._on_warn_from_thread,
            on_status_change=self._on_status_from_thread,
        )
        # 初期状態(モデル未ロード)を反映: ボタンを準備中にする
        self._sync_ready_state()

    # ============================================================
    def _build_widgets(self) -> None:
        ctk.CTkLabel(self, text="動作", font=("", 16, "bold")).grid(
            row=0, column=0, columnspan=3, sticky="w", padx=10, pady=(8, 4)
        )

        self._toggle_btn = ctk.CTkButton(
            self, text="▶ 開始", width=140, command=self._on_toggle
        )
        self._toggle_btn.grid(row=1, column=0, padx=10, pady=8, sticky="w")

        self._status_label = ctk.CTkLabel(self, text="停止中")
        self._status_label.grid(row=1, column=1, padx=10, pady=8, sticky="w")

        self._latency_label = ctk.CTkLabel(self, text="平均レイテンシ: -")
        self._latency_label.grid(row=1, column=2, padx=10, pady=8, sticky="e")

        # 履歴ラベル + クリアボタン(同じ行に配置)
        ctk.CTkLabel(self, text="最近の翻訳:").grid(
            row=2, column=0, sticky="w", padx=10, pady=(8, 0)
        )
        self._clear_btn = ctk.CTkButton(
            self, text="クリア", width=80, command=self._on_clear_history
        )
        self._clear_btn.grid(row=2, column=2, sticky="e", padx=10, pady=(8, 0))

        self._history_text = ctk.CTkTextbox(self, height=360, wrap="word")
        self._history_text.grid(row=3, column=0, columnspan=3, sticky="nsew", padx=10, pady=4)
        self._history_text.configure(state="disabled")

        self.columnconfigure(1, weight=1)
        # 履歴ボックスをウィンドウ拡大時に伸ばす
        self.rowconfigure(3, weight=1)

    # ============================================================
    def _on_toggle(self) -> None:
        if self._state == "running":
            self._do_stop()
        elif self._state == "idle":
            self._do_start_async()
        # loading / stopping 中は何もしない(ボタン disable で防御)

    def _do_start_async(self) -> None:
        """処理スレッドを起動する(モデルは事前ロード済みの前提)。

        全レイヤが LOADED でないときは _sync_ready_state でボタンが無効化されており、
        ここには来ない。即時の例外(デバイスバリデーション等)だけ捕捉。
        """
        try:
            self._controller.start_pipeline_async(
                on_started=self._on_loader_started,
                on_failed=self._on_loader_failed,
            )
        except Exception as e:  # noqa: BLE001
            self._append_history(f"[起動失敗] {e}")
            return
        self._state = "starting"
        self._toggle_btn.configure(text="開始中…", state="disabled")
        self._status_label.configure(text="開始中…")

    def _do_stop(self) -> None:
        self._state = "stopping"
        self._toggle_btn.configure(text="停止中…", state="disabled")
        self._status_label.configure(text="停止中…")
        try:
            self._controller.stop_pipeline()
        except Exception as e:  # noqa: BLE001
            self._append_history(f"[停止時例外] {e}")
        self._state = "idle"
        # 停止後はバックエンドが残っているので即 ready 状態に戻す
        self._sync_ready_state()

    # ============================================================
    # Loader からのコールバック
    # ============================================================
    def _on_loader_started(self) -> None:
        self.after(0, self._apply_loader_started)

    def _on_loader_failed(self, message: str) -> None:
        self.after(0, lambda: self._apply_loader_failed(message))

    def _apply_loader_started(self) -> None:
        self._state = "running"
        self._toggle_btn.configure(text="■ 停止", state="normal")
        self._status_label.configure(text="動作中")

    def _apply_loader_failed(self, message: str) -> None:
        self._append_history(f"[起動失敗] {message}")
        self._state = "idle"
        # 起動失敗時は現在のレイヤ状態を見て ready 表示を更新
        self._sync_ready_state()
        # ready 表示で "停止中" になった後、起動失敗を伝えるためラベルを上書き
        self._status_label.configure(text="停止中(起動失敗)")

    # ============================================================
    # Coordinator スレッドからのコールバック
    # ============================================================
    def _on_utterance_from_thread(self, record: dict) -> None:
        self.after(0, lambda: self._apply_utterance(record))

    def _on_fatal_from_thread(
        self, message: str, *, exc=None, stage=None, seq_id=None, suppressed=0
    ) -> None:
        formatted = self._format_with_context(
            message, stage=stage, seq_id=seq_id, suppressed=suppressed
        )
        self.after(0, lambda: self._apply_fatal(formatted))

    def _on_warn_from_thread(
        self, message: str, *, exc=None, stage=None, seq_id=None, suppressed=0
    ) -> None:
        formatted = self._format_with_context(
            message, stage=stage, seq_id=seq_id, suppressed=suppressed
        )
        self.after(0, lambda: self._apply_warn(formatted))

    @staticmethod
    def _format_with_context(message: str, *, stage, seq_id, suppressed=0) -> str:
        """UI 表示用に "[stage] #seq message (+N件抑制)" 形式に整形。"""
        prefix_parts: list[str] = []
        if stage is not None:
            prefix_parts.append(f"[{stage}]")
        if seq_id is not None:
            prefix_parts.append(f"#{seq_id}")
        prefix = " ".join(prefix_parts)
        suffix = f" (+{suppressed}件抑制)" if suppressed > 0 else ""
        if not prefix:
            return message + suffix
        return prefix + " " + message + suffix

    def _on_status_from_thread(self, layer: LayerKind, status: ModelStatus) -> None:
        # SettingsPanel に転送(UI 表示) + 自分も再計算
        if self._settings_panel is not None:
            self._settings_panel.on_status_change(layer, status)
        # 自身もスレッドセーフに反映(メインスレッドへ転送)
        self.after(0, lambda: self._apply_layer_status(layer, status))

    def _apply_layer_status(self, layer: LayerKind, status: ModelStatus) -> None:
        self._layer_statuses[layer] = status
        self._sync_ready_state()

    def _sync_ready_state(self) -> None:
        """全レイヤのステータスを見て、開始ボタン/ステータスラベルを再構成する。

        idle 以外(running / starting / stopping)のときはここでは触らない
        (それぞれのフローで管理されるため)。
        """
        if self._state != "idle":
            return

        statuses = list(self._layer_statuses.values())
        if not statuses:
            return

        if any(s == ModelStatus.LOADING for s in statuses):
            self._toggle_btn.configure(text="モデル準備中…", state="disabled")
            self._status_label.configure(text="モデル準備中…")
        elif any(s == ModelStatus.NOT_DOWNLOADED for s in statuses):
            self._toggle_btn.configure(text="モデル未準備", state="disabled")
            self._status_label.configure(text="モデル未準備(設定/ネット接続を確認)")
        else:
            # 全部 LOADED
            self._toggle_btn.configure(text="▶ 開始", state="normal")
            self._status_label.configure(text="停止中")

    # ---- メインスレッドでの反映 ----
    def _apply_utterance(self, record: dict) -> None:
        timeline = record.get("timeline", {}) or {}
        t_cap = timeline.get("t_capture")
        t_play = timeline.get("t_playback")
        if t_cap is not None and t_play is not None:
            latency = t_play - t_cap
            self._latencies.append(latency)
            avg = sum(self._latencies) / len(self._latencies)
            self._latency_label.configure(
                text=f"平均レイテンシ: {avg:.2f} 秒(直近{len(self._latencies)}件)"
            )

        seq = record.get("seq_id", "?")
        src_lang = record.get("src_lang", "")
        tgt_lang = record.get("tgt_lang", "")
        src_text = record.get("src_text", "")
        tgt_text = record.get("tgt_text", "")
        text = f"#{seq} [{src_lang} → {tgt_lang}] {src_text}\n   → {tgt_text}"
        self._append_history(text)

    def _apply_fatal(self, message: str) -> None:
        self._append_history(f"[致命的エラー] {message}")
        self._state = "idle"
        # ready 表示を更新(基本は全 LOADED 維持なので "▶ 開始" 復活)
        self._sync_ready_state()
        # その上で「(エラー)」を明示してユーザに通知
        self._status_label.configure(text="停止中(エラー)")

    def _apply_warn(self, message: str) -> None:
        # 警告は UI には出さない(app.log に残るので、調査時はログを参照)。
        # UI には致命的エラーだけを表示し、ユーザが「動いている/止まった」を判別しやすくする。
        return

    # ============================================================
    def _on_clear_history(self) -> None:
        """履歴と平均レイテンシ表示をリセットする(状態には影響しない)。"""
        self._history_text.configure(state="normal")
        self._history_text.delete("1.0", "end")
        self._history_text.configure(state="disabled")
        self._latencies.clear()
        self._latency_label.configure(text="平均レイテンシ: -")

    # ============================================================
    def _append_history(self, text: str) -> None:
        self._history_text.configure(state="normal")
        self._history_text.insert("end", text + "\n\n")
        contents = self._history_text.get("1.0", "end").splitlines()
        if len(contents) > 50:
            self._history_text.delete("1.0", f"{len(contents) - 50}.0")
        self._history_text.see("end")
        self._history_text.configure(state="disabled")
