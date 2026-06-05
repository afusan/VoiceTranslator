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
from voice_translator.common.types import CaptureKind, LayerKind, ModelStatus

from .collapsible_section import CollapsibleSection
from .consent_dialog import ConsentDialog
from .layer_settings_dialog import LayerSettingsDialog

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

# ModelStatus → 色マップ(customtkinter は色名そのまま使える)
_STATUS_COLORS: dict[ModelStatus, str] = {
    ModelStatus.INIT: "#64748b",            # slate gray (まだロード起動前)
    ModelStatus.NOT_DOWNLOADED: "#dc2626",  # red
    ModelStatus.LOADING: "#d97706",         # amber
    ModelStatus.LOADED: "#16a34a",          # green
}

# 各セクションの開閉永続化キー(default=False は「開」を表す。set_setting には
# is_open の論理否定を保存することで「閉じた状態だけ True を立てる」運用)。
_CFG_COLLAPSED_BACKENDS = ("ui", "collapsed", "backends")
_CFG_COLLAPSED_DEVICES = ("ui", "collapsed", "devices")
_CFG_COLLAPSED_LANGUAGES = ("ui", "collapsed", "languages")

# TTS backend に「(なし)」を選んだとき(= text_only モード)に無効化するレイヤ。
_OUTPUT_DISABLED_LAYERS: set[LayerKind] = {LayerKind.TTS, LayerKind.OUTPUT}

# TTS プルダウンの「(なし)」表示と内部値(2026-06-05 refactor)。
# 内部値 `_TTS_NONE_INTERNAL` は AppController.TTS_NONE と一致させること。
# BackendRegistry にこの名前の backend は登録しない前提。
_TTS_NONE_DISPLAY = "(なし)"
_TTS_NONE_INTERNAL = "none"


def _tts_display_to_internal(display: str) -> str:
    """TTS プルダウンの表示文字列を内部値に変換する。"""
    return _TTS_NONE_INTERNAL if display == _TTS_NONE_DISPLAY else display


def _tts_internal_to_display(internal: str) -> str:
    """TTS の内部値を表示文字列に変換する。"""
    return _TTS_NONE_DISPLAY if internal == _TTS_NONE_INTERNAL else internal


# 音声取得 backend の kind 表示ラベル(2026-06-05 / ProcTap 取り込み 段階1)。
# 「音声取得」プルダウンの表示は `<kind label> (<backend name>)` 形式。
# 内部値(ConfigStore `backends.capture`)は backend 名のまま維持する。
_CAPTURE_KIND_LABELS: dict[CaptureKind, str] = {
    CaptureKind.DEVICE: "デバイス",
    CaptureKind.PROCESS: "プロセス",
}


def _capture_display_to_internal(display: str) -> str:
    """「デバイス (soundcard)」のような表示文字列から backend 名を抽出する。

    形式 `<label> (<backend>)` の末尾カッコ内を取り出す。マッチしないものは
    そのまま返す(防衛: 未登録表示 `(未登録)` や旧式設定の互換)。
    """
    if display.endswith(")") and "(" in display:
        start = display.rindex("(") + 1
        return display[start:-1]
    return display



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
        # 起動時に入力言語プルダウンを ASR backend の対応言語に合わせて構築
        current_asr = str(controller.get_setting("backends", LayerKind.ASR.value, default=""))
        if current_asr:
            self._refresh_input_language_choices(current_asr, notify_fallback=False)
        # 出力言語プルダウンも Translator backend に合わせて構築
        current_translator = str(
            controller.get_setting("backends", LayerKind.TRANSLATOR.value, default="")
        )
        if current_translator:
            self._refresh_target_language_choices(current_translator, notify_fallback=False)
        # 起動時に TTS 互換も一度チェック(警告は出さない)
        self._check_tts_output_lang_compatibility(notify_fallback=False)

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
        row = 0
        for layer, label in _LAYER_LABELS:
            label_widget = ctk.CTkLabel(body, text=f"{label}:")
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

        - TTS: 末尾に「(なし)」を追加(text_only モード切替)
        - CAPTURE: `<kind label> (<backend>)` 形式に変換(段階 1 / ProcTap 取り込み準備)
        - その他: backend 名そのまま
        """
        if layer == LayerKind.TTS:
            return list(internal_names) + [_TTS_NONE_DISPLAY]
        if layer == LayerKind.CAPTURE:
            return [self._capture_internal_to_display(n) for n in internal_names]
        return list(internal_names)

    def _backend_internal_to_display(
        self, layer: LayerKind, internal: str,
    ) -> str:
        """指定レイヤの内部 backend 名を表示文字列に変換する。"""
        if layer == LayerKind.TTS:
            return _tts_internal_to_display(internal)
        if layer == LayerKind.CAPTURE:
            return self._capture_internal_to_display(internal)
        return internal

    def _backend_display_to_internal(
        self, layer: LayerKind, display: str,
    ) -> str:
        """表示文字列を内部 backend 名に変換する。"""
        if layer == LayerKind.TTS:
            return _tts_display_to_internal(display)
        if layer == LayerKind.CAPTURE:
            return _capture_display_to_internal(display)
        return display

    def _capture_internal_to_display(self, internal: str) -> str:
        """CAPTURE backend 名を「<kind label> (<backend>)」形式に変換する。

        kind が取れない / 未登録 / `CaptureKind` 以外の値は backend 名そのままを返す
        (防衛: 古い AppController モックや未登録 backend に対する縮退)。
        """
        if not internal or internal == "(未登録)":
            return internal
        try:
            kind = self._controller.get_capture_kind(internal)
        except Exception:  # noqa: BLE001
            return internal
        if not isinstance(kind, CaptureKind):
            return internal
        label = _CAPTURE_KIND_LABELS.get(kind, internal)
        return f"{label} ({internal})"

    # ----------------------------------------------------------
    # TTS=(なし) 連動(Output 行のグレーアウト)
    # ----------------------------------------------------------
    def _apply_tts_none_visual(self) -> None:
        """TTS=(なし) のとき TTS+Output 行をグレーアウトする。

        TTS の StringVar 自体は維持(ユーザが「(なし)」を選んだら表示は「(なし)」のまま)。
        Output 行は完全に disable して触れないようにする。TTS 自身は「(なし) を解除する」
        ためにプルダウンだけ enable のままにする。
        """
        is_none = self._controller.get_setting(
            "backends", LayerKind.TTS.value, default="",
        ) == _TTS_NONE_INTERNAL
        rows = getattr(self, "_backend_rows", {})

        # Output 行: TTS=(なし) なら全要素 disable / グレーアウト
        for w in rows.get(LayerKind.OUTPUT, []):
            try:
                if isinstance(w, (ctk.CTkOptionMenu, ctk.CTkButton)):
                    w.configure(state="disabled" if is_none else "normal")
                elif isinstance(w, ctk.CTkLabel):
                    w.configure(text_color="#475569" if is_none else None)
            except Exception:  # noqa: BLE001 - widget 破棄 / プロパティ未対応で UI を止めない
                pass

        # TTS 行: ラベル行頭の色だけグレーアウト(プルダウン自体は触れる必要があるので enable)
        tts_widgets = rows.get(LayerKind.TTS, [])
        for w in tts_widgets:
            try:
                if isinstance(w, ctk.CTkLabel):
                    w.configure(text_color="#475569" if is_none else None)
                elif isinstance(w, ctk.CTkButton):
                    # 設定ボタンは TTS=(なし) のとき意味がない → disable
                    w.configure(state="disabled" if is_none else "normal")
            except Exception:  # noqa: BLE001
                pass

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
        self._capture_dropdown = ctk.CTkOptionMenu(
            body, values=["(列挙中)"], variable=self._capture_var,
            command=self._on_capture_changed,
        )
        self._capture_dropdown.grid(row=0, column=1, sticky="ew", padx=4, pady=2)

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
        label = self._status_labels.get(layer)
        if label is None:
            return
        text = self._format_status_text(layer, status)
        label.configure(text=text, text_color=_STATUS_COLORS.get(status, "#64748b"))

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
        if internal_value != _TTS_NONE_INTERNAL:
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
        # ASR backend 切替時は入力言語プルダウンを新 backend の対応言語に合わせる
        if layer == LayerKind.ASR:
            self._refresh_input_language_choices(value, notify_fallback=True)
        # Translator backend 切替時は出力言語プルダウンを再構築
        if layer == LayerKind.TRANSLATOR:
            self._refresh_target_language_choices(value, notify_fallback=True)
        # TTS backend 切替: Output 行のグレーアウト連動 + 言語互換チェック
        if layer == LayerKind.TTS:
            self._apply_tts_none_visual()
            if internal_value != _TTS_NONE_INTERNAL:
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

        - backend の `supported_input_languages()` を引いて選択肢を組み立てる
        - 取得失敗 / 未対応 backend のときは fallback リストを使う
        - 既存設定値が新 backend で非対応のときは自動 fallback
          (auto 対応なら "auto"、非対応なら先頭言語)+ 通知バナーで明示
        - `notify_fallback=False` のときは通知を出さない(起動時の初回構築用)
        """
        if self._src_dropdown is None:
            return  # 初期化未完了時の防御

        codes = self._controller.get_supported_input_languages(backend_name)
        if not codes:
            codes = list(_FALLBACK_INPUT_LANGS)
        # 重複除去 + ソート(UI 表示の安定性)
        codes = sorted(set(codes))
        # auto 対応 backend なら先頭に追加
        if self._controller.supports_auto_detect(backend_name):
            codes = ["auto"] + codes

        # 選択肢を再構築
        labels = [format_language(c) for c in codes]
        self._src_dropdown.configure(values=labels)

        # 既存設定値の検証
        current_code = str(self._controller.get_setting("languages", "src", default="auto"))
        if current_code in codes:
            # 表示形式を新リストの対応ラベルに合わせる(format_language の変化に追従)
            self._src_var.set(format_language(current_code))
            return

        # 非対応 → fallback
        new_code = "auto" if "auto" in codes else codes[0]
        self._src_var.set(format_language(new_code))
        self._controller.set_setting("languages", "src", new_code)
        if notify_fallback:
            self._notify_lang_fallback(current_code, new_code, backend_name)

    def _notify_lang_fallback(self, old_code: str, new_code: str, backend_name: str) -> None:
        """入力言語が自動変更されたことを通知バナーで明示する。

        backend 切替の副作用として言語が変わるのは UI 操作の自然な帰結なので、
        確認ダイアログは出さず通知のみ(CLAUDE.md「ユーザ設定を勝手に変更しない」原則の
        例外扱い、ただし「黙って変える」のは避ける)。
        """
        msg = (
            f"入力言語を {format_language(old_code)} から {format_language(new_code)} に変更しました"
            f"({backend_name} が {old_code} に対応していないため)"
        )
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
    def _refresh_target_language_choices(
        self, backend_name: str, *, notify_fallback: bool,
    ) -> None:
        """Translator backend に応じて出力言語プルダウンの選択肢を再構築する。

        - backend の `supported_target_languages()` を引いて選択肢を組み立てる
        - 取得失敗 / 未対応 backend のときは fallback リストを使う
        - `"auto"` は含めない(出力言語に「自動」は意味を持たない)
        - 既存設定値が新 backend で非対応のとき:
          - 日本語があれば日本語に fallback(本アプリは日本語主用途)
          - 無ければ英語、両方無ければ先頭言語
          - `notify_fallback=True` なら通知バナーで明示
        """
        if self._tgt_dropdown is None:
            return

        codes = self._controller.get_supported_target_languages(backend_name)
        if not codes:
            codes = list(_TGT_LANG_CHOICES)
        codes = sorted(set(c for c in codes if c != "auto"))

        labels = [format_language(c) for c in codes]
        self._tgt_dropdown.configure(values=labels)

        current_code = str(self._controller.get_setting("languages", "tgt", default="ja"))
        if current_code in codes:
            self._tgt_var.set(format_language(current_code))
            return

        # 非対応 → fallback(日本語 > 英語 > 先頭)
        if "ja" in codes:
            new_code = "ja"
        elif "en" in codes:
            new_code = "en"
        else:
            new_code = codes[0]
        self._tgt_var.set(format_language(new_code))
        self._controller.set_setting("languages", "tgt", new_code)
        if notify_fallback:
            self._notify_tgt_lang_fallback(current_code, new_code, backend_name)
        # tgt が fallback で変わった可能性があるので TTS 互換チェック
        self._check_tts_output_lang_compatibility(notify_fallback=notify_fallback)

    # ============================================================
    # TTS 対応言語チェック(現在の出力言語が TTS で読めるか)
    # ============================================================
    def _check_tts_output_lang_compatibility(self, *, notify_fallback: bool) -> None:
        """現在の TTS backend が現在の出力言語(tgt)を読み上げ可能か確認し、
        対応外なら警告バナーを出す。

        - ユーザ選択(TTS / tgt_lang)は変更しない: TTS は「結果に対する制約」で
          因果関係が遠いため、勝手に切り替えず警告に留める
        - 呼び出し箇所: TTS backend 切替時 / tgt_lang 切替時 /
          Translator 切替後の fallback で tgt が変わった後
        - `notify_fallback=False` は起動時の初期化用(バナーを出さない)
        - 取得失敗 / 対応言語不明(空リスト)時は警告を出さない
        """
        tts_backend = str(
            self._controller.get_setting("backends", LayerKind.TTS.value, default="")
        )
        if not tts_backend or tts_backend == _TTS_NONE_INTERNAL:
            # TTS=(なし) のときは text_only モードなので、読み上げ言語の警告は出さない
            return
        supported = self._controller.get_supported_output_languages(tts_backend)
        if not supported:
            # 「分からない」backend は警告を出さない(誤検知より沈黙)
            return
        current_tgt = str(self._controller.get_setting("languages", "tgt", default=""))
        if current_tgt and current_tgt in supported:
            return
        if not notify_fallback:
            return
        self._notify_tts_unsupported_lang(current_tgt, tts_backend)

    def _notify_tts_unsupported_lang(self, tgt_code: str, backend_name: str) -> None:
        """TTS が現在の出力言語を読み上げられないことを通知バナーで明示する。"""
        msg = (
            f"TTS バックエンド {backend_name} は読み上げ言語 "
            f"{format_language(tgt_code)} に対応していません"
            "(Translator 出力言語を変えるか、別の TTS バックエンドに切り替えてください)"
        )
        if self._banner is not None:
            try:
                self._banner.show_warning(msg)
                return
            except Exception:  # noqa: BLE001
                pass
        self._show_message(msg)

    def _notify_tgt_lang_fallback(
        self, old_code: str, new_code: str, backend_name: str,
    ) -> None:
        msg = (
            f"出力言語を {format_language(old_code)} から {format_language(new_code)} に変更しました"
            f"({backend_name} が {old_code} に対応していないため)"
        )
        if self._banner is not None:
            try:
                self._banner.show_warning(msg)
                return
            except Exception:  # noqa: BLE001
                pass
        self._show_message(msg)

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
        """入力ソースプルダウンを「現在の `backends.capture` backend」に基づき再列挙する。

        - `AppController.list_capture_sources()` は現在の `backends.capture` 設定を
          見て当該 backend のソースを返す(`_create` 経由)。
        - 既存の `devices.input` 値が新ソース一覧に含まれていれば選択を維持。
          含まれていなければ先頭ソースに fallback し ConfigStore を更新する。
        - 取得失敗時は「(取得失敗: ...)」を表示し `_capture_id_map` を空にする
          (UI を壊さない)。
        """
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
            self._controller.set_setting("devices", "input", device_id)
            # P4: 動作中に変えたら自動 restart(停止→再開)
            if self._controller_is_running():
                self._trigger_device_restart("入力")

    def _on_output_changed(self, display_name: str) -> None:
        device_id = self._output_id_map.get(display_name)
        if device_id:
            self._controller.set_setting("devices", "output", device_id)
            if self._controller_is_running():
                self._trigger_device_restart("出力")

    # ============================================================
    # P4: 動作中デバイス変更の自動 restart
    # ============================================================
    def _controller_is_running(self) -> bool:
        """AppController.is_running を安全に問い合わせる(モック/古い実装対策)。"""
        try:
            return bool(self._controller.is_running)
        except Exception:  # noqa: BLE001
            return False

    def _trigger_device_restart(self, kind: str) -> None:
        """動作中のパイプラインを停止→再開する(デバイス切替の自動反映)。

        バナーに「(入力/出力)デバイスを切り替えました(再開中…)」を永続表示し、
        restart 完了で dismiss、失敗時は show_error で上書きする。
        """
        msg = f"{kind}デバイスを切り替えました(再開中…)"
        if self._banner is not None:
            try:
                self._banner.show_info(msg, duration_ms=0)
            except Exception:  # noqa: BLE001
                pass
        self._controller.restart_pipeline_async(
            on_restarted=lambda: self._on_restart_completed(kind),
            on_failed=lambda m: self._on_restart_failed(kind, m),
        )

    def _on_restart_completed(self, kind: str) -> None:
        """restart 完了通知(`vt_restart` スレッド上)。tk への反映は after で marshalling。"""
        try:
            self.after(0, self._apply_restart_completed)
        except Exception:  # noqa: BLE001
            pass

    def _apply_restart_completed(self) -> None:
        if self._banner is not None:
            try:
                self._banner.dismiss()
            except Exception:  # noqa: BLE001
                pass

    def _on_restart_failed(self, kind: str, message: str) -> None:
        """restart 失敗通知(`vt_restart` スレッド上)。"""
        try:
            self.after(0, lambda: self._apply_restart_failed(kind, message))
        except Exception:  # noqa: BLE001
            pass

    def _apply_restart_failed(self, kind: str, message: str) -> None:
        if self._banner is not None:
            try:
                self._banner.show_error(
                    f"{kind}デバイス変更後の再開に失敗しました: {message}"
                )
            except Exception:  # noqa: BLE001
                pass

    def _on_save(self) -> None:
        try:
            self._controller.save_settings()
        except Exception as e:  # noqa: BLE001
            self._show_message(f"保存失敗: {e}")
        else:
            self._show_message("設定を保存しました")

    def _on_reload(self) -> None:
        try:
            self._controller.load_settings()
        except Exception as e:  # noqa: BLE001
            self._show_message(f"読込失敗: {e}")
        else:
            self._populate_devices_into_dropdowns()
            self._sync_all_status_labels()
            self._show_message("設定を再読込しました")

    def _show_message(self, msg: str) -> None:
        print(f"[SettingsPanel] {msg}")
