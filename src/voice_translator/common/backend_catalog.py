"""BackendCatalog: backend クラスのメタ情報問合せ口(状態なし)。

役割: BackendRegistry に登録された backend クラスのクラスメソッド
(capture_kind / supported_*_languages / supports_auto_detect / credential_spec)と
capability hint を、**インスタンス化せずに**引く。設定ダイアログやプルダウンの構築で
重い import / モデルロードを発生させないための窓口。

縮退規約(refactor-ui-3move P3 で AppController から移管、挙動は 1:1):
- backend 未登録 / クラス未提供 → 安全側の既定値(DEVICE / 空リスト / False / None)
- クラスメソッドが例外 → 既定値 + ログ(GUI の防御縮退がこの規約に依存している)
"""

from __future__ import annotations

import logging

from .backend_registry import BackendRegistry
from .types import BackendCapabilities, CaptureKind, CredentialField, LayerKind


class BackendCatalog:
    """BackendRegistry のメタ情報を安全に引く問合せ口(状態を持たない)。"""

    def __init__(
        self, registry: BackendRegistry, logger: logging.Logger | None = None,
    ) -> None:
        self._registry = registry
        self._logger = logger or logging.getLogger("voice_translator")

    # ---- 音声取得 backend の取得単位 ----
    def get_capture_kind(self, backend_name: str) -> CaptureKind:
        """指定 capture backend の取得単位(`CaptureKind`)を返す。

        backend 未登録 / 例外時は `CaptureKind.DEVICE`(安全側 = 従来挙動)を返す。
        backend クラスの `capture_kind()` クラスメソッドを呼ぶだけ(インスタンス化しない)。
        """
        cls = self._registry.get_backend_class(LayerKind.CAPTURE, backend_name)
        if cls is None:
            return CaptureKind.DEVICE
        try:
            kind = cls.capture_kind()
        except Exception:  # noqa: BLE001
            self._logger.exception(
                "capture_kind の呼び出し失敗 backend=%s", backend_name
            )
            return CaptureKind.DEVICE
        return kind

    # ---- ASR 対応言語 ----
    def get_supported_input_languages(self, backend_name: str) -> list[str]:
        """指定 ASR backend の対応入力言語(ISO 639-1)。失敗時は空リスト。"""
        cls = self._registry.get_backend_class(LayerKind.ASR, backend_name)
        if cls is None:
            return []
        try:
            return list(cls.supported_input_languages())
        except Exception:  # noqa: BLE001
            self._logger.exception(
                "supported_input_languages の呼び出し失敗 backend=%s", backend_name
            )
            return []

    def supports_auto_detect(self, backend_name: str) -> bool:
        """指定 ASR backend が言語自動検出に対応するか。失敗時は False(安全側)。"""
        cls = self._registry.get_backend_class(LayerKind.ASR, backend_name)
        if cls is None:
            return False
        try:
            return bool(cls.supports_auto_detect())
        except Exception:  # noqa: BLE001
            self._logger.exception(
                "supports_auto_detect の呼び出し失敗 backend=%s", backend_name
            )
            return False

    # ---- Translator 対応出力言語 ----
    def get_supported_target_languages(
        self, backend_name: str, *, layer: LayerKind = LayerKind.TRANSLATOR,
    ) -> list[str]:
        """指定 backend の対応出力言語(ISO 639-1)。失敗時は空リスト。

        通常は Translator レイヤに問い合わせる。翻訳ロールが複合 backend に
        吸収されている場合は、吸収先のレイヤ(`layer=ASR` 等)を指定して
        複合 backend の `supported_target_languages()` を引く。
        """
        cls = self._registry.get_backend_class(layer, backend_name)
        if cls is None:
            return []
        try:
            return list(cls.supported_target_languages())
        except Exception:  # noqa: BLE001
            self._logger.exception(
                "supported_target_languages の呼び出し失敗 backend=%s", backend_name
            )
            return []

    # ---- TTS 対応読み上げ言語 ----
    def get_supported_output_languages(self, backend_name: str) -> list[str]:
        """指定 TTS backend の対応読み上げ言語(ISO 639-1)。失敗時は空リスト。"""
        cls = self._registry.get_backend_class(LayerKind.TTS, backend_name)
        if cls is None:
            return []
        try:
            return list(cls.supported_output_languages())
        except Exception:  # noqa: BLE001
            self._logger.exception(
                "supported_output_languages の呼び出し失敗 backend=%s", backend_name
            )
            return []

    # ---- 認証スペック / capability hint ----
    def get_credential_spec(
        self, layer: LayerKind, name: str,
    ) -> list[CredentialField]:
        """指定 backend の認証情報スペック。登録なし / 例外時は空リスト。"""
        cls = self._registry.get_backend_class(layer, name)
        if cls is None:
            return []
        try:
            spec = cls.credential_spec()
        except Exception:  # noqa: BLE001
            self._logger.exception("credential_spec の呼び出し失敗 backend=%s", name)
            return []
        return list(spec)

    def get_capability_hint(
        self, layer: LayerKind, name: str,
    ) -> BackendCapabilities | None:
        """登録時に指定された capability ヒント。無ければ None。"""
        return self._registry.get_capability_hint(layer, name)
