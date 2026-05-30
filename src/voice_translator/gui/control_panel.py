"""ControlPanel: 動作開始/停止と直近結果の表示UI(customtkinter)。

役割: Start/Stop トグル、モデル状態の集約観測、直近の翻訳テキスト表示、レイテンシ表示。

Phase B 以降のロード方式変更:
- 開始ボタンは常時押下可(状態によらず)。
- 押下時に未ロードの backend があれば、AppController が Loader スレッドでまとめてロード → Coordinator 起動。
- MISSING_CREDENTIALS のレイヤがあるときだけボタンを disable(ロードしても意味がないため)。

`AppController.start_pipeline_async()` を使い、UIをブロックしない。
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

        # アクセラレータ表示("GPU 使ってる/CPU のみ" の一目情報)
        self._accel_label = ctk.CTkLabel(self, text="演算: -", text_color="#94a3b8")
        self._accel_label.grid(
            row=2, column=0, columnspan=3, sticky="w", padx=10, pady=(0, 4)
        )

        # ステータステキストボックス(Phase C3): 全レイヤ状態 + 最近のエラー集約
        ctk.CTkLabel(self, text="ステータス:").grid(
            row=3, column=0, sticky="w", padx=10, pady=(6, 0)
        )
        self._status_text = ctk.CTkTextbox(self, height=140, wrap="word")
        self._status_text.grid(
            row=4, column=0, columnspan=3, sticky="ew", padx=10, pady=4
        )
        self._status_text.configure(state="disabled")

        # 履歴ラベル + クリアボタン(同じ行に配置)
        ctk.CTkLabel(self, text="最近の翻訳:").grid(
            row=5, column=0, sticky="w", padx=10, pady=(8, 0)
        )
        self._clear_btn = ctk.CTkButton(
            self, text="クリア", width=80, command=self._on_clear_history
        )
        self._clear_btn.grid(row=5, column=2, sticky="e", padx=10, pady=(8, 0))

        self._history_text = ctk.CTkTextbox(self, height=260, wrap="word")
        self._history_text.grid(row=6, column=0, columnspan=3, sticky="nsew", padx=10, pady=4)
        self._history_text.configure(state="disabled")

        self.columnconfigure(1, weight=1)
        # 履歴ボックスをウィンドウ拡大時に伸ばす
        self.rowconfigure(6, weight=1)

        # 初期状態を反映 + 定期更新を仕掛ける
        self._refresh_status_text()
        self._schedule_status_refresh()

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
            # フィードバックを 3 箇所に出して見落としを防ぐ:
            # 1) app.log にスタックトレース付きで残す(原因調査用)
            # 2) history widget(従来通り)
            # 3) status_label に短く表示(ユーザの視線が一番行く場所)。
            #   「ボタン押したが反応無い」状態を作らないため。
            import logging
            logging.getLogger("voice_translator").exception(
                "_do_start_async で start_pipeline_async が同期失敗"
            )
            self._append_history(f"[起動失敗] {e}")
            # status_label は ready_state の周期更新で上書きされるので、
            # 短時間でも見えるようここで上書きしておく。
            try:
                self._status_label.configure(text=f"起動失敗: {e}")
            except Exception:  # noqa: BLE001 - widget 破棄済み等
                pass
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
        # ステータステキストボックスにも反映(Phase C3)
        self._refresh_status_text()

    # ============================================================
    # ステータステキストボックス(Phase C3)
    # ============================================================
    _STATUS_REFRESH_INTERVAL_MS = 3000  # 3 秒ごとにエラー履歴等を再フェッチ

    def _refresh_status_text(self) -> None:
        """`AppController.get_status_summary()` を取得してテキストボックスを更新する。"""
        try:
            summary = self._controller.get_status_summary()
        except Exception as e:  # noqa: BLE001
            summary = f"(ステータス取得に失敗: {e})"
        try:
            self._status_text.configure(state="normal")
            self._status_text.delete("1.0", "end")
            self._status_text.insert("end", summary)
            self._status_text.configure(state="disabled")
        except Exception:  # noqa: BLE001
            # widget が破棄済み等の場合は無視
            pass

    def _schedule_status_refresh(self) -> None:
        """周期的にステータスを再描画する。`on_status_change` の通知漏れに対する保険。"""
        try:
            self.after(self._STATUS_REFRESH_INTERVAL_MS, self._tick_status_refresh)
        except Exception:  # noqa: BLE001
            pass

    def _tick_status_refresh(self) -> None:
        self._refresh_status_text()
        self._schedule_status_refresh()

    def _sync_ready_state(self) -> None:
        """各レイヤのステータスを見て、開始ボタン/ステータスラベルを再構成する。

        Phase B: 「全レイヤ LOADED でないと開始ボタン無効」を撤回。
        開始ボタンは常時押下可で、押された時点で未ロードならまとめてロードする。
        MISSING_CREDENTIALS / DOWNLOADING のときだけ無効化する:
          - MISSING_CREDENTIALS: ロードしても意味なし(API key 未設定)
          - DOWNLOADING: 進行中のロードを待つ(押下しても何も起きない)

        idle 以外(running / starting / stopping)のときは触らない(各フローで管理)。
        """
        if self._state != "idle":
            return

        statuses = list(self._layer_statuses.values())
        if not statuses:
            return

        if any(s == ModelStatus.MISSING_CREDENTIALS for s in statuses):
            self._toggle_btn.configure(text="認証情報未設定", state="disabled")
            self._status_label.configure(
                text="認証情報未設定(詳細ダイアログで設定してください)"
            )
        elif any(s == ModelStatus.DOWNLOADING for s in statuses):
            self._toggle_btn.configure(text="モデル DL 中…", state="disabled")
            self._status_label.configure(text="モデルダウンロード中…")
        else:
            # 開始ボタンは常時押下可。ロード状況は補助情報としてラベルに出す。
            self._toggle_btn.configure(text="▶ 開始", state="normal")
            if any(s in (ModelStatus.INIT, ModelStatus.NOT_DOWNLOADED) for s in statuses):
                self._status_label.configure(text="停止中(押下時にロードします)")
            elif any(s == ModelStatus.LOADING for s in statuses):
                self._status_label.configure(text="停止中(ロード中)")
            else:
                self._status_label.configure(text="停止中")

        # アクセラレータ表示は ready_state とは独立に常に更新する
        self._refresh_accel_label()

    def _refresh_accel_label(self) -> None:
        """各レイヤの device を集約して「演算: GPU(cuda) / CPU のみ / 不明」を表示する。

        device 概念を持つレイヤ(ASR / Translator 等)の値を見て、1つでも CUDA/MPS が
        あれば GPU 利用扱い、すべて CPU なら "CPU のみ"、まだロードされていなければ
        "不明" を表示する。
        """
        if self._accel_label is None:
            return
        gpu_devices: set[str] = set()
        has_cpu = False
        for layer in LayerKind:
            device = self._controller.get_layer_device(layer)
            if not device:
                continue
            d = device.lower()
            if d in ("cuda", "mps"):
                gpu_devices.add(d)
            elif d == "cpu":
                has_cpu = True

        if gpu_devices:
            color = "#16a34a"  # green
            text = f"演算: GPU ({', '.join(sorted(gpu_devices))})"
        elif has_cpu:
            color = "#d97706"  # amber(動作はするがプロファイル的に最速ではない)
            text = "演算: CPU のみ"
        else:
            color = "#94a3b8"  # slate
            text = "演算: -(モデル準備中)"
        self._accel_label.configure(text=text, text_color=color)

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
