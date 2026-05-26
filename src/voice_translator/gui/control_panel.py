"""ControlPanel: 動作開始/停止と直近結果の表示UI(customtkinter)。

役割: Start/Stop トグル、直近の翻訳テキスト表示、レイテンシ表示。
パイプライン結果やエラーは AppController のコールバック経由で受け取り、
UIスレッドに反映する(after() で main loop に戻す)。
"""

from __future__ import annotations

from collections import deque

import customtkinter as ctk

from voice_translator.common.app_controller import AppController
from voice_translator.common.utterance import Utterance


class ControlPanel(ctk.CTkFrame):
    """動作操作と直近結果を表示するパネル。

    役割: 開始/停止ボタン、翻訳テキストの履歴表示(最新N件)、レイテンシ平均表示。
    """

    HISTORY_SIZE = 5

    def __init__(self, master, controller: AppController) -> None:
        super().__init__(master)
        self._controller = controller
        self._is_running = False
        self._latencies: deque[float] = deque(maxlen=10)

        self._build_widgets()
        # AppController からのコールバックを受け取る(別スレッド呼び出しになる前提)
        self._controller.set_callbacks(
            on_utterance_done=self._on_utterance_from_thread,
            on_fatal=self._on_fatal_from_thread,
            on_warn=self._on_warn_from_thread,
        )

    # ============================================================
    def _build_widgets(self) -> None:
        ctk.CTkLabel(self, text="動作", font=("", 16, "bold")).grid(
            row=0, column=0, columnspan=3, sticky="w", padx=10, pady=(8, 4)
        )

        self._toggle_btn = ctk.CTkButton(
            self, text="▶ 開始", width=120, command=self._on_toggle
        )
        self._toggle_btn.grid(row=1, column=0, padx=10, pady=8, sticky="w")

        self._status_label = ctk.CTkLabel(self, text="停止中")
        self._status_label.grid(row=1, column=1, padx=10, pady=8, sticky="w")

        self._latency_label = ctk.CTkLabel(self, text="平均レイテンシ: -")
        self._latency_label.grid(row=1, column=2, padx=10, pady=8, sticky="e")

        ctk.CTkLabel(self, text="最近の翻訳:").grid(
            row=2, column=0, columnspan=3, sticky="w", padx=10, pady=(8, 0)
        )
        self._history_text = ctk.CTkTextbox(self, height=180, wrap="word")
        self._history_text.grid(row=3, column=0, columnspan=3, sticky="ew", padx=10, pady=4)
        self._history_text.configure(state="disabled")

        self.columnconfigure(1, weight=1)

    # ============================================================
    def _on_toggle(self) -> None:
        if self._is_running:
            self._do_stop()
        else:
            self._do_start()

    def _do_start(self) -> None:
        try:
            self._controller.start_pipeline()
        except Exception as e:  # noqa: BLE001 - 起動失敗は致命扱い
            self._append_history(f"[起動失敗] {e}")
            return
        self._is_running = True
        self._toggle_btn.configure(text="■ 停止")
        self._status_label.configure(text="動作中")

    def _do_stop(self) -> None:
        try:
            self._controller.stop_pipeline()
        except Exception as e:  # noqa: BLE001
            self._append_history(f"[停止時例外] {e}")
        self._is_running = False
        self._toggle_btn.configure(text="▶ 開始")
        self._status_label.configure(text="停止中")

    # ============================================================
    # Coordinator スレッドからのコールバック
    # ============================================================
    def _on_utterance_from_thread(self, utt: Utterance) -> None:
        # tkinter はメインスレッド以外から触れないので after() で戻す
        self.after(0, lambda: self._apply_utterance(utt))

    def _on_fatal_from_thread(self, message: str) -> None:
        self.after(0, lambda: self._apply_fatal(message))

    def _on_warn_from_thread(self, message: str) -> None:
        self.after(0, lambda: self._apply_warn(message))

    # ---- メインスレッドでの反映 ----
    def _apply_utterance(self, utt: Utterance) -> None:
        latency = utt.timeline.elapsed("t_capture", "t_playback")
        if latency is not None:
            self._latencies.append(latency)
            avg = sum(self._latencies) / len(self._latencies)
            self._latency_label.configure(text=f"平均レイテンシ: {avg:.2f} 秒(直近{len(self._latencies)}件)")

        text = f"[{utt.src_lang} → {utt.tgt_lang}] {utt.src_text}\n   → {utt.tgt_text}"
        self._append_history(text)

    def _apply_fatal(self, message: str) -> None:
        self._append_history(f"[致命的エラー] {message}")
        # パイプライン自体は Coordinator 側で停止しているので UI 状態だけ戻す
        self._is_running = False
        self._toggle_btn.configure(text="▶ 開始")
        self._status_label.configure(text="停止中(エラー)")

    def _apply_warn(self, message: str) -> None:
        self._append_history(f"[警告] {message}")

    # ============================================================
    def _append_history(self, text: str) -> None:
        self._history_text.configure(state="normal")
        self._history_text.insert("end", text + "\n\n")
        # 履歴が長くなりすぎないよう、先頭からトリム(雑だが MVP では十分)
        contents = self._history_text.get("1.0", "end").splitlines()
        if len(contents) > 50:
            self._history_text.delete("1.0", f"{len(contents) - 50}.0")
        self._history_text.see("end")
        self._history_text.configure(state="disabled")
