"""SettingsPanel: 設定操作UI(customtkinter)。

役割: バックエンド/デバイス/言語ペア/ログ出力先 のプルダウン+入力欄と、
設定の保存/読込ボタンを提供する。レイヤ別のモデルステータス(英語表示)も併記する。

UI 構成(2026-06-05 改 / P1):
- 内部を 3 つの `CollapsibleSection` に分割:
  - 「バックエンド」: 6 レイヤの選択 + ステータス + 設定ボタン
  - 「デバイス」: 入力デバイス / 出力デバイス
  - 「翻訳」: 入力言語 (src) / 出力言語 (tgt)
- 各セクションの開閉状態は ConfigStore の
  `ui.collapsed.{backends, devices, languages}` に独立して永続化する。
- ログ出力先 + 「設定を保存 / 再読込 / デバイス再列挙」ボタンは 3 セクションの
  どれにも属さないため、セクション外の共通行として下部に置く。

入力言語(src)プルダウンは ASR backend ごとの対応言語に動的に追従する:
- backend 切替時に `_refresh_input_language_choices` で選択肢を再構築
- 既存設定値が新 backend で非対応のときは自動 fallback + 通知バナー
- 表示は `"en (English)"` 形式、内部値は `"en"`(共通言語テーブルで変換)
"""

from __future__ import annotations

import customtkinter as ctk

from voice_translator.common.app_controller import AppController
from voice_translator.common.languages import format_language, parse_language
from voice_translator.common.types import (
    AuthState,
    CaptureKind,
    LayerKind,
    ModelStatus,
)

from .collapsible_section import CollapsibleSection
from .consent_dialog import ConsentDialog
from .layer_settings_dialog import LayerSettingsDialog
from .logic.backend_display import (
    SKIPPED_STATUS_TEXT,
    TTS_NONE_DISPLAY,
    TTS_NONE_INTERNAL,
    backend_display_to_internal,
    backend_internal_to_display,
    capture_internal_to_display,
)
from .logic.language_choices import (
    compute_src_selection,
    compute_tgt_selection,
    format_src_fallback_message,
    format_tgt_fallback_message,
    format_tts_warning_message,
    restrict_to_tts,
    tts_warning_needed,
)
from .logic.auth_display import auth_status_override
from .logic.palette import DISABLED_TEXT, STATUS_COLOR_DEFAULT, STATUS_COLORS
from .logic.restart_messages import format_restart_failed, format_restart_started
from .process_select_dialog import ProcessSelectDialog

# GUIで切替対象とするレイヤと表示ラベル
_LAYER_LABELS: list[tuple[LayerKind, str]] = [
    (LayerKind.CAPTURE, "音声取得"),
    (LayerKind.VAD, "VAD"),
    (LayerKind.ASR, "ASR(書き起こし)"),
    (LayerKind.TRANSLATOR, "翻訳"),
    (LayerKind.TTS, "TTS(音声合成)"),
    (LayerKind.OUTPUT, "音声出力"),
]

# 翻訳先(tgt)言語の候補: 当面は固定リスト(Translator backend ごとの連動は別ブランチ)。
# auto は出力言語としては意味が無いので含めない。
_TGT_LANG_CHOICES: list[str] = [
    "en", "ja", "zh", "ko", "es", "fr", "de", "it", "pt", "ru", "ar",
    "hi", "th", "vi", "id", "tr",
]

# ASR backend が未登録 / 対応言語不明 のときの fallback 候補(最低限の MVP セット)。
_FALLBACK_INPUT_LANGS: list[str] = list(_TGT_LANG_CHOICES)

# 各セクションの開閉永続化キー(default=False は「開」を表す。set_setting には
# is_open の論理否定を保存することで「閉じた状態だけ True を立てる」運用)。
_CFG_COLLAPSED_BACKENDS = ("ui", "collapsed", "backends")
_CFG_COLLAPSED_DEVICES = ("ui", "collapsed", "devices")
_CFG_COLLAPSED_LANGUAGES = ("ui", "collapsed", "languages")

class SettingsPanel(ctk.CTkFrame):
    """設定操作のパネル + レイヤ別モデルステータス表示。"""

    def __init__(self, master, controller: AppController, *, banner=None) -> None:
        super().__init__(master)
        self._controller = controller
        # 通知バナー(入力言語の自動 fallback などをユーザに伝える)。
        # None でも動作する(その場合は print に落とす)。
        self._banner = banner

        self._backend_vars: dict[LayerKind, ctk.StringVar] = {}
        self._status_labels: dict[LayerKind, ctk.CTkLabel] = {}
        # ステータス欄を編成表示(「〜側で実行」/「(なし)」)で上書き中のレイヤ。
        # ここに入っている間は実ステータスでの再描画をブロックする。
        self._status_overridden: set[LayerKind] = set()
        self._capture_var = ctk.StringVar(value="(未選択)")
        self._output_var = ctk.StringVar(value="(未選択)")
        # 言語プルダウンは表示形式 "en (English)" を保持。内部値(コード)と区別する。
        initial_src = str(controller.get_setting("languages", "src", default="auto"))
        initial_tgt = str(controller.get_setting("languages", "tgt", default="ja"))
        self._src_var = ctk.StringVar(value=format_language(initial_src))
        self._tgt_var = ctk.StringVar(value=format_language(initial_tgt))
        self._src_dropdown: ctk.CTkOptionMenu | None = None  # 後で再構築するので保持
        self._tgt_dropdown: ctk.CTkOptionMenu | None = None  # Translator 連動用
        self._log_dir_var = ctk.StringVar(value=str(controller.get_setting("log", "directory", default="./logs")))

        # 3 セクションの参照(テストや外部からの取得用に property も提供)
        self._backends_section: CollapsibleSection | None = None
        self._devices_section: CollapsibleSection | None = None
        self._languages_section: CollapsibleSection | None = None

        self._build_widgets()
        self._populate_devices_into_dropdowns()
        self._sync_all_status_labels()
        self._apply_absorbed_visuals()
        # 起動時に入力言語プルダウンを ASR backend の対応言語に合わせて構築
        current_asr = str(controller.get_setting("backends", LayerKind.ASR.value, default=""))
        if current_asr:
            self._refresh_input_language_choices(current_asr, notify_fallback=False)
        # 出力言語プルダウンは「翻訳ロールを実際に担う backend」(複合に吸収されて
        # いれば複合側)に合わせて構築
        if self._tgt_provider_name():
            self._refresh_target_language_choices(notify_fallback=False)
        # 起動時に TTS 互換も一度チェック(警告は出さない)
        self._check_tts_output_lang_compatibility(notify_fallback=False)

        # P2: 通知の購読(従来は ControlPanel 経由で状態転送されていた)。
        # listener は emit 元スレッドで呼ばれるため、各ハンドラ側で after(0) marshalling する。
        self._subscriptions = [
            controller.add_status_listener(self.on_status_change),
            controller.add_restart_listener(self._on_restart_event),
            controller.add_settings_listener(self._on_settings_event),
            controller.add_running_listener(self._on_running_changed),
        ]

    # ============================================================
    def _build_widgets(self) -> None:
        # 3 セクションを縦に並べる(pack で並べ、各 section の body 内は grid)。
        # ヘッダ「設定」は CollapsibleSection 自身が出すので不要。
        self._backends_section = self._build_backends_section()
        self._backends_section.pack(fill="x", padx=10, pady=(8, 2))

        self._devices_section = self._build_devices_section()
        self._devices_section.pack(fill="x", padx=10, pady=2)

        self._languages_section = self._build_languages_section()
        self._languages_section.pack(fill="x", padx=10, pady=2)

        # 共通行(セクション外): ログ出力先 + 保存/再読込/デバイス再列挙ボタン
        self._build_common_rows()

    # ----------------------------------------------------------
    # セクション 1: バックエンド
    # ----------------------------------------------------------
    def _build_backends_section(self) -> CollapsibleSection:
        initially_open = self._initial_open_state(_CFG_COLLAPSED_BACKENDS)
        section = CollapsibleSection(
            self,
            title="バックエンド",
            initially_open=initially_open,
            on_toggle=lambda is_open: self._persist_collapsed(
                _CFG_COLLAPSED_BACKENDS, is_open
            ),
        )

        body = section.body
        # 6 レイヤ行を保持(TTS=(なし) 時にグレーアウトするため参照を残す)
        self._backend_rows: dict[LayerKind, list[ctk.CTkBaseClass]] = {}
        # グレーアウト解除時の復元色。ctk は text_color=None を受け付けない
        # (ValueError)ため、構築時の既定値を保存しておき復元に使う。
        self._default_row_text_color: object | None = None
        row = 0
        for layer, label in _LAYER_LABELS:
            label_widget = ctk.CTkLabel(body, text=f"{label}:")
            if self._default_row_text_color is None:
                self._default_row_text_color = label_widget.cget("text_color")
            label_widget.grid(row=row, column=0, sticky="w", padx=4, pady=2)
            internal_names = self._controller.list_backends(layer) or ["(未登録)"]
            # 表示用候補(layer ごとに display フォーマットを変換)
            names = self._render_backend_choices(layer, internal_names)
            current_internal = str(
                self._controller.get_setting(
                    "backends", layer.value, default=internal_names[0],
                )
            )
            current_display = self._backend_internal_to_display(layer, current_internal)
            var = ctk.StringVar(value=current_display)
            option = ctk.CTkOptionMenu(
                body,
                values=names,
                variable=var,
                command=lambda v, lyr=layer: self._on_backend_change(lyr, v),
            )
            option.grid(row=row, column=1, sticky="ew", padx=4, pady=2)
            self._backend_vars[layer] = var

            status_label = ctk.CTkLabel(body, text="-", text_color="#64748b", anchor="w")
            status_label.grid(row=row, column=2, sticky="ew", padx=(4, 4), pady=2)
            self._status_labels[layer] = status_label

            cfg_btn = ctk.CTkButton(
                body, text="設定", width=60,
                command=lambda lyr=layer: self._open_layer_settings(lyr),
            )
            cfg_btn.grid(row=row, column=3, sticky="e", padx=(0, 4), pady=2)

            self._backend_rows[layer] = [label_widget, option, status_label, cfg_btn]
            row += 1

        # 起動時に「TTS=(なし)」状態を反映: Output 行をグレーアウトする
        self._apply_tts_none_visual()

        body.columnconfigure(1, weight=1)
        body.columnconfigure(2, weight=0)
        body.columnconfigure(3, weight=0)
        return section

    # ----------------------------------------------------------
    # backend プルダウンの表示形式 ↔ 内部値変換(各レイヤの特例を吸収)
    # ----------------------------------------------------------
    def _render_backend_choices(
        self, layer: LayerKind, internal_names: list[str],
    ) -> list[str]:
        """`list_backends(layer)` の戻り値を layer 別の表示形式に整える。

        変換規則は `gui/logic/backend_display.py` に委譲:
        - TTS: 末尾に「(なし)」を追加(text_only モード切替)
        - CAPTURE: `<kind label> (<backend>)` 形式に変換
        - その他: backend 名そのまま
        """
        if layer == LayerKind.TTS:
            return list(internal_names) + [TTS_NONE_DISPLAY]
        if layer == LayerKind.CAPTURE:
            return [self._capture_internal_to_display(n) for n in internal_names]
        return list(internal_names)

    def _backend_internal_to_display(
        self, layer: LayerKind, internal: str,
    ) -> str:
        """指定レイヤの内部 backend 名を表示文字列に変換する。"""
        if layer == LayerKind.CAPTURE:
            return self._capture_internal_to_display(internal)
        return backend_internal_to_display(layer, internal)

    def _backend_display_to_internal(
        self, layer: LayerKind, display: str,
    ) -> str:
        """表示文字列を内部 backend 名に変換する。"""
        return backend_display_to_internal(layer, display)

    def _capture_internal_to_display(self, internal: str) -> str:
        """CAPTURE backend 名を「<kind label> (<backend>)」形式に変換する。

        kind の解決(controller 問い合わせ + 失敗時の縮退)だけここで行い、
        表示形式は `gui/logic/backend_display.py` に委譲する。
        """
        return capture_internal_to_display(internal, self._capture_kind_of(internal))

    def _capture_kind_of(self, backend_name: str) -> CaptureKind | None:
        """backend 名から CaptureKind を解決する。取れないときは None(表示は素通し)。

        防衛: 古い AppController モックや未登録 backend、空 / "(未登録)" 表示に対する縮退。
        """
        if not backend_name or backend_name == "(未登録)":
            return None
        try:
            kind = self._controller.get_capture_kind(backend_name)
        except Exception:  # noqa: BLE001
            return None
        return kind if isinstance(kind, CaptureKind) else None

    # ----------------------------------------------------------
    # TTS=(なし) 連動(Output 行のグレーアウト)
    # ----------------------------------------------------------
    def _apply_tts_none_visual(self) -> None:
        """TTS=(なし) のとき TTS+Output 行をグレーアウトする。

        TTS の StringVar 自体は維持(ユーザが「(なし)」を選んだら表示は「(なし)」のまま)。
        Output 行は完全に disable して触れないようにする。TTS 自身は「(なし) を解除する」
        ためにプルダウンだけ enable のままにする。

        **ステータスラベルには触らない**: ステータス欄のテキスト・色は編成表示
        (`_apply_absorbed_visuals` の「(なし)」上書き)と `_apply_status`(実状態の
        色付き再描画)の管轄。ここで色を初期化すると Loaded(緑)等の状態色を消してしまう。
        """
        is_none = self._controller.get_setting(
            "backends", LayerKind.TTS.value, default="",
        ) == TTS_NONE_INTERNAL
        rows = getattr(self, "_backend_rows", {})

        # Output 行: TTS=(なし) なら全要素 disable / グレーアウト(ステータス欄を除く)
        out_status = self._status_labels.get(LayerKind.OUTPUT)
        for w in rows.get(LayerKind.OUTPUT, []):
            try:
                if isinstance(w, (ctk.CTkOptionMenu, ctk.CTkButton)):
                    w.configure(
                        state="disabled" if is_none else self._interactive_state()
                    )
                elif isinstance(w, ctk.CTkLabel) and w is not out_status:
                    w.configure(
                        text_color=DISABLED_TEXT if is_none else self._restore_text_color()
                    )
            except Exception:  # noqa: BLE001 - widget 破棄 / プロパティ未対応で UI を止めない
                pass

        # TTS 行: ラベル行頭の色だけグレーアウト(プルダウン自体は触れる必要があるので enable)
        tts_status = self._status_labels.get(LayerKind.TTS)
        for w in rows.get(LayerKind.TTS, []):
            try:
                if isinstance(w, ctk.CTkLabel) and w is not tts_status:
                    w.configure(
                        text_color=DISABLED_TEXT if is_none else self._restore_text_color()
                    )
                elif isinstance(w, ctk.CTkButton):
                    # 設定ボタンは TTS=(なし) のとき意味がない → disable
                    w.configure(
                        state="disabled" if is_none else self._interactive_state()
                    )
            except Exception:  # noqa: BLE001
                pass

    def _restore_text_color(self) -> object:
        """グレーアウト解除時に戻す既定の文字色(構築時に保存した値)。"""
        return self._default_row_text_color

    def _interactive_state(self) -> str:
        """バックエンド行の操作 widget(プルダウン / 設定ボタン)の enable 状態。

        動作中(`is_running`)は "disabled": 選択を変えても動作には反映されず、
        「何で動いているのか」が表示と食い違うため、変更自体を塞ぐ。
        devices / languages は動作中の変更に対応済みなので対象外。
        問い合わせ失敗は「停止中」扱いに縮退(入力収集ヘルパ)。
        """
        try:
            running = bool(self._controller.is_running)
        except Exception:  # noqa: BLE001
            running = False
        return "disabled" if running else "normal"

    def _on_running_changed(self, running: bool) -> None:
        """running イベント(emit 元スレッド)→ バックエンド行のロック/解除を反映する。"""
        self.after(0, lambda: self._apply_running_lock_visual(bool(running)))

    def _apply_running_lock_visual(self, running: bool) -> None:
        """動作中はバックエンド行の操作 widget をすべて disable する。

        停止時は一括で normal に戻したあと、編成表示(吸収)と TTS=(なし) 由来の
        disable を再適用して整合させる。
        """
        rows = getattr(self, "_backend_rows", {})
        for widgets in rows.values():
            for w in widgets:
                try:
                    if isinstance(w, (ctk.CTkOptionMenu, ctk.CTkButton)):
                        w.configure(state="disabled" if running else "normal")
                except Exception:  # noqa: BLE001 - widget 破棄で UI を止めない
                    pass
        if not running:
            self._apply_absorbed_visuals()

    # ----------------------------------------------------------
    # セクション 2: デバイス
    # ----------------------------------------------------------
    def _build_devices_section(self) -> CollapsibleSection:
        initially_open = self._initial_open_state(_CFG_COLLAPSED_DEVICES)
        section = CollapsibleSection(
            self,
            title="デバイス",
            initially_open=initially_open,
            on_toggle=lambda is_open: self._persist_collapsed(
                _CFG_COLLAPSED_DEVICES, is_open
            ),
        )
        body = section.body

        ctk.CTkLabel(body, text="入力デバイス:").grid(
            row=0, column=0, sticky="w", padx=4, pady=2
        )
        # capture_kind に応じて 2 種類の UI を使い分ける(段階 3 / ProcTap):
        #   DEVICE  → 従来のプルダウン (`_capture_dropdown`)
        #   PROCESS → 「プロセス選択…」ボタン (`_capture_select_btn`)
        # 切替時はもう一方を `grid_remove()` して非表示にし、grid 領域は保持しない。
        self._capture_dropdown = ctk.CTkOptionMenu(
            body, values=["(列挙中)"], variable=self._capture_var,
            command=self._on_capture_changed,
        )
        self._capture_dropdown.grid(row=0, column=1, sticky="ew", padx=4, pady=2)

        self._capture_select_btn = ctk.CTkButton(
            body, text="プロセス選択…", command=self._on_capture_select_clicked,
        )
        # 初期状態では非表示(DEVICE kind backend が選ばれている前提)
        self._capture_select_btn.grid(row=0, column=1, sticky="ew", padx=4, pady=2)
        self._capture_select_btn.grid_remove()

        ctk.CTkLabel(body, text="出力デバイス:").grid(
            row=1, column=0, sticky="w", padx=4, pady=2
        )
        self._output_dropdown = ctk.CTkOptionMenu(
            body, values=["(列挙中)"], variable=self._output_var,
            command=self._on_output_changed,
        )
        self._output_dropdown.grid(row=1, column=1, sticky="ew", padx=4, pady=2)

        body.columnconfigure(1, weight=1)
        return section

    # ----------------------------------------------------------
    # セクション 3: 翻訳
    # ----------------------------------------------------------
    def _build_languages_section(self) -> CollapsibleSection:
        initially_open = self._initial_open_state(_CFG_COLLAPSED_LANGUAGES)
        section = CollapsibleSection(
            self,
            title="翻訳",
            initially_open=initially_open,
            on_toggle=lambda is_open: self._persist_collapsed(
                _CFG_COLLAPSED_LANGUAGES, is_open
            ),
        )
        body = section.body

        ctk.CTkLabel(body, text="入力言語 (src):").grid(
            row=0, column=0, sticky="w", padx=4, pady=2
        )
        self._src_dropdown = ctk.CTkOptionMenu(
            body,
            values=[format_language(c) for c in _FALLBACK_INPUT_LANGS],
            variable=self._src_var,
            command=self._on_src_lang_changed,
        )
        self._src_dropdown.grid(row=0, column=1, sticky="ew", padx=4, pady=2)

        ctk.CTkLabel(body, text="出力言語 (tgt):").grid(
            row=1, column=0, sticky="w", padx=4, pady=2
        )
        self._tgt_dropdown = ctk.CTkOptionMenu(
            body,
            values=[format_language(c) for c in _TGT_LANG_CHOICES],
            variable=self._tgt_var,
            command=self._on_tgt_lang_changed,
        )
        self._tgt_dropdown.grid(row=1, column=1, sticky="ew", padx=4, pady=2)

        body.columnconfigure(1, weight=1)
        return section

    # ----------------------------------------------------------
    # 共通行(セクション外)
    # ----------------------------------------------------------
    def _build_common_rows(self) -> None:
        # ログ出力先
        log_row = ctk.CTkFrame(self, fg_color="transparent")
        log_row.pack(fill="x", padx=10, pady=(8, 2))
        ctk.CTkLabel(log_row, text="ログ出力先:").pack(side="left")
        log_entry = ctk.CTkEntry(log_row, textvariable=self._log_dir_var)
        log_entry.pack(side="left", fill="x", expand=True, padx=(8, 0))
        log_entry.bind(
            "<FocusOut>",
            lambda _e: self._controller.set_setting("log", "directory", self._log_dir_var.get()),
        )

        # 保存/再読込/デバイス再列挙
        btn_frame = ctk.CTkFrame(self, fg_color="transparent")
        btn_frame.pack(fill="x", padx=10, pady=(4, 8))
        ctk.CTkButton(btn_frame, text="設定を保存", command=self._on_save).pack(
            side="left", padx=4
        )
        ctk.CTkButton(btn_frame, text="設定を再読込", command=self._on_reload).pack(
            side="left", padx=4
        )
        ctk.CTkButton(btn_frame, text="デバイス再列挙", command=self._populate_devices_into_dropdowns).pack(
            side="left", padx=4
        )

    # ----------------------------------------------------------
    # セクション開閉の永続化
    # ----------------------------------------------------------
    def _initial_open_state(self, key: tuple[str, ...]) -> bool:
        """ConfigStore に保存された "閉じてる" フラグを読み、open 状態を返す。

        保存形式: `is_collapsed: bool`(True=閉じてる)。default=False(=開)。
        """
        try:
            collapsed = bool(self._controller.get_setting(*key, default=False))
        except Exception:  # noqa: BLE001 - 設定取得失敗時は安全側で「開」
            collapsed = False
        return not collapsed

    def _persist_collapsed(self, key: tuple[str, ...], is_open: bool) -> None:
        """開閉状態を ConfigStore に保存する。`is_open` の論理否定を保存。

        書き込み失敗は無視(UI 操作が失敗で詰まらないことを優先)。
        """
        try:
            self._controller.set_setting(*key, not is_open)
        except Exception:  # noqa: BLE001
            pass

    # ----------------------------------------------------------
    def _open_layer_settings(self, layer: LayerKind) -> None:
        """指定レイヤの設定ダイアログを開く(モーダル風)。"""
        LayerSettingsDialog(self, self._controller, layer)

    # ============================================================
    # ステータス更新(AppController から呼ばれる)
    # ============================================================
    def on_status_change(self, layer: LayerKind, status: ModelStatus) -> None:
        """AppController からのコールバック(別スレッド可)。UIへ反映する。"""
        # tkinter はメインスレッドからの更新が必須
        self.after(0, lambda: self._apply_status(layer, status))

    def _apply_status(self, layer: LayerKind, status: ModelStatus) -> None:
        if layer in self._status_overridden:
            return  # 編成表示(吸収 / なし)を維持(対象外レイヤの実状態は表示しない)
        label = self._status_labels.get(layer)
        if label is None:
            return
        # 認証未完了(静的判定)はインスタンス状態より優先して見せる:
        # 未ロード(Init)でも「認証が必要」が伝わり、「Loaded なのに Start で
        # 認証エラー」の矛盾も出ない。判断は logic(auth_status_override)に委譲。
        override = auth_status_override(self._get_auth_state(layer))
        if override is not None:
            text, color = override
            label.configure(text=text, text_color=color)
            return
        text = self._format_status_text(layer, status)
        label.configure(
            text=text, text_color=STATUS_COLORS.get(status, STATUS_COLOR_DEFAULT)
        )

    def _get_auth_state(self, layer: LayerKind) -> AuthState:
        """認証準備状態の入力収集(controller 問い合わせ失敗は NOT_REQUIRED に縮退)。"""
        try:
            state = self._controller.get_auth_state(layer)
        except Exception:  # noqa: BLE001
            return AuthState.NOT_REQUIRED
        return state if isinstance(state, AuthState) else AuthState.NOT_REQUIRED

    def _on_settings_event(self, keys: tuple[str, ...]) -> None:
        """settings イベント(emit 元スレッド)。認証情報の変化だけ全行を再描画する。

        backend 変更は status イベント(INIT)経由で再描画されるためここでは扱わない。
        """
        if keys and keys[0] == "credentials":
            self.after(0, self._sync_all_status_labels)

    def _format_status_text(self, layer: LayerKind, status: ModelStatus) -> str:
        """Loaded のときだけ device 情報を併記する("Loaded (cuda)" 等)。

        device 概念を持たないレイヤ(Capture/VAD/TTS/Output)は status のみ表示。
        """
        if status != ModelStatus.LOADED:
            return status.value
        device = self._controller.get_layer_device(layer)
        if not device:
            return status.value
        return f"{status.value} ({device})"

    def _sync_all_status_labels(self) -> None:
        """初期化時/再読込後に全レイヤのステータスを一括反映する。"""
        for layer, status in self._controller.get_all_model_statuses().items():
            self._apply_status(layer, status)

    # ============================================================
    # 編成表示(吸収=「〜側で実行」/ 対象外=「(なし)」)
    # ============================================================
    def _absorbed_roles(self) -> dict[LayerKind, LayerKind]:
        """吸収状況を controller に問い合わせる(失敗時は「吸収なし」に縮退)。"""
        try:
            result = self._controller.get_absorbed_roles()
        except Exception:  # noqa: BLE001
            return {}
        return dict(result) if isinstance(result, dict) else {}

    def _skipped_roles(self) -> set[LayerKind]:
        """編成に載らないレイヤ(text_only の TTS/Output)。失敗時は「なし」に縮退。"""
        try:
            if self._controller.output_mode == "text_only":
                return {LayerKind.TTS, LayerKind.OUTPUT}
        except Exception:  # noqa: BLE001
            pass
        return set()

    def _apply_absorbed_visuals(self) -> None:
        """編成上「動かないレイヤ」の行に実態を表示する。

        - 複合 backend に吸収: ステータス欄は**空表示**(使われないレイヤであることは
          プルダウン/設定ボタンの disabled で伝わるため文言は出さない。どの backend が
          代行するかは動作タブのステータス集約に出る)。プルダウンと設定ボタンは
          disabled(選択値は保存されるが Start 時は無視。複合をやめた時の選択を
          保持するため、値自体は維持される)。
        - 編成対象外(text_only の TTS/Output): ステータス欄に「(なし)」。
          プルダウン/設定ボタンの disable は `_apply_tts_none_visual` が担当。
        - 解除されたレイヤ: 行ラベルの色と widget 状態と実ステータス表示に戻す。
        """
        absorbed = self._absorbed_roles()
        overrides: dict[LayerKind, str] = {layer: "" for layer in absorbed}
        for layer in self._skipped_roles():
            overrides.setdefault(layer, SKIPPED_STATUS_TEXT)

        prev = set(self._status_overridden)
        self._status_overridden = set(overrides)
        rows = getattr(self, "_backend_rows", {})

        for layer, text in overrides.items():
            status_label = self._status_labels.get(layer)
            if status_label is not None:
                try:
                    status_label.configure(text=text, text_color=DISABLED_TEXT)
                except Exception:  # noqa: BLE001 - widget 破棄で UI を止めない
                    pass
            # 吸収レイヤ: 行ラベルもグレー + プルダウン/設定ボタンを disable
            # (対象外レイヤの widget disable は _apply_tts_none_visual が担当)
            if layer in absorbed:
                for w in rows.get(layer, []):
                    try:
                        if isinstance(w, ctk.CTkLabel) and w is not status_label:
                            w.configure(text_color=DISABLED_TEXT)
                        elif isinstance(w, (ctk.CTkOptionMenu, ctk.CTkButton)):
                            w.configure(state="disabled")
                    except Exception:  # noqa: BLE001
                        pass

        # 表示上書きが解除されたレイヤ: 行ラベル色 + widget 状態を戻し、実ステータスで再描画する
        # (ctk は text_color=None を受け付けないため、保存済みの既定色で戻す)
        for layer in prev - set(overrides):
            for w in rows.get(layer, []):
                try:
                    if isinstance(w, ctk.CTkLabel):
                        w.configure(text_color=self._restore_text_color())
                    elif isinstance(w, (ctk.CTkOptionMenu, ctk.CTkButton)):
                        # 動作中は normal に戻さない(動作中ロックの管轄)
                        w.configure(state=self._interactive_state())
                except Exception:  # noqa: BLE001
                    pass
            try:
                self._apply_status(layer, self._controller.get_model_status(layer))
            except Exception:  # noqa: BLE001
                pass

        # TTS=(なし) 時の TTS/Output 行の disable は専用関数が管理しているため、
        # 吸収解除で widget を normal に戻したあとで再適用しておく(復帰直後の表示崩れ防止)。
        try:
            self._apply_tts_none_visual()
        except Exception:  # noqa: BLE001
            pass

    # ============================================================
    def _on_backend_change(self, layer: LayerKind, value: str) -> None:
        """backend 選択変更。クラウド backend なら同意ダイアログを先に通す(Phase D)。

        各レイヤの「表示 ↔ 内部値」変換は `_backend_display_to_internal` に委譲:
        - TTS: 「(なし)」 ↔ "none"
        - CAPTURE: 「デバイス (soundcard)」 ↔ "soundcard"
        - その他: 変換なし
        TTS の「(なし)」選択時は同意ダイアログを通さない(ローカル動作 = backend 起動しない)。
        """
        internal_value = self._backend_display_to_internal(layer, value)

        # 「(なし)」選択は同意ダイアログ不要(ローカル動作 = backend 起動しない)
        if internal_value != TTS_NONE_INTERNAL:
            if not self._gate_cloud_consent(layer, internal_value):
                # キャンセル: プルダウン表示を元の値に戻す
                current_internal = str(
                    self._controller.get_setting("backends", layer.value, default="")
                )
                current_display = self._backend_internal_to_display(
                    layer, current_internal,
                )
                var = self._backend_vars.get(layer)
                if var is not None:
                    var.set(current_display)
                return

        self._controller.set_setting("backends", layer.value, internal_value)
        # set_setting 側でステータスは更新されるが、ラベル反映は明示的に行う
        self._sync_all_status_labels()
        self._apply_absorbed_visuals()
        # ASR backend 切替時は入力言語プルダウンを新 backend の対応言語に合わせる。
        # 複合(翻訳吸収)⇔ 単体の切替で「翻訳先言語を決める backend」も変わるため、
        # 出力言語プルダウンも連動して再構築する。
        if layer == LayerKind.ASR:
            self._refresh_input_language_choices(value, notify_fallback=True)
            self._refresh_target_language_choices(notify_fallback=True)
        # Translator backend 切替時は出力言語プルダウンを再構築
        if layer == LayerKind.TRANSLATOR:
            self._refresh_target_language_choices(notify_fallback=True)
        # TTS backend 切替: Output 行のグレーアウト連動 + 出力言語候補の再構築
        # (候補は 翻訳 ∩ TTS のため、TTS が変わると候補も変わる)+ 言語互換チェック
        if layer == LayerKind.TTS:
            self._apply_tts_none_visual()
            self._refresh_target_language_choices(notify_fallback=True)
            if internal_value != TTS_NONE_INTERNAL:
                self._check_tts_output_lang_compatibility(notify_fallback=True)
        # CAPTURE backend 切替: 入力デバイスプルダウンを新 backend の `list_sources` で再列挙
        # (P5。ProcTap など複数 capture backend が並ぶ未来に備え、上段=backend / 下段=source の
        #  連動を保つ。Output 側は触らない)。
        if layer == LayerKind.CAPTURE:
            self._refresh_capture_sources_dropdown()

    # ============================================================
    # 入力言語プルダウンの連動(ASR backend ごとに対応言語が違う)
    # ============================================================
    def _on_src_lang_changed(self, displayed: str) -> None:
        """入力言語プルダウンの変更ハンドラ。表示形式を内部コードに変換して保存。"""
        code = parse_language(displayed)
        self._controller.set_setting("languages", "src", code)

    def _on_tgt_lang_changed(self, displayed: str) -> None:
        code = parse_language(displayed)
        self._controller.set_setting("languages", "tgt", code)
        # tgt を変えた結果、現在の TTS が読めない言語になっていないか警告
        self._check_tts_output_lang_compatibility(notify_fallback=True)

    def _refresh_input_language_choices(
        self, backend_name: str, *, notify_fallback: bool,
    ) -> None:
        """ASR backend に応じて入力言語プルダウンの選択肢を再構築する。

        候補・fallback の判断は `gui/logic/language_choices.py` に委譲。ここは
        controller への問い合わせ、dropdown / StringVar への反映、設定の書き戻し、
        通知バナーの発火だけを行う。
        `notify_fallback=False` のときは通知を出さない(起動時の初回構築用)。
        """
        if self._src_dropdown is None:
            return  # 初期化未完了時の防御

        sel = compute_src_selection(
            self._controller.get_supported_input_languages(backend_name),
            supports_auto=self._controller.supports_auto_detect(backend_name),
            current=str(
                self._controller.get_setting("languages", "src", default="auto")
            ),
            fallback_pool=_FALLBACK_INPUT_LANGS,
        )

        # 選択肢を再構築 + 表示形式を新リストの対応ラベルに合わせる
        self._src_dropdown.configure(values=[format_language(c) for c in sel.codes])
        self._src_var.set(format_language(sel.selected))

        if sel.fallback_from is None:
            return
        # 非対応 → fallback(設定を書き戻し、必要なら通知)
        self._controller.set_setting("languages", "src", sel.selected)
        if notify_fallback:
            self._notify_lang_fallback(sel.fallback_from, sel.selected, backend_name)

    def _notify_lang_fallback(self, old_code: str, new_code: str, backend_name: str) -> None:
        """入力言語が自動変更されたことを通知バナーで明示する。

        backend 切替の副作用として言語が変わるのは UI 操作の自然な帰結なので、
        確認ダイアログは出さず通知のみ(CLAUDE.md「ユーザ設定を勝手に変更しない」原則の
        例外扱い、ただし「黙って変える」のは避ける)。
        """
        self._notify_warning(
            format_src_fallback_message(old_code, new_code, backend_name)
        )

    def _notify_warning(self, msg: str) -> None:
        """警告バナーに出す(banner が無い / 失敗時は _show_message に縮退)。"""
        if self._banner is not None:
            try:
                self._banner.show_warning(msg)
                return
            except Exception:  # noqa: BLE001
                pass
        # banner が無い / 失敗時はログに落とす(テスト環境含む)
        self._show_message(msg)

    # ============================================================
    # 出力言語プルダウンの連動(Translator backend ごとに対応言語が違う)
    # ============================================================
    def _refresh_target_language_choices(self, *, notify_fallback: bool) -> None:
        """出力言語プルダウンを「翻訳 ∩ TTS」の対応言語で再構築する。

        候補のベースは「翻訳ロールを実際に担う backend」(通常は Translator、
        複合(ASR+翻訳)に吸収されている場合は複合 backend)の対応言語。
        TTS が有効(audio モード)なら、さらに TTS の読み上げ可能言語との積(AND)に
        絞る(TTS の対応言語が不明な backend は絞らない。積が空になる組合せは
        絞らずに従来の警告に委ねる)。候補・fallback(ja > en > 先頭)の判断は
        `gui/logic/language_choices.py` に委譲。ここは controller への問い合わせ、
        widget への反映、設定の書き戻し、通知バナーの発火、fallback 後の TTS 互換
        チェック連鎖だけを行う。
        """
        if self._tgt_dropdown is None:
            return

        tts_langs = self._active_tts_languages()
        sel = compute_tgt_selection(
            restrict_to_tts(self._effective_target_languages(), tts_langs),
            current=str(
                self._controller.get_setting("languages", "tgt", default="ja")
            ),
            fallback_pool=restrict_to_tts(_TGT_LANG_CHOICES, tts_langs),
        )

        self._tgt_dropdown.configure(values=[format_language(c) for c in sel.codes])
        self._tgt_var.set(format_language(sel.selected))

        if sel.fallback_from is None:
            return

        # 非対応 → fallback(設定を書き戻し、必要なら通知)
        self._controller.set_setting("languages", "tgt", sel.selected)
        if notify_fallback:
            self._notify_tgt_lang_fallback(
                sel.fallback_from, sel.selected, self._tgt_provider_name()
            )
        # tgt が fallback で変わった可能性があるので TTS 互換チェック
        self._check_tts_output_lang_compatibility(notify_fallback=notify_fallback)

    # ---- 翻訳先言語の問い合わせ(吸収を考慮した入力収集 + 失敗縮退)----
    def _effective_target_languages(self) -> list[str]:
        """翻訳先言語の候補を controller に問い合わせる。

        縮退: `get_effective_target_languages` を持たない旧 controller(モック含む)は
        従来どおり Translator レイヤの選択 backend へ直接問い合わせる。
        """
        try:
            return list(self._controller.get_effective_target_languages())
        except Exception:  # noqa: BLE001
            pass
        try:
            name = str(
                self._controller.get_setting(
                    "backends", LayerKind.TRANSLATOR.value, default="",
                ) or ""
            )
            return list(self._controller.get_supported_target_languages(name))
        except Exception:  # noqa: BLE001
            return []

    def _active_tts_languages(self) -> list[str]:
        """現在有効な TTS の読み上げ可能言語(候補の AND 用の入力収集)。

        TTS なし(text_only)/ backend 不明 / 取得失敗は [] を返し、
        `restrict_to_tts` 側で「制限しない」と解釈される。
        """
        try:
            tts_name = str(
                self._controller.get_setting(
                    "backends", LayerKind.TTS.value, default="",
                ) or ""
            )
        except Exception:  # noqa: BLE001
            return []
        if not tts_name or tts_name == TTS_NONE_INTERNAL:
            return []
        try:
            return list(self._controller.get_supported_output_languages(tts_name))
        except Exception:  # noqa: BLE001
            return []

    def _tgt_provider_name(self) -> str:
        """翻訳先言語を決めている backend 名(fallback 通知の文言用)。"""
        try:
            _, name = self._controller.get_target_language_provider()
            return name
        except Exception:  # noqa: BLE001
            pass
        try:
            return str(
                self._controller.get_setting(
                    "backends", LayerKind.TRANSLATOR.value, default="",
                ) or ""
            )
        except Exception:  # noqa: BLE001
            return ""

    # ============================================================
    # TTS 対応言語チェック(現在の出力言語が TTS で読めるか)
    # ============================================================
    def _check_tts_output_lang_compatibility(self, *, notify_fallback: bool) -> None:
        """現在の TTS backend が現在の出力言語(tgt)を読み上げ可能か確認し、
        対応外なら警告バナーを出す。

        警告要否の判断は `gui/logic/language_choices.py:tts_warning_needed` に委譲。
        - ユーザ選択(TTS / tgt_lang)は変更しない: TTS は「結果に対する制約」で
          因果関係が遠いため、勝手に切り替えず警告に留める
        - 呼び出し箇所: TTS backend 切替時 / tgt_lang 切替時 /
          Translator 切替後の fallback で tgt が変わった後
        - `notify_fallback=False` は起動時の初期化用(バナーを出さない)
        """
        tts_backend = str(
            self._controller.get_setting("backends", LayerKind.TTS.value, default="")
        )
        if not tts_backend or tts_backend == TTS_NONE_INTERNAL:
            # TTS=(なし) のときは text_only モードなので、読み上げ言語の警告は出さない
            # (supported の問い合わせ自体を省く)
            return
        current_tgt = str(self._controller.get_setting("languages", "tgt", default=""))
        if not tts_warning_needed(
            tts_backend=tts_backend,
            supported=self._controller.get_supported_output_languages(tts_backend),
            current_tgt=current_tgt,
        ):
            return
        if not notify_fallback:
            return
        self._notify_tts_unsupported_lang(current_tgt, tts_backend)

    def _notify_tts_unsupported_lang(self, tgt_code: str, backend_name: str) -> None:
        """TTS が現在の出力言語を読み上げられないことを通知バナーで明示する。"""
        self._notify_warning(format_tts_warning_message(tgt_code, backend_name))

    def _notify_tgt_lang_fallback(
        self, old_code: str, new_code: str, backend_name: str,
    ) -> None:
        self._notify_warning(
            format_tgt_fallback_message(old_code, new_code, backend_name)
        )

    def _gate_cloud_consent(self, layer: LayerKind, backend_name: str) -> bool:
        """クラウド backend なら同意ダイアログで gate する。同意あり/不要なら True。

        capability hint が未登録(=従来のローカル backend)なら無条件 True。
        """
        try:
            hint = self._controller.get_backend_capability_hint(layer, backend_name)
        except Exception:  # noqa: BLE001
            hint = None
        if hint is None or not hint.is_cloud:
            return True
        # ConsentDialog.maybe_show が既存同意 / suppress を見て即時 True を返す可能性あり
        return ConsentDialog.maybe_show(
            self,
            self._controller,
            backend_name=backend_name,
            service_name=hint.service_name or backend_name,
            terms_url=hint.terms_url,
        )

    def _populate_devices_into_dropdowns(self) -> None:
        """入力ソース / 出力デバイスの両方を再列挙する(初期化 / 再読込 / 再列挙ボタン用)。

        個別に呼びたい場合は `_refresh_capture_sources_dropdown` /
        `_refresh_output_devices_dropdown` を直接使う(P5: CAPTURE backend 切替時に
        input 側だけ再列挙したい等)。
        """
        self._refresh_capture_sources_dropdown()
        self._refresh_output_devices_dropdown()

    def _refresh_capture_sources_dropdown(self) -> None:
        """入力ソースの UI を「現在の `backends.capture` backend」の kind に基づいて再構築する。

        段階 3 で 2 モード対応:
          DEVICE  → 従来のプルダウン(`list_capture_sources()` の戻り値で埋める)
          PROCESS → 「プロセス選択…」ボタン(押下時にダイアログで PID を選ぶ)
        """
        kind = self._current_capture_kind()
        if kind == CaptureKind.PROCESS:
            self._show_process_select_ui()
        else:
            self._show_device_dropdown_ui()

    def _current_capture_kind(self) -> CaptureKind:
        """現在の capture backend の kind を取得する(取れないときは DEVICE フォールバック)。"""
        backend_name = str(
            self._controller.get_setting("backends", LayerKind.CAPTURE.value, default="")
        )
        if not backend_name:
            return CaptureKind.DEVICE
        try:
            kind = self._controller.get_capture_kind(backend_name)
        except Exception:  # noqa: BLE001
            return CaptureKind.DEVICE
        return kind if isinstance(kind, CaptureKind) else CaptureKind.DEVICE

    def _show_device_dropdown_ui(self) -> None:
        """DEVICE kind: 従来のプルダウンを表示し、ソースを列挙して埋める。"""
        # ボタンを隠してプルダウンを出す
        try:
            self._capture_select_btn.grid_remove()
        except Exception:  # noqa: BLE001
            pass
        try:
            self._capture_dropdown.grid()
        except Exception:  # noqa: BLE001
            pass

        try:
            sources = self._controller.list_capture_sources()
        except Exception as e:  # noqa: BLE001
            self._capture_dropdown.configure(values=[f"(取得失敗: {e})"])
            self._capture_id_map = {}
            return

        if not sources:
            self._capture_dropdown.configure(values=["(入力デバイスなし)"])
            self._capture_id_map = {}
            return

        self._capture_dropdown.configure(values=[s.display_name for s in sources])
        self._capture_id_map = {s.display_name: s.source_id for s in sources}
        current_id = self._controller.get_setting("devices", "input")
        for s in sources:
            if s.source_id == current_id:
                self._capture_var.set(s.display_name)
                return
        # 既存値が新一覧に無い → 先頭に fallback
        self._capture_var.set(sources[0].display_name)
        self._controller.set_setting("devices", "input", sources[0].source_id)

    def _show_process_select_ui(self) -> None:
        """PROCESS kind: プルダウンを隠してボタンを表示。ラベルは現在 PID で更新。"""
        try:
            self._capture_dropdown.grid_remove()
        except Exception:  # noqa: BLE001
            pass
        try:
            self._capture_select_btn.grid()
        except Exception:  # noqa: BLE001
            pass
        self._capture_id_map = {}
        self._update_capture_select_btn_label()

    def _update_capture_select_btn_label(self) -> None:
        """プロセス選択ボタンのラベルを現在の `devices.input` で同期する。

        未選択時: 「プロセス選択…」
        選択時:   「PID 1234 ▼」(プロセス名解決は重いのでダイアログ側で表示する)
        """
        current = self._controller.get_setting("devices", "input", default="")
        text = "プロセス選択…"
        if current:
            try:
                pid = int(str(current).strip())
                text = f"PID {pid} ▼"
            except (TypeError, ValueError):
                text = "プロセス選択…"
        try:
            self._capture_select_btn.configure(text=text)
        except Exception:  # noqa: BLE001
            pass

    def _on_capture_select_clicked(self) -> None:
        """「プロセス選択…」ボタン押下: ProcessSelectDialog を開き、OK で PID を保存。"""
        current = self._controller.get_setting("devices", "input", default="")
        initial_pid: int | None = None
        if current:
            try:
                initial_pid = int(str(current).strip())
            except (TypeError, ValueError):
                initial_pid = None

        try:
            dlg = ProcessSelectDialog(self, initial_pid=initial_pid)
        except Exception as e:  # noqa: BLE001
            self._show_message(f"プロセス選択ダイアログ起動失敗: {e}")
            return
        try:
            dlg.wait_window()
        except Exception:  # noqa: BLE001
            pass
        if dlg.result_pid is None:
            # Cancel / 閉じる
            return
        # `set_setting("devices", ...)` の書き込みだけで以下が連鎖する(P2):
        # - ControlPanel の ready 再計算(settings イベント購読。
        #   「プロセス未選択(disable)→ ▶ 開始(normal)」遷移)
        # - 動作中なら AppController 側で自動 restart + restart イベント
        self._controller.set_setting("devices", "input", str(dlg.result_pid))
        self._update_capture_select_btn_label()

    def _refresh_output_devices_dropdown(self) -> None:
        """出力デバイスプルダウンを再列挙する(挙動は capture 側と対称)。"""
        try:
            devices = self._controller.list_output_devices()
        except Exception as e:  # noqa: BLE001
            self._output_dropdown.configure(values=[f"(取得失敗: {e})"])
            self._output_id_map = {}
            return

        if not devices:
            self._output_dropdown.configure(values=["(出力デバイスなし)"])
            self._output_id_map = {}
            return

        self._output_dropdown.configure(values=[d.display_name for d in devices])
        self._output_id_map = {d.display_name: d.device_id for d in devices}
        current_id = self._controller.get_setting("devices", "output")
        for d in devices:
            if d.device_id == current_id:
                self._output_var.set(d.display_name)
                return
        self._output_var.set(devices[0].display_name)
        self._controller.set_setting("devices", "output", devices[0].device_id)

    def _on_capture_changed(self, display_name: str) -> None:
        device_id = self._capture_id_map.get(display_name)
        if device_id:
            # 動作中の自動 restart は AppController の set_setting 反応系が担う(P2)
            self._controller.set_setting("devices", "input", device_id)

    def _on_output_changed(self, display_name: str) -> None:
        device_id = self._output_id_map.get(display_name)
        if device_id:
            self._controller.set_setting("devices", "output", device_id)

    # ============================================================
    # 自動 restart のバナー連動(P2: AppController の restart イベントを購読)
    # ============================================================
    def _on_restart_event(self, event) -> None:
        """restart イベント受信(`vt_restart` 等の別スレッド)。after で marshalling。"""
        try:
            self.after(0, lambda: self._apply_restart_event(event))
        except Exception:  # noqa: BLE001 - widget 破棄後の通知は無視
            pass

    def _apply_restart_event(self, event) -> None:
        """restart ライフサイクルをバナーに反映する(started / completed / failed)。"""
        if self._banner is None:
            return
        try:
            if event.phase == "started":
                self._banner.show_info(
                    format_restart_started(event.device_key), duration_ms=0
                )
            elif event.phase == "completed":
                self._banner.dismiss()
            elif event.phase == "failed":
                self._banner.show_error(
                    format_restart_failed(event.device_key, event.message)
                )
        except Exception:  # noqa: BLE001 - バナー表示失敗で UI を止めない
            pass

    def _on_save(self) -> None:
        try:
            self._controller.save_settings()
        except Exception as e:  # noqa: BLE001
            self._show_message(f"保存失敗: {e}")
        else:
            self._show_message("設定を保存しました")

    def _on_reload(self) -> None:
        # 動作中 / ロード中の再読込は拒否する(2026-06-10 ドッグフーディング起票)。
        # 再読込は全 backend キャッシュを evict するため、動作中に走らせると
        # Coordinator が旧インスタンスを掴んだまま表示状態だけ INIT に戻り、
        # 表示と実行状態が食い違う。
        if self._reload_blocked():
            self._notify_warning(
                "動作中は設定を再読込できません(停止してから実行してください)"
            )
            return
        try:
            self._controller.load_settings()
        except Exception as e:  # noqa: BLE001
            self._show_message(f"読込失敗: {e}")
        else:
            self._populate_devices_into_dropdowns()
            self._sync_all_status_labels()
            self._apply_absorbed_visuals()
            self._show_message("設定を再読込しました")

    def _reload_blocked(self) -> bool:
        """再読込を拒否すべき状態(動作中 / ロード中)か。取得失敗は許可側に縮退。"""
        try:
            return bool(self._controller.is_running) or bool(
                self._controller.is_loading
            )
        except Exception:  # noqa: BLE001
            return False

    def _show_message(self, msg: str) -> None:
        print(f"[SettingsPanel] {msg}")
