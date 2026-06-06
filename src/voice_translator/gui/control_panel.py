"""ControlPanel: 動作開始/停止と直近結果の表示UI(customtkinter)。

役割: Start/Stop トグル、モデル状態の集約観測、直近の翻訳テキスト表示、レイテンシ表示。

「🔊 出力テスト」ボタン: 選択中の TTS / Output backend / 出力デバイスで「テスト音声」
を 1 回鳴らす切り分け用ボタン。本体パイプラインが動作中 / TTS=「(なし)」 / 出力
デバイス未選択のときは無効化される。実処理は `AppController.test_output_playback`
に委譲。

Phase B 以降のロード方式変更:
- 開始ボタンは常時押下可(状態によらず)。
- 押下時に未ロードの backend があれば、AppController が Loader スレッドでまとめてロード → Coordinator 起動。
- MISSING_CREDENTIALS のレイヤがあるときだけボタンを disable(ロードしても意味がないため)。

`AppController.start_pipeline_async()` を使い、UIをブロックしない。

UI 改修(2026-05-30):
- ステータステキストボックスを CollapsibleSection で囲い、見出しクリックで折り畳み可能に。
  開閉状態は ConfigStore の `ui.collapsed.status_text` に永続化。
- 起動失敗時、status_label / history widget に加えて NotificationBanner にも出す
  (バナーが渡されている場合のみ)。3 段フィードバックで「無反応に見える」事故を防止。
"""

from __future__ import annotations

import threading
from collections import deque

import customtkinter as ctk

from voice_translator.common.app_controller import AppController
from voice_translator.common.types import CaptureKind, LayerKind, ModelStatus

from .collapsible_section import CollapsibleSection


# ConfigStore のキー: 折り畳み状態の永続化用
_CFG_COLLAPSED_STATUS = ("ui", "collapsed", "status_text")


class ControlPanel(ctk.CTkFrame):
    """動作操作と直近結果を表示するパネル。"""

    HISTORY_SIZE = 5

    def __init__(
        self, master, controller: AppController,
        settings_panel=None, banner=None,
    ) -> None:
        super().__init__(master)
        self._controller = controller
        self._settings_panel = settings_panel  # 共有: SettingsPanel.on_status_change を呼ぶため
        self._banner = banner                  # NotificationBanner(あれば起動失敗を流す)
        self._state: str = "idle"  # idle / loading / running / stopping
        self._latencies: deque[float] = deque(maxlen=10)
        # GUI 操作イベントの履歴(起動失敗 / 停止例外 / 致命的エラー 等)。
        # 「最近の翻訳」widget を翻訳結果に純化するため、エラー系は status textbox 側に表示する。
        # backend 由来エラーは `_collect_recent_errors` で別途集約されるので、
        # ここは GUI 操作起源のもの専用(2026-05-30)。
        self._gui_event_log: deque[str] = deque(maxlen=10)

        # 各レイヤの現在のステータス(初期値は AppController から取得)
        self._layer_statuses: dict[LayerKind, ModelStatus] = dict(
            self._controller.get_all_model_statuses()
        )

        self._build_widgets()
        # AppController からのコールバックを受け取る
        self._controller.set_callbacks(
            on_utterance_done=self._on_utterance_from_thread,
            on_text_ready=self._on_text_ready_from_thread,
            on_fatal=self._on_fatal_from_thread,
            on_warn=self._on_warn_from_thread,
            on_status_change=self._on_status_from_thread,
        )
        # 初期状態(モデル未ロード)を反映: ボタンを準備中にする
        self._sync_ready_state()

    # ============================================================
    def _build_widgets(self) -> None:
        # ヘッダ frame: 「動作」ラベル + 状態メッセージ(status_label)を横並びにする。
        # status_label をボタン列(下段 col=1)に置くとボタン幅次第で右端まで押し出され、
        # 「動作」のすぐ隣に並ばない。frame で囲って pack(side="left")で並べることで
        # ボタン列のサイズに影響されず「動作 [プロセスを選択してください…]」と
        # 隣接表示できる。
        header_frame = ctk.CTkFrame(self, fg_color="transparent")
        header_frame.grid(
            row=0, column=0, columnspan=3, sticky="w", padx=10, pady=(8, 4),
        )
        ctk.CTkLabel(header_frame, text="動作", font=("", 16, "bold")).pack(side="left")
        self._status_label = ctk.CTkLabel(header_frame, text="停止中")
        self._status_label.pack(side="left", padx=(12, 0))

        # 開始/停止ボタン と 中央ロードボタン を 1 つの frame にまとめて col 0 に配置
        btn_frame = ctk.CTkFrame(self, fg_color="transparent")
        btn_frame.grid(row=1, column=0, padx=10, pady=8, sticky="w")
        self._toggle_btn = ctk.CTkButton(
            btn_frame, text="▶ 開始", width=140, command=self._on_toggle
        )
        self._toggle_btn.pack(side="left")
        # 中央ロードボタン: 全レイヤを冪等に load(既ロードはスキップ)。設定変更後の
        # 反映は dialog 保存時に該当 backend が evict されるため、このボタン押下で
        # 再 load される。動作中 / ロード中は disable。
        self._load_btn = ctk.CTkButton(
            btn_frame, text="↻ ロード", width=100, command=self._on_load_clicked,
        )
        self._load_btn.pack(side="left", padx=(8, 0))
        # 「🔊 出力テスト」ボタン: 「翻訳まで出ているのに音が鳴らない」の切り分け用。
        # 動作中 / text_only / 出力デバイス未選択 のとき disable(_sync_ready_state で管理)。
        self._test_btn = ctk.CTkButton(
            btn_frame, text="🔊 出力テスト", width=120,
            command=self._on_test_output_clicked,
        )
        self._test_btn.pack(side="left", padx=(8, 0))

        self._latency_label = ctk.CTkLabel(self, text="平均レイテンシ: -")
        self._latency_label.grid(row=1, column=2, padx=10, pady=8, sticky="e")

        # アクセラレータ表示("GPU 使ってる/CPU のみ" の一目情報)
        self._accel_label = ctk.CTkLabel(self, text="演算: -", text_color="#94a3b8")
        self._accel_label.grid(
            row=2, column=0, columnspan=3, sticky="w", padx=10, pady=(0, 4)
        )

        # ステータステキストボックス(Phase C3): 全レイヤ状態 + 最近のエラー集約
        # CollapsibleSection で囲って、画面を広く使いたい時は畳めるようにする。
        # 開閉状態は ConfigStore の `ui.collapsed.status_text` に永続化。
        status_initially_open = not bool(
            self._controller.get_setting(*_CFG_COLLAPSED_STATUS, default=False)
        )
        self._status_section = CollapsibleSection(
            self, title="ステータス",
            initially_open=status_initially_open,
            on_toggle=self._on_status_toggle,
        )
        self._status_section.grid(
            row=3, column=0, columnspan=3, rowspan=2, sticky="nsew", padx=10, pady=(6, 4)
        )
        # body 内: ツールバー(クリアボタン) + textbox を縦並びに。
        # クリア対象は GUI 操作イベント履歴(`_gui_event_log`)のみ。
        # レイヤ状態と backend エラー集約は AppController が管理しているので、
        # ここで消しても次回 refresh で復活する(これは意図通り)。
        status_toolbar = ctk.CTkFrame(self._status_section.body, fg_color="transparent")
        status_toolbar.pack(fill="x", padx=2, pady=(0, 2))
        self._status_clear_btn = ctk.CTkButton(
            status_toolbar,
            text="操作イベントをクリア",
            width=160,
            command=self._on_clear_status_events,
        )
        self._status_clear_btn.pack(side="right")
        self._status_text = ctk.CTkTextbox(
            self._status_section.body, height=140, wrap="word"
        )
        self._status_text.pack(fill="both", expand=True)
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

    def _on_load_clicked(self) -> None:
        """中央ロードボタン: 全レイヤを冪等に load する。

        - 動作中 (`is_running`) / 既にロード中 (`is_loading`) は disable 経由で
          ここには来ない想定だが、二重押し対策で sync 確認も入れる。
        - `load_models_async` は既ロードのレイヤはスキップする冪等版。
        - 結果は各レイヤの `ModelStatus` 更新 → `_on_status_from_thread` 経由で
          UI に伝播するので、別途完了通知ロジックは不要。
        """
        if self._state != "idle":
            return
        if self._controller.is_running or self._controller.is_loading:
            return
        try:
            self._controller.load_models_async(
                on_done=self._on_load_done,
                on_failed=self._on_load_failed,
            )
        except Exception as e:  # noqa: BLE001
            import logging
            logging.getLogger("voice_translator").exception(
                "_on_load_clicked: load_models_async 起動失敗"
            )
            self._show_failure_banner(f"ロード起動失敗: {e}")
            return
        # 押下中は一時的に disable + 「ロード中…」表示。完了で _sync_ready_state が戻す。
        self._load_btn.configure(text="ロード中…", state="disabled")
        # ロード中に出力テストを叩くと TTS / Output の二重 load 競合が起きうるので disable
        try:
            self._test_btn.configure(state="disabled")
        except AttributeError:
            pass

    def _on_load_done(self) -> None:
        # Loader スレッドからの完了通知。tk へは after で marshalling。
        self.after(0, self._apply_load_done)

    def _on_load_failed(self, message: str) -> None:
        self.after(0, lambda: self._apply_load_failed(message))

    def _apply_load_done(self) -> None:
        self._sync_ready_state()  # ボタン text/state を最新状態に合わせ直す

    def _apply_load_failed(self, message: str) -> None:
        self._show_failure_banner(f"ロード失敗: {message}")
        self._append_status_event(f"[ロード失敗] {message}")
        self._sync_ready_state()

    # ============================================================
    # 出力テストボタン
    # ============================================================
    # 出力テストで読み上げるテキスト(切り分け用なので短く固定)。
    _TEST_PLAYBACK_TEXT = "テスト音声"

    def _on_test_output_clicked(self) -> None:
        """🔊 出力テストボタン: TTS → Output の経路を 1 回だけ叩いて音を鳴らす。

        - 動作中 / text_only / 出力デバイス未選択 のときは _sync_ready_state でボタンが
          disable のためここには来ない想定だが、二重防御で sync 確認も入れる。
        - 押下中はボタンを「再生中…」+ disable にして二重押し防止 + UI フィードバック。
        - 実処理(TTS 合成 + Output 再生)はブロッキングなので別スレッドで動かす。
          完了 / 失敗は `after(0, ...)` でメインスレッドへ戻して UI に反映する。
        """
        if self._state != "idle":
            return
        if self._controller.is_running or self._controller.is_loading:
            return

        self._test_btn.configure(text="再生中…", state="disabled")

        def _worker() -> None:
            try:
                self._controller.test_output_playback(self._TEST_PLAYBACK_TEXT)
            except Exception as e:  # noqa: BLE001
                msg = str(e)
                self.after(0, lambda m=msg: self._on_test_playback_failed(m))
                return
            self.after(0, self._on_test_playback_done)

        threading.Thread(
            target=_worker, name="vt_test_output", daemon=True,
        ).start()

    def _on_test_playback_done(self) -> None:
        """テスト再生の正常完了を UI に反映する。"""
        # ステータスにも軽く出しておく(目に見える結果が「音」だけだとボタンを連打されやすいので)
        self._append_status_event(f"[出力テスト] 再生完了: {self._TEST_PLAYBACK_TEXT!r}")
        self._sync_ready_state()

    def _on_test_playback_failed(self, message: str) -> None:
        """テスト再生失敗(例外)を UI に反映する。"""
        import logging
        logging.getLogger("voice_translator").warning(
            "test_output_playback 失敗: %s", message,
        )
        self._show_failure_banner(f"出力テスト失敗: {message}")
        self._append_status_event(f"[出力テスト失敗] {message}")
        self._sync_ready_state()

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
            # フィードバックを 4 箇所に出して見落としを防ぐ:
            # 1) app.log にスタックトレース付きで残す(原因調査用)
            # 2) NotificationBanner(画面上部、最も目立つ。banner があれば)
            # 3) status_label に短く表示(ボタン横、視線が行く場所)
            # 4) history widget(従来通り、後で見返すため)
            import logging
            logging.getLogger("voice_translator").exception(
                "_do_start_async で start_pipeline_async が同期失敗"
            )
            self._show_failure_banner(f"起動失敗: {e}")
            # status_label は ready_state の周期更新で上書きされるので、
            # 短時間でも見えるようここで上書きしておく。
            try:
                self._status_label.configure(text=f"起動失敗: {e}")
            except Exception:  # noqa: BLE001 - widget 破棄済み等
                pass
            # 履歴は status textbox 側に積む(従来は _append_history で翻訳履歴に混ぜていた)
            self._append_status_event(f"[起動失敗] {e}")
            return
        self._state = "starting"
        self._toggle_btn.configure(text="開始中…", state="disabled")
        try:
            self._load_btn.configure(text="(起動中)", state="disabled")
        except AttributeError:
            pass
        try:
            self._test_btn.configure(text="🔊 出力テスト", state="disabled")
        except AttributeError:
            pass
        self._status_label.configure(text="開始中…")

    def _do_stop(self) -> None:
        self._state = "stopping"
        self._toggle_btn.configure(text="停止中…", state="disabled")
        try:
            self._load_btn.configure(text="(停止中)", state="disabled")
        except AttributeError:
            pass
        try:
            self._test_btn.configure(text="🔊 出力テスト", state="disabled")
        except AttributeError:
            pass
        self._status_label.configure(text="停止中…")
        try:
            self._controller.stop_pipeline()
        except Exception as e:  # noqa: BLE001
            self._append_status_event(f"[停止時例外] {e}")
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
        # 動作中はモデル差し替え禁止 → 中央ロードボタンも disable
        try:
            self._load_btn.configure(text="(動作中)", state="disabled")
        except AttributeError:
            pass
        # 動作中は Output backend を本体が掴んでいるため出力テストは衝突する → disable
        try:
            self._test_btn.configure(text="🔊 (動作中)", state="disabled")
        except AttributeError:
            pass

    def _apply_loader_failed(self, message: str) -> None:
        # 非同期ロード失敗も同期失敗と同じ 4 段フィードバックで通知
        self._show_failure_banner(f"起動失敗: {message}")
        self._append_status_event(f"[起動失敗] {message}")
        self._state = "idle"
        # 起動失敗時は現在のレイヤ状態を見て ready 表示を更新
        self._sync_ready_state()
        # ready 表示で "停止中" になった後、起動失敗を伝えるためラベルを上書き
        self._status_label.configure(text="停止中(起動失敗)")

    # ============================================================
    # 折り畳み + バナー連携
    # ============================================================
    def _on_status_toggle(self, is_open: bool) -> None:
        """ステータスセクションの開閉状態を ConfigStore に永続化。"""
        try:
            self._controller.set_setting(*_CFG_COLLAPSED_STATUS, not is_open)
        except Exception:  # noqa: BLE001
            pass

    def _show_failure_banner(self, message: str) -> None:
        """起動失敗を NotificationBanner に出す(banner があれば)。"""
        if self._banner is None:
            return
        try:
            self._banner.show_error(message)
        except Exception:  # noqa: BLE001
            pass

    # ============================================================
    # Coordinator スレッドからのコールバック
    # ============================================================
    def _on_utterance_from_thread(self, record: dict) -> None:
        self.after(0, lambda: self._apply_utterance(record))

    def _on_text_ready_from_thread(self, record: dict) -> None:
        """TTS 完了時(= 音声合成完了の時点)に呼ばれる前倒し通知。

        履歴表示はこちらで行い、レイテンシ計算は `_apply_utterance`(Output 完了後)
        で行う 2 段構成。発話が長い再生でも、テキストは音より先に出せる。
        """
        self.after(0, lambda: self._apply_text_ready(record))

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
        """`AppController.get_status_summary()` + GUI 操作イベント履歴を表示する。

        構成:
          1. レイヤ別 backend 状態
          2. 最近の backend エラー(controller 側)
          3. 操作イベント(本パネル側、起動失敗 / 致命的エラー 等。新しい順)
        """
        try:
            summary = self._controller.get_status_summary()
        except Exception as e:  # noqa: BLE001
            summary = f"(ステータス取得に失敗: {e})"

        # GUI 操作イベントを末尾に付加(あれば)
        if self._gui_event_log:
            lines = [summary, "", "操作イベント:"]
            # 新しいものから 5 件まで(直近を見たい想定)
            for ev in list(self._gui_event_log)[-5:][::-1]:
                lines.append(f"  {ev}")
            summary = "\n".join(lines)

        try:
            self._status_text.configure(state="normal")
            self._status_text.delete("1.0", "end")
            self._status_text.insert("end", summary)
            self._status_text.configure(state="disabled")
        except Exception:  # noqa: BLE001
            # widget が破棄済み等の場合は無視
            pass

    def _append_status_event(self, message: str) -> None:
        """操作起源のイベント(起動失敗 / 停止例外 / 致命的エラー 等)を status textbox に積む。

        履歴は 10 件まで(`_gui_event_log` の maxlen)、表示は新しい順 5 件。
        積んだら即時 `_refresh_status_text` で反映する(3 秒の周期更新を待たない)。
        ステータスセクションが畳まれていれば見えないが、開けば最新が出る。
        """
        from time import strftime

        stamped = f"[{strftime('%H:%M:%S')}] {message}"
        self._gui_event_log.append(stamped)
        self._refresh_status_text()

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

        text_only モード(P3): TTS / Output レイヤは判定対象から除外する
        (起動対象外なので、未ロードで残っていても Start を阻害しない)。

        idle 以外(running / starting / stopping)のときは触らない(各フローで管理)。
        """
        if self._state != "idle":
            return

        statuses = list(self._active_layer_statuses().values())
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
        elif self._capture_source_required_but_empty():
            # 段階 3 / A-7: PROCESS kind の capture backend を選択中で `devices.input`
            # が未設定(=PID 未選択)のときは Start させない。プロセス選択ダイアログから
            # PID を選んでもらう必要がある。
            self._toggle_btn.configure(text="プロセス未選択", state="disabled")
            self._status_label.configure(
                text="プロセスを選択してください(設定 → プロセス選択…)"
            )
        else:
            # 開始ボタンは常時押下可。ロード状況は補助情報としてラベルに出す。
            self._toggle_btn.configure(text="▶ 開始", state="normal")
            if any(s in (ModelStatus.INIT, ModelStatus.NOT_DOWNLOADED) for s in statuses):
                self._status_label.configure(text="停止中(押下時にロードします)")
            elif any(s == ModelStatus.LOADING for s in statuses):
                self._status_label.configure(text="停止中(ロード中)")
            else:
                self._status_label.configure(text="停止中")

        # 中央ロードボタンの state を再計算(text_only 時は対象レイヤから絞った statuses を使う)
        self._sync_load_button_state(statuses)
        # 出力テストボタンの state も再計算(動作中 / text_only / output 未選択 で disable)
        self._sync_test_button_state()
        # アクセラレータ表示は ready_state とは独立に常に更新する
        self._refresh_accel_label()

    def _sync_load_button_state(self, statuses: list[ModelStatus]) -> None:
        """中央「↻ ロード」ボタンの enable/disable と text を再設定する。

        - 全レイヤが LOADED → ボタン文言「ロード済み」+ disable
        - LOADING 中のレイヤあり → ボタン文言「ロード中…」+ disable
        - MISSING_CREDENTIALS あり → 「↻ ロード」だが押せる(押すと部分 load 試行 →
          MISSING のレイヤだけ skip、それ以外はロード)。disable はしない。
        - それ以外(INIT / NOT_DOWNLOADED が混在) → 「↻ ロード」+ normal
        """
        try:
            btn = self._load_btn
        except AttributeError:
            return  # 初期化前に呼ばれた場合(理論上ありえない)
        if not statuses:
            btn.configure(text="↻ ロード", state="normal")
            return
        if all(s == ModelStatus.LOADED for s in statuses):
            btn.configure(text="ロード済み", state="disabled")
        elif any(s == ModelStatus.LOADING for s in statuses):
            btn.configure(text="ロード中…", state="disabled")
        else:
            btn.configure(text="↻ ロード", state="normal")

    def _sync_test_button_state(self) -> None:
        """🔊 出力テストボタンの enable/disable と text を再設定する。

        無効化条件(いずれか):
        - text_only モード(TTS=「(なし)」、合成手段がない)
        - 出力デバイス未選択(devices.output が空)
        - すでに何かのトランジション中(running / starting / stopping)
        """
        try:
            btn = self._test_btn
        except AttributeError:
            return  # 初期化前に呼ばれた場合
        try:
            mode = self._controller.output_mode
        except Exception:  # noqa: BLE001
            mode = "audio"
        if mode == "text_only":
            btn.configure(text="🔊 (TTS なし)", state="disabled")
            return
        try:
            output_id = str(
                self._controller.get_setting("devices", "output", default="") or ""
            ).strip()
        except Exception:  # noqa: BLE001
            output_id = ""
        if not output_id:
            btn.configure(text="🔊 出力未選択", state="disabled")
            return
        btn.configure(text="🔊 出力テスト", state="normal")

    def _capture_source_required_but_empty(self) -> bool:
        """capture_kind == PROCESS かつ `devices.input` が未設定なら True。

        段階 3 / A-7 で導入: PROCESS kind backend(ProcTap 等)は PID を毎回選び直す
        前提のため、未選択のままで Start を許すと FatalError になる。先回りで disable。
        """
        try:
            backend_name = str(
                self._controller.get_setting("backends", LayerKind.CAPTURE.value, default="")
            )
        except Exception:  # noqa: BLE001
            return False
        if not backend_name:
            return False
        try:
            kind = self._controller.get_capture_kind(backend_name)
        except Exception:  # noqa: BLE001
            return False
        if not isinstance(kind, CaptureKind) or kind != CaptureKind.PROCESS:
            return False
        try:
            source = self._controller.get_setting("devices", "input", default="")
        except Exception:  # noqa: BLE001
            return True  # 設定取得失敗 → 安全側で未選択扱い
        return not bool(str(source).strip())

    def _active_layer_statuses(self) -> dict[LayerKind, ModelStatus]:
        """text_only モードでは TTS / Output を除いたレイヤ状態を返す。"""
        try:
            mode = self._controller.output_mode
        except Exception:  # noqa: BLE001 - 古いモック / 仕様逸脱
            mode = "audio"
        if mode == "text_only":
            return {
                layer: status
                for layer, status in self._layer_statuses.items()
                if layer not in (LayerKind.TTS, LayerKind.OUTPUT)
            }
        return dict(self._layer_statuses)

    def _refresh_accel_label(self) -> None:
        """各レイヤの device を集約して「演算: GPU(cuda) / CPU のみ / 不明」を表示する。

        device 概念を持つレイヤ(ASR / Translator 等)の値を見て、1つでも CUDA/MPS が
        あれば GPU 利用扱い、すべて CPU なら "CPU のみ"、まだロードされていなければ
        "不明" を表示する。
        text_only モードでは TTS / Output レイヤの device 報告は無視する。
        """
        if self._accel_label is None:
            return
        try:
            mode = self._controller.output_mode
        except Exception:  # noqa: BLE001
            mode = "audio"
        gpu_devices: set[str] = set()
        has_cpu = False
        for layer in LayerKind:
            if mode == "text_only" and layer in (LayerKind.TTS, LayerKind.OUTPUT):
                continue
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
        """Output 完了時に呼ばれる。レイテンシ計算のみを行う(履歴は前倒し済み)。

        計測区間: 「発話の終端確定 → 再生指示の発行」
        体感の「喋り終わってから音が返ってくるまで」に対応(発話そのものの長さは含めない)。
        トータル時間(録音開始 → 再生戻り)や段別の内訳は processtime.csv で見られる。
        """
        timeline = record.get("timeline", {}) or {}
        t_start = timeline.get("t_vad_end")
        t_end = timeline.get("t_playback_start")
        if t_start is not None and t_end is not None:
            latency = t_end - t_start
            self._latencies.append(latency)
            avg = sum(self._latencies) / len(self._latencies)
            self._latency_label.configure(
                text=f"平均レイテンシ: {avg:.2f} 秒(直近{len(self._latencies)}件)"
            )

    def _apply_text_ready(self, record: dict) -> None:
        """TTS 完了時に呼ばれる前倒し通知。履歴ボックスに翻訳結果を表示する。

        ledger スナップショットを使うため、`t_playback_start` 以降は含まれない。
        レイテンシ表示は `_apply_utterance`(Output 完了後)で別途更新される。
        """
        seq = record.get("seq_id", "?")
        src_lang = record.get("src_lang", "")
        tgt_lang = record.get("tgt_lang", "")
        src_text = record.get("src_text", "")
        tgt_text = record.get("tgt_text", "")
        text = f"#{seq} [{src_lang} → {tgt_lang}] {src_text}\n   → {tgt_text}"
        self._append_history(text)

    def _apply_fatal(self, message: str) -> None:
        self._append_status_event(f"[致命的エラー] {message}")
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
    def _on_clear_status_events(self) -> None:
        """ステータスの「操作イベント」履歴をクリアする。

        対象は `_gui_event_log`(起動失敗 / 停止例外 / 致命的エラー 等の GUI 操作起源)。
        レイヤ別 backend 状態と最近の backend エラーは AppController 側が持っているので
        ここではクリアしない(次の refresh で再表示される、これは意図通り)。
        """
        self._gui_event_log.clear()
        self._refresh_status_text()

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
