"""CredentialDialog: 汎用認証情報入力ダイアログ(customtkinter)。

役割: backend が `credential_spec()` で宣言したフィールドから入力欄を動的に組み立て、
「テスト」ボタンで `verify_credentials()` を呼び、成功なら `CredentialsStore` に保存する
までを 1 つのダイアログにパックする(Phase E-2)。

ライフサイクル:
1. 起動 → backend の `credential_spec()` からフィールド生成
2. 既存値があれば placeholder で「●●●●(設定済み)」を表示(secret フィールド)
3. ユーザが入力 → 「テスト」クリック → AppController.verify_and_save_credentials
4. 成功 → 緑メッセージ「認証 OK: ...」→ 自動で閉じる
5. 失敗 → 赤メッセージで原因表示、入力は保持(ユーザが修正してリトライ)

設計の前提:
- backend の verify_credentials は **例外を投げない** 想定(VerifyResult で返す)。
  例外が来ても AppController が catch して result に変換する
- 既存値があるフィールドを空欄で「テスト」した場合、AppController が空欄を「未編集」と
  扱って既存値で検証する(誤って既存値を消さない)
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import customtkinter as ctk

from voice_translator.common.types import LayerKind

if TYPE_CHECKING:
    from voice_translator.common.app_controller import AppController


class CredentialDialog(ctk.CTkToplevel):
    """汎用の認証情報入力 + 検証ダイアログ。"""

    def __init__(
        self,
        parent,
        controller: "AppController",
        *,
        layer: LayerKind,
        backend_name: str,
        service_name: str | None = None,
    ) -> None:
        super().__init__(parent)
        self._controller = controller
        self._layer = layer
        self._backend_name = backend_name
        self._service_name = service_name or backend_name
        # key_name -> StringVar
        self._field_vars: dict[str, ctk.StringVar] = {}
        # key_name -> 既存値があるか(送信時の「空欄=未編集」判定用)
        self._has_existing: dict[str, bool] = {}

        self.title(f"認証情報の入力 — {self._service_name}")
        self.geometry("560x520")
        self.transient(parent)
        try:
            self.grab_set()
        except Exception:  # noqa: BLE001
            pass

        self._build_widgets()
        self.protocol("WM_DELETE_WINDOW", self._on_cancel)

    # ----------------------------------------------------------
    def _build_widgets(self) -> None:
        spec = self._controller.get_credential_spec(self._layer, self._backend_name)

        ctk.CTkLabel(
            self, text=f"認証情報の入力 — {self._service_name}",
            font=("", 16, "bold"),
        ).grid(row=0, column=0, columnspan=2, sticky="w", padx=12, pady=(12, 4))

        ctk.CTkLabel(
            self,
            text=(
                "下のフィールドに API キー等を入力し、「テスト」を押してください。"
                "成功すると保存され、「動作開始」ボタンが押せるようになります。"
            ),
            text_color="#94a3b8", wraplength=520, justify="left", anchor="w",
        ).grid(row=1, column=0, columnspan=2, sticky="ew", padx=12, pady=(0, 8))

        if not spec:
            ctk.CTkLabel(
                self, text="このバックエンドは認証情報を要求していません。",
                text_color="#94a3b8",
            ).grid(row=2, column=0, columnspan=2, sticky="w", padx=12, pady=12)
            field_rows = 1
        else:
            field_rows = 0
            for f in spec:
                self._add_field_row(f, row=2 + field_rows * 2)
                field_rows += 1

        # 検証結果メッセージ
        self._message_label = ctk.CTkLabel(
            self, text="", text_color="#94a3b8",
            wraplength=520, justify="left", anchor="w",
        )
        self._message_label.grid(
            row=2 + max(field_rows, 1) * 2,
            column=0, columnspan=2, sticky="ew", padx=12, pady=(4, 0),
        )

        # ボタン
        btn_frame = ctk.CTkFrame(self, fg_color="transparent")
        btn_frame.grid(
            row=3 + max(field_rows, 1) * 2,
            column=0, columnspan=2, sticky="e", padx=12, pady=(10, 12),
        )
        ctk.CTkButton(
            btn_frame, text="キャンセル", width=110, command=self._on_cancel,
        ).pack(side="right", padx=(8, 0))
        self._test_btn = ctk.CTkButton(
            btn_frame, text="テスト", width=110, command=self._on_test,
        )
        self._test_btn.pack(side="right")

        self.columnconfigure(1, weight=1)

    def _add_field_row(self, field, *, row: int) -> None:
        """1 フィールド分の入力欄 + ヘルプを置く。"""
        existing = self._controller.get_credential(self._backend_name, field.key_name)
        self._has_existing[field.key_name] = bool(existing)
        placeholder = (
            "●●●●●●●● (設定済み、変更時のみ入力)" if existing else "(未設定)"
        )

        var = ctk.StringVar(value="")
        ctk.CTkLabel(self, text=field.label).grid(
            row=row, column=0, sticky="w", padx=12, pady=(8, 0)
        )
        entry_kwargs = {"textvariable": var, "placeholder_text": placeholder}
        if field.secret:
            entry_kwargs["show"] = "*"
        ctk.CTkEntry(self, **entry_kwargs).grid(
            row=row, column=1, sticky="ew", padx=12, pady=(8, 0)
        )
        if field.help_text:
            ctk.CTkLabel(
                self, text=field.help_text, text_color="#94a3b8",
                wraplength=520, justify="left",
            ).grid(
                row=row + 1, column=0, columnspan=2,
                sticky="w", padx=12, pady=(0, 4),
            )
        self._field_vars[field.key_name] = var

    # ----------------------------------------------------------
    def _on_test(self) -> None:
        """入力値で `verify_and_save_credentials` を呼ぶ。

        空欄のフィールドは「未編集」として扱い、既存値が使われる(=既存値で再検証)。
        """
        values: dict[str, str] = {}
        for key_name, var in self._field_vars.items():
            raw = var.get()
            if raw == "" and self._has_existing.get(key_name):
                existing = self._controller.get_credential(
                    self._backend_name, key_name
                )
                values[key_name] = existing or ""
            else:
                values[key_name] = raw

        # 必須フィールドの未入力チェック
        missing = [k for k, v in values.items() if v == ""]
        if missing:
            self._message_label.configure(
                text=f"未入力のフィールドがあります: {', '.join(missing)}",
                text_color="#dc2626",
            )
            return

        self._test_btn.configure(state="disabled", text="テスト中…")
        try:
            result = self._controller.verify_and_save_credentials(
                self._layer, self._backend_name, values
            )
        except Exception as e:  # noqa: BLE001
            self._test_btn.configure(state="normal", text="テスト")
            self._message_label.configure(
                text=f"検証中に内部エラー: {e}",
                text_color="#dc2626",
            )
            return
        self._test_btn.configure(state="normal", text="テスト")

        if result.ok:
            self._message_label.configure(
                text=f"✓ 認証 OK — {result.message or '保存しました'}",
                text_color="#16a34a",
            )
            self.after(800, self._dismiss)
        else:
            self._message_label.configure(
                text=f"✗ 認証失敗 — {result.message or '原因不明'}",
                text_color="#dc2626",
            )

    def _on_cancel(self) -> None:
        self._dismiss()

    def _dismiss(self) -> None:
        try:
            self.grab_release()
        except Exception:  # noqa: BLE001
            pass
        self.destroy()
