"""LayerSettingsDialog: 単一レイヤの設定編集ダイアログ(customtkinter)。

役割: `layer_settings_schema.LAYER_SETTINGS` に従って入力欄を動的に組み立て、
保存時に `AppController.set_setting` 経由で ConfigStore に書き戻す。
スキーマ駆動なので、新しい設定項目はスキーマに足すだけで GUI に出現する。

注意: pipeline 関連の値は **「▶ 開始」を押した時** に Coordinator に渡される。
動作中に保存しても、いったん停止→開始しないと反映されない。ダイアログ下部に
そのヒントを表示する。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import customtkinter as ctk

from voice_translator.common.types import LayerKind

from .layer_settings_schema import (
    SettingField,
    parse_field_value,
    visible_fields,
)

if TYPE_CHECKING:
    from voice_translator.common.app_controller import AppController


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
        self._entries: dict[tuple[str, ...], tuple[SettingField, ctk.StringVar]] = {}

        self.title(f"{_LAYER_DISPLAY.get(layer, layer.value)} の設定")
        self.geometry("520x380")
        self.transient(parent)  # 親の前面に固定
        try:
            self.grab_set()  # フォーカスを奪う(モーダル風)
        except Exception:  # noqa: BLE001
            # 一部の環境(テスト等)で grab_set が失敗するのは許容
            pass

        self._build_widgets()

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
        else:
            for i, field in enumerate(fields):
                self._add_field_row(field, row=2 + i * 2)

        # メッセージ表示
        self._message_label = ctk.CTkLabel(self, text="", text_color="#94a3b8")
        self._message_label.grid(
            row=2 + max(len(fields), 1) * 2, column=0, columnspan=2,
            sticky="w", padx=12, pady=(4, 0),
        )

        # 操作ボタン
        btn_frame = ctk.CTkFrame(self, fg_color="transparent")
        btn_frame.grid(
            row=3 + max(len(fields), 1) * 2, column=0, columnspan=2,
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
        """1フィールド分の「ラベル + 入力欄 + ヘルプ」を grid に置く。"""
        current_value = self._controller.get_setting(*field.keys, default=field.default)
        var = ctk.StringVar(value="" if current_value is None else str(current_value))

        ctk.CTkLabel(self, text=field.label).grid(
            row=row, column=0, sticky="w", padx=12, pady=(8, 0)
        )
        entry = ctk.CTkEntry(self, textvariable=var)
        entry.grid(row=row, column=1, sticky="ew", padx=12, pady=(8, 0))

        if field.help_text:
            help_label = ctk.CTkLabel(
                self, text=field.help_text, text_color="#94a3b8", wraplength=460,
                justify="left",
            )
            help_label.grid(
                row=row + 1, column=0, columnspan=2, sticky="w", padx=12, pady=(0, 4)
            )

        self._entries[field.keys] = (field, var)

    # ----------------------------------------------------------
    def _on_save(self) -> None:
        """全入力欄を検証して ConfigStore に書き戻し、ダイアログを閉じる。

        変換失敗が1つでもあれば書き込まず、エラーメッセージを下部に表示する。
        """
        # 1) 全フィールドを変換(失敗があれば中止)
        new_values: list[tuple[tuple[str, ...], object]] = []
        for keys, (field, var) in self._entries.items():
            raw = var.get()
            try:
                value = parse_field_value(field.field_type, raw)
            except ValueError as e:
                self._message_label.configure(
                    text=f"入力エラー({field.label}): {e}",
                    text_color="#dc2626",
                )
                return
            new_values.append((keys, value))

        # 2) 書き戻し
        for keys, value in new_values:
            self._controller.set_setting(*keys, value)

        self._message_label.configure(
            text="保存しました。pipeline 値は次の「▶ 開始」で反映されます。",
            text_color="#16a34a",
        )
        # 短いタイマで閉じる(成功メッセージを一瞬見せる)
        self.after(800, self._dismiss)

    def _on_cancel(self) -> None:
        self._dismiss()

    def _dismiss(self) -> None:
        try:
            self.grab_release()
        except Exception:  # noqa: BLE001
            pass
        self.destroy()
