"""LayerSettingsDialog: 単一レイヤの設定編集ダイアログ(customtkinter)。

役割: `layer_settings_schema.LAYER_SETTINGS` に従って入力欄を動的に組み立て、
保存時に `AppController.set_setting` 経由で ConfigStore に書き戻す。
スキーマ駆動なので、新しい設定項目はスキーマに足すだけで GUI に出現する。

Phase C2 で field_type ごとの dispatch を導入(`_add_<type>_row`):
- "int" / "float" / "str" / "bool" → 既存のテキスト入力(`_add_text_row`)
- "toggle" → ON/OFF スイッチ
- "dropdown" → プルダウン(options_fn で選択肢取得)
- "button" → クリックハンドラ(action_fn)
- "label_readonly" → 値表示のみ。reactive_to で示したレイヤの状態変化を購読して更新

R2-6: ダイアログ open 時に `controller.add_status_listener` を呼び、`_dismiss` で
明示的に unsubscribe してリーク・死んだ widget 参照を防ぐ。

注意: pipeline 関連の値は **「▶ 開始」を押した時** に Coordinator に渡される。
動作中に保存しても、いったん停止→開始しないと反映されない。ダイアログ下部に
そのヒントを表示する。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import customtkinter as ctk

from voice_translator.common.types import LayerKind, ModelStatus

from voice_translator.common.types import ModelInfo

from .layer_settings_schema import (
    CREDENTIAL_KEYS_MARKER,
    SettingField,
    format_model_option,
    parse_field_value,
    recent_durations_text,
    visible_fields,
)

if TYPE_CHECKING:
    from voice_translator.common.app_controller import AppController
    from voice_translator.common.backend_base import Subscription


# レイヤ表示名(`SettingsPanel` と揃えておく)
_LAYER_DISPLAY: dict[LayerKind, str] = {
    LayerKind.CAPTURE: "音声取得",
    LayerKind.VAD: "VAD",
    LayerKind.ASR: "ASR(書き起こし)",
    LayerKind.TRANSLATOR: "翻訳",
    LayerKind.TTS: "TTS(音声合成)",
    LayerKind.OUTPUT: "音声出力",
}


class LayerSettingsDialog(ctk.CTkToplevel):
    """1レイヤぶんの設定編集ウィンドウ。

    親ウィンドウの上にモーダル風に表示する(完全モーダルではない)。
    保存ボタン押下時に値を ConfigStore に書き戻して閉じる。キャンセルは破棄。
    """

    def __init__(
        self,
        parent,
        controller: "AppController",
        layer: LayerKind,
    ) -> None:
        super().__init__(parent)
        self._controller = controller
        self._layer = layer
        # text/toggle/dropdown は (field, var) を保持。button/label は別途。
        self._entries: dict[tuple[str, ...], tuple[SettingField, ctk.StringVar]] = {}
        # dropdown 用: 表示文字列 → 内部値(モデル名等)の変換 map。ModelInfo 表示用。
        self._dropdown_value_maps: dict[tuple[str, ...], dict[str, str]] = {}
        # label_readonly: (field, label widget) を保持し、状態変化で再描画する
        self._reactive_labels: list[tuple[SettingField, ctk.CTkLabel]] = []
        # AppController 状態変化購読(R2-6)
        self._status_subscription: "Subscription | None" = None

        self.title(f"{_LAYER_DISPLAY.get(layer, layer.value)} の設定")
        self.geometry("560x520")
        self.transient(parent)  # 親の前面に固定
        try:
            self.grab_set()  # フォーカスを奪う(モーダル風)
        except Exception:  # noqa: BLE001
            # 一部の環境(テスト等)で grab_set が失敗するのは許容
            pass

        self._build_widgets()
        self._subscribe_status()

    # ----------------------------------------------------------
    def _build_widgets(self) -> None:
        current_backend = str(
            self._controller.get_setting("backends", self._layer.value, default="")
        )
        fields = visible_fields(self._layer, current_backend)

        ctk.CTkLabel(
            self, text=f"{_LAYER_DISPLAY.get(self._layer, self._layer.value)}",
            font=("", 16, "bold"),
        ).grid(row=0, column=0, columnspan=2, sticky="w", padx=12, pady=(10, 0))
        ctk.CTkLabel(
            self, text=f"バックエンド: {current_backend or '(未選択)'}",
            text_color="#94a3b8",
        ).grid(row=1, column=0, columnspan=2, sticky="w", padx=12, pady=(0, 8))

        if not fields:
            ctk.CTkLabel(
                self, text="このレイヤに編集可能な設定はありません。",
                text_color="#94a3b8",
            ).grid(row=2, column=0, columnspan=2, sticky="w", padx=12, pady=12)
            field_rows = 1
        else:
            field_rows = 0
            for field_def in fields:
                self._add_field_row(field_def, row=2 + field_rows * 2)
                field_rows += 1

        # メッセージ表示
        self._message_label = ctk.CTkLabel(self, text="", text_color="#94a3b8")
        self._message_label.grid(
            row=2 + max(field_rows, 1) * 2, column=0, columnspan=2,
            sticky="w", padx=12, pady=(4, 0),
        )

        # 操作ボタン
        btn_frame = ctk.CTkFrame(self, fg_color="transparent")
        btn_frame.grid(
            row=3 + max(field_rows, 1) * 2, column=0, columnspan=2,
            sticky="e", padx=12, pady=(10, 12),
        )
        ctk.CTkButton(
            btn_frame, text="キャンセル", width=100, command=self._on_cancel,
        ).pack(side="right", padx=(8, 0))
        ctk.CTkButton(
            btn_frame, text="保存", width=100, command=self._on_save,
        ).pack(side="right")

        self.columnconfigure(1, weight=1)

    # ----------------------------------------------------------
    def _add_field_row(self, field: SettingField, *, row: int) -> None:
        """field_type に応じて適切な row 追加メソッドへ dispatch する(Phase C2)。"""
        ft = field.field_type
        if ft in ("int", "float", "str", "bool"):
            self._add_text_row(field, row=row)
        elif ft == "toggle":
            self._add_toggle_row(field, row=row)
        elif ft == "dropdown":
            self._add_dropdown_row(field, row=row)
        elif ft == "button":
            self._add_button_row(field, row=row)
        elif ft == "label_readonly":
            self._add_label_readonly_row(field, row=row)
        elif ft == "password":
            self._add_password_row(field, row=row)
        else:
            # 想定外型: ラベルだけ出して値は触らない(将来の追加に保険)
            ctk.CTkLabel(self, text=f"{field.label}(未対応型: {ft})").grid(
                row=row, column=0, columnspan=2, sticky="w", padx=12, pady=2
            )

    # ---- テキスト入力(従来の int/float/str/bool 用)----
    def _add_text_row(self, field: SettingField, *, row: int) -> None:
        current_value = self._controller.get_setting(*field.keys, default=field.default)
        var = ctk.StringVar(value="" if current_value is None else str(current_value))
        ctk.CTkLabel(self, text=field.label).grid(
            row=row, column=0, sticky="w", padx=12, pady=(8, 0)
        )
        entry = ctk.CTkEntry(self, textvariable=var)
        entry.grid(row=row, column=1, sticky="ew", padx=12, pady=(8, 0))
        self._add_help_row(field, row=row + 1)
        self._entries[field.keys] = (field, var)

    # ---- toggle(ON/OFF スイッチ)----
    def _add_toggle_row(self, field: SettingField, *, row: int) -> None:
        current_value = self._controller.get_setting(*field.keys, default=field.default)
        is_on = bool(current_value)
        var = ctk.StringVar(value="1" if is_on else "0")
        ctk.CTkLabel(self, text=field.label).grid(
            row=row, column=0, sticky="w", padx=12, pady=(8, 0)
        )
        switch = ctk.CTkSwitch(
            self, text="ON", variable=var, onvalue="1", offvalue="0",
        )
        switch.grid(row=row, column=1, sticky="w", padx=12, pady=(8, 0))
        self._add_help_row(field, row=row + 1)
        self._entries[field.keys] = (field, var)

    # ---- dropdown(プルダウン)----
    def _add_dropdown_row(self, field: SettingField, *, row: int) -> None:
        """選択肢を実行時に options_fn から取得する。

        ModelInfo のリストが返った場合は display_name + リソース目安 + fit アイコンで整形し、
        内部値(モデル名)とは分離して保持する。文字列リストならそのまま表示=値。
        """
        raw_opts: list = []
        if field.options_fn is not None:
            try:
                raw_opts = list(field.options_fn(self._controller, self._layer))
            except Exception:  # noqa: BLE001
                raw_opts = []

        # display_text → internal_value のマップを作る
        display_to_value: dict[str, str] = {}
        if raw_opts and all(isinstance(o, ModelInfo) for o in raw_opts):
            for m in raw_opts:
                display_to_value[format_model_option(m)] = m.name
        else:
            for o in raw_opts:
                s = str(o)
                display_to_value[s] = s

        if not display_to_value:
            # 選択肢が空 → 既定値だけ出す(編集はできるがほぼ機能しない)
            fallback = str(field.default) if field.default is not None else ""
            display_to_value[fallback or "(選択肢なし)"] = fallback

        options = list(display_to_value.keys())

        # 現在の内部値 → 表示テキスト
        current_value = self._controller.get_setting(*field.keys, default=field.default)
        current_value_str = "" if current_value is None else str(current_value)
        value_to_display = {v: d for d, v in display_to_value.items()}
        initial_display = value_to_display.get(current_value_str, options[0])

        var = ctk.StringVar(value=initial_display)
        ctk.CTkLabel(self, text=field.label).grid(
            row=row, column=0, sticky="w", padx=12, pady=(8, 0)
        )
        ctk.CTkOptionMenu(self, values=options, variable=var).grid(
            row=row, column=1, sticky="ew", padx=12, pady=(8, 0)
        )
        self._add_help_row(field, row=row + 1)
        self._entries[field.keys] = (field, var)
        self._dropdown_value_maps[field.keys] = display_to_value

    # ---- button(アクションボタン)----
    def _add_button_row(self, field: SettingField, *, row: int) -> None:
        ctk.CTkLabel(self, text=field.label).grid(
            row=row, column=0, sticky="w", padx=12, pady=(8, 0)
        )
        action = field.action_fn
        if action is None:
            ctk.CTkButton(self, text=field.label, state="disabled").grid(
                row=row, column=1, sticky="w", padx=12, pady=(8, 0)
            )
        else:
            ctk.CTkButton(
                self, text=field.label,
                command=lambda: action(self._controller, self._layer),
            ).grid(row=row, column=1, sticky="w", padx=12, pady=(8, 0))
        self._add_help_row(field, row=row + 1)

    # ---- label_readonly(値表示のみ、reactive_to で更新)----
    def _add_label_readonly_row(self, field: SettingField, *, row: int) -> None:
        ctk.CTkLabel(self, text=field.label).grid(
            row=row, column=0, sticky="w", padx=12, pady=(8, 0)
        )
        value_text = self._compute_label_value(field)
        value_label = ctk.CTkLabel(self, text=value_text, anchor="w")
        value_label.grid(row=row, column=1, sticky="ew", padx=12, pady=(8, 0))
        self._add_help_row(field, row=row + 1)
        if field.reactive_to:
            self._reactive_labels.append((field, value_label))

    @staticmethod
    def _compute_label_value(field: SettingField) -> str:
        """label_readonly の表示値を返す。recent_durations 専用の簡易ディスパッチ。

        将来別の label_readonly を増やすときは field.keys / 専用 callback で識別する。
        """
        return "—"  # 実値は `_refresh_reactive_labels` で上書きされる

    # ---- password(認証情報入力、CredentialsStore へ書く)----
    def _add_password_row(self, field: SettingField, *, row: int) -> None:
        """`keys = ("__credential__", backend, key_name)` 形式で credentials に保存する。

        既存値があればマスク表示。空のまま保存すると変更なし(誤って消さない設計)。
        """
        backend_name, key_name = self._parse_credential_keys(field.keys)
        if backend_name is None:
            # marker 不整合: テキストで縮退
            self._add_text_row(field, row=row)
            return

        try:
            current = self._controller.get_credential(backend_name, key_name)
        except Exception:  # noqa: BLE001
            current = None
        placeholder = "●●●●●●●● (設定済み、変更時のみ入力)" if current else "(未設定)"

        var = ctk.StringVar(value="")
        ctk.CTkLabel(self, text=field.label).grid(
            row=row, column=0, sticky="w", padx=12, pady=(8, 0)
        )
        entry = ctk.CTkEntry(
            self, textvariable=var, show="*", placeholder_text=placeholder
        )
        entry.grid(row=row, column=1, sticky="ew", padx=12, pady=(8, 0))
        self._add_help_row(field, row=row + 1)
        # 保存時に空欄なら触らない、非空なら set_credential する用に保持
        self._entries[field.keys] = (field, var)

    @staticmethod
    def _parse_credential_keys(keys: tuple[str, ...]) -> tuple[str | None, str | None]:
        """`("__credential__", backend, key_name)` を分解する。形式不正は (None, None)。"""
        if len(keys) >= 3 and keys[0] == CREDENTIAL_KEYS_MARKER:
            return keys[1], keys[2]
        return None, None

    def _add_help_row(self, field: SettingField, *, row: int) -> None:
        if not field.help_text:
            return
        ctk.CTkLabel(
            self, text=field.help_text, text_color="#94a3b8", wraplength=460,
            justify="left",
        ).grid(row=row, column=0, columnspan=2, sticky="w", padx=12, pady=(0, 4))

    # ============================================================
    # 状態変化購読(R2-6)
    # ============================================================
    def _subscribe_status(self) -> None:
        """AppController の状態変化を購読し、関連 layer のラベル/ボタンを更新する。"""
        try:
            self._status_subscription = self._controller.add_status_listener(
                self._on_status_change
            )
        except Exception:  # noqa: BLE001
            self._status_subscription = None
        # 初期描画
        self._refresh_reactive_labels()

    def _on_status_change(self, layer: LayerKind, status: ModelStatus) -> None:
        """AppController 由来の状態変化通知。メインスレッドにマーシャルして再描画。"""
        # 自レイヤ or reactive_to に含まれるレイヤだけが対象。
        if layer != self._layer and not any(
            layer in f.reactive_to for f, _ in self._reactive_labels
        ):
            return
        try:
            self.after(0, self._refresh_reactive_labels)
        except Exception:  # noqa: BLE001
            # widget が破棄済みなら無視(後段の unsubscribe で安全網)
            pass

    def _refresh_reactive_labels(self) -> None:
        """label_readonly の値を再計算して反映する(メインスレッド前提)。"""
        for field, widget in self._reactive_labels:
            try:
                # 直近処理時間表示 専用(他種を増やすときは field.keys で分岐)
                text = recent_durations_text(self._controller, self._layer)
                widget.configure(text=text)
            except Exception:  # noqa: BLE001
                pass

    # ----------------------------------------------------------
    def _on_save(self) -> None:
        """全入力欄を検証して ConfigStore に書き戻し、ダイアログを閉じる。

        変換失敗が1つでもあれば書き込まず、エラーメッセージを下部に表示する。
        button / label_readonly は書き込み対象外。
        """
        # 1) 全フィールドを変換(失敗があれば中止)
        new_values: list[tuple[tuple[str, ...], object]] = []
        credential_updates: list[tuple[str, str, str]] = []  # (backend, key_name, value)
        for keys, (field, var) in self._entries.items():
            if field.field_type in ("button", "label_readonly"):
                continue
            raw = var.get()
            # password: 空欄=未編集として扱う(誤って既存値を消さない)
            if field.field_type == "password":
                if raw == "":
                    continue
                b, k = self._parse_credential_keys(keys)
                if b is not None and k is not None:
                    credential_updates.append((b, k, raw))
                continue
            # dropdown: 表示文字列 → 内部値(モデル名等)に変換
            if field.field_type == "dropdown":
                value_map = self._dropdown_value_maps.get(keys, {})
                value = value_map.get(raw, raw)
                new_values.append((keys, value))
                continue
            try:
                value = parse_field_value(field.field_type, raw)
            except ValueError as e:
                self._message_label.configure(
                    text=f"入力エラー({field.label}): {e}",
                    text_color="#dc2626",
                )
                return
            new_values.append((keys, value))

        # 2) 書き戻し(ConfigStore 経路)
        for keys, value in new_values:
            self._controller.set_setting(*keys, value)
        # 認証情報(CredentialsStore 経路)
        for backend, key_name, value in credential_updates:
            try:
                self._controller.set_credential(backend, key_name, value)
            except Exception as e:  # noqa: BLE001
                self._message_label.configure(
                    text=f"認証情報保存に失敗: {e}", text_color="#dc2626"
                )
                return

        self._message_label.configure(
            text="保存しました。pipeline 値は次の「▶ 開始」で反映されます。",
            text_color="#16a34a",
        )
        # 短いタイマで閉じる(成功メッセージを一瞬見せる)
        self.after(800, self._dismiss)

    def _on_cancel(self) -> None:
        self._dismiss()

    def _dismiss(self) -> None:
        # 状態変化購読を明示解除(R2-6)
        if self._status_subscription is not None:
            try:
                self._status_subscription.unsubscribe()
            except Exception:  # noqa: BLE001
                pass
            self._status_subscription = None
        try:
            self.grab_release()
        except Exception:  # noqa: BLE001
            pass
        self.destroy()
