"""SettingsPanel: 設定操作UI(customtkinter)。

役割: バックエンド/デバイス/言語ペア/ログ出力先 のプルダウン+入力欄と、
設定の保存/読込ボタンを提供する。レイヤ別のモデルステータス(英語表示)も併記する。

入力言語(src)プルダウンは ASR backend ごとの対応言語に動的に追従する:
- backend 切替時に `_refresh_input_language_choices` で選択肢を再構築
- 既存設定値が新 backend で非対応のときは自動 fallback + 通知バナー
- 表示は `"en (English)"` 形式、内部値は `"en"`(共通言語テーブルで変換)
"""

from __future__ import annotations

import customtkinter as ctk

from voice_translator.common.app_controller import AppController
from voice_translator.common.languages import format_language, parse_language
from voice_translator.common.types import LayerKind, ModelStatus

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
        ctk.CTkLabel(self, text="設定", font=("", 16, "bold")).grid(
            row=0, column=0, columnspan=4, sticky="w", padx=10, pady=(8, 4)
        )

        row = 1
        # レイヤ実装の選択 + モデルステータス + 設定ボタン
        # ("バックエンド" 表記はユーザ向けには冗長なので外し、ラベル単体に統一)
        for layer, label in _LAYER_LABELS:
            ctk.CTkLabel(self, text=f"{label}:").grid(
                row=row, column=0, sticky="w", padx=10, pady=2
            )
            names = self._controller.list_backends(layer) or ["(未登録)"]
            current = str(self._controller.get_setting("backends", layer.value, default=names[0]))
            var = ctk.StringVar(value=current)
            option = ctk.CTkOptionMenu(
                self,
                values=names,
                variable=var,
                command=lambda v, lyr=layer: self._on_backend_change(lyr, v),
            )
            option.grid(row=row, column=1, sticky="ew", padx=10, pady=2)
            self._backend_vars[layer] = var

            status_label = ctk.CTkLabel(self, text="-", text_color="#64748b", anchor="w")
            status_label.grid(row=row, column=2, sticky="ew", padx=(4, 4), pady=2)
            self._status_labels[layer] = status_label

            # レイヤ別「設定」ボタン → LayerSettingsDialog
            ctk.CTkButton(
                self, text="設定", width=60,
                command=lambda lyr=layer: self._open_layer_settings(lyr),
            ).grid(row=row, column=3, sticky="e", padx=(0, 10), pady=2)

            row += 1

        # レイヤ実装グループとデバイス選択グループの境界線(視覚的な区切り)
        separator = ctk.CTkFrame(self, height=2, fg_color="#475569")
        separator.grid(
            row=row, column=0, columnspan=4, sticky="ew", padx=10, pady=(8, 8)
        )
        row += 1

        # 入力デバイス
        ctk.CTkLabel(self, text="入力デバイス:").grid(
            row=row, column=0, sticky="w", padx=10, pady=2
        )
        self._capture_dropdown = ctk.CTkOptionMenu(
            self, values=["(列挙中)"], variable=self._capture_var, command=self._on_capture_changed
        )
        self._capture_dropdown.grid(row=row, column=1, columnspan=3, sticky="ew", padx=10, pady=2)
        row += 1

        # 出力デバイス
        ctk.CTkLabel(self, text="出力デバイス:").grid(
            row=row, column=0, sticky="w", padx=10, pady=2
        )
        self._output_dropdown = ctk.CTkOptionMenu(
            self, values=["(列挙中)"], variable=self._output_var, command=self._on_output_changed
        )
        self._output_dropdown.grid(row=row, column=1, columnspan=3, sticky="ew", padx=10, pady=2)
        row += 1

        # src 言語(ASR backend に追従して再構築される)
        ctk.CTkLabel(self, text="入力言語 (src):").grid(
            row=row, column=0, sticky="w", padx=10, pady=2
        )
        self._src_dropdown = ctk.CTkOptionMenu(
            self,
            values=[format_language(c) for c in _FALLBACK_INPUT_LANGS],  # 初期は fallback
            variable=self._src_var,
            command=self._on_src_lang_changed,
        )
        self._src_dropdown.grid(row=row, column=1, columnspan=3, sticky="ew", padx=10, pady=2)
        row += 1

        # tgt 言語(Translator backend に追従して再構築される)
        ctk.CTkLabel(self, text="出力言語 (tgt):").grid(
            row=row, column=0, sticky="w", padx=10, pady=2
        )
        self._tgt_dropdown = ctk.CTkOptionMenu(
            self,
            values=[format_language(c) for c in _TGT_LANG_CHOICES],  # 初期は fallback
            variable=self._tgt_var,
            command=self._on_tgt_lang_changed,
        )
        self._tgt_dropdown.grid(row=row, column=1, columnspan=3, sticky="ew", padx=10, pady=2)
        row += 1

        # ログ出力先
        ctk.CTkLabel(self, text="ログ出力先:").grid(
            row=row, column=0, sticky="w", padx=10, pady=2
        )
        log_frame = ctk.CTkFrame(self)
        log_frame.grid(row=row, column=1, columnspan=3, sticky="ew", padx=10, pady=2)
        log_frame.columnconfigure(0, weight=1)
        log_entry = ctk.CTkEntry(log_frame, textvariable=self._log_dir_var)
        log_entry.grid(row=0, column=0, sticky="ew")
        log_entry.bind(
            "<FocusOut>",
            lambda _e: self._controller.set_setting("log", "directory", self._log_dir_var.get()),
        )
        row += 1

        # 保存/再読込
        btn_frame = ctk.CTkFrame(self)
        btn_frame.grid(row=row, column=0, columnspan=4, sticky="ew", padx=10, pady=(8, 8))
        ctk.CTkButton(btn_frame, text="設定を保存", command=self._on_save).pack(
            side="left", padx=4
        )
        ctk.CTkButton(btn_frame, text="設定を再読込", command=self._on_reload).pack(
            side="left", padx=4
        )
        ctk.CTkButton(btn_frame, text="デバイス再列挙", command=self._populate_devices_into_dropdowns).pack(
            side="left", padx=4
        )

        self.columnconfigure(1, weight=1)
        self.columnconfigure(2, weight=0)
        self.columnconfigure(3, weight=0)

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
        """backend 選択変更。クラウド backend なら同意ダイアログを先に通す(Phase D)。"""
        if not self._gate_cloud_consent(layer, value):
            # キャンセル: プルダウン表示を元の値に戻す
            current = str(self._controller.get_setting("backends", layer.value, default=""))
            var = self._backend_vars.get(layer)
            if var is not None:
                var.set(current)
            return
        self._controller.set_setting("backends", layer.value, value)
        # set_setting 側でステータスは更新されるが、ラベル反映は明示的に行う
        self._sync_all_status_labels()
        # ASR backend 切替時は入力言語プルダウンを新 backend の対応言語に合わせる
        if layer == LayerKind.ASR:
            self._refresh_input_language_choices(value, notify_fallback=True)
        # Translator backend 切替時は出力言語プルダウンを再構築
        if layer == LayerKind.TRANSLATOR:
            self._refresh_target_language_choices(value, notify_fallback=True)
        # TTS backend 切替時は現在の出力言語が新 TTS で読めるか警告チェック
        if layer == LayerKind.TTS:
            self._check_tts_output_lang_compatibility(notify_fallback=True)

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
        if not tts_backend:
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
        try:
            sources = self._controller.list_capture_sources()
        except Exception as e:  # noqa: BLE001
            sources = []
            self._capture_dropdown.configure(values=[f"(取得失敗: {e})"])
        try:
            devices = self._controller.list_output_devices()
        except Exception as e:  # noqa: BLE001
            devices = []
            self._output_dropdown.configure(values=[f"(取得失敗: {e})"])

        if sources:
            self._capture_dropdown.configure(values=[s.display_name for s in sources])
            self._capture_id_map = {s.display_name: s.source_id for s in sources}
            current_id = self._controller.get_setting("devices", "input")
            for s in sources:
                if s.source_id == current_id:
                    self._capture_var.set(s.display_name)
                    break
            else:
                self._capture_var.set(sources[0].display_name)
                self._controller.set_setting("devices", "input", sources[0].source_id)
        else:
            self._capture_id_map = {}

        if devices:
            self._output_dropdown.configure(values=[d.display_name for d in devices])
            self._output_id_map = {d.display_name: d.device_id for d in devices}
            current_id = self._controller.get_setting("devices", "output")
            for d in devices:
                if d.device_id == current_id:
                    self._output_var.set(d.display_name)
                    break
            else:
                self._output_var.set(devices[0].display_name)
                self._controller.set_setting("devices", "output", devices[0].device_id)
        else:
            self._output_id_map = {}

    def _on_capture_changed(self, display_name: str) -> None:
        device_id = self._capture_id_map.get(display_name)
        if device_id:
            self._controller.set_setting("devices", "input", device_id)

    def _on_output_changed(self, display_name: str) -> None:
        device_id = self._output_id_map.get(display_name)
        if device_id:
            self._controller.set_setting("devices", "output", device_id)

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
