"""CredentialsService: 認証情報の保管・疎通確認・verified フラグ管理。

役割: CredentialsStore(keyring / 平文ファイル fallback)と ConfigStore の
`credentials.*` キーに閉じた認証フローの実体。backend キャッシュ(ロード状態)には
触らない — 認証成功後の reload 判定はランタイム(AppController)の責務。

refactor-ui-3move P3 で AppController から移管(挙動は 1:1):
- `set()` は verified フラグを自動で False に戻す(再認証必須)
- `verify_and_save()` は成功時のみ保存 + verified=True、空欄キーはスキップ
"""

from __future__ import annotations

import logging

from .backend_registry import BackendRegistry
from .config_store import ConfigStore
from .credentials import CredentialsStore
from .types import AuthState, LayerKind, VerifyResult


class CredentialsService:
    """クラウド backend の認証情報フロー(保管 / verify / verified 管理)。"""

    def __init__(
        self,
        *,
        registry: BackendRegistry,
        config: ConfigStore,
        logger: logging.Logger | None = None,
    ) -> None:
        self._registry = registry
        self._config = config
        self._logger = logger or logging.getLogger("voice_translator")
        # Phase D: 初回利用時に遅延初期化する
        # (テスト時の `keyring.set_keyring(InMemoryKeyring())` のタイミングを尊重)。
        self._store: CredentialsStore | None = None

    # ---- 保管 ----
    def _credentials_store(self) -> CredentialsStore:
        """`CredentialsStore` を遅延初期化して返す。

        ConfigStore の `credentials.use_local_file` フラグを反映。ロード/初回呼び出しの
        タイミングで生成する。
        """
        if self._store is None:
            use_local = bool(
                self._config.get("credentials", "use_local_file", default=False)
            )
            self._store = CredentialsStore(use_local_file=use_local)
        return self._store

    def get(self, backend: str, key: str) -> str | None:
        """指定 backend / key の認証情報を返す。未設定なら None。"""
        return self._credentials_store().get(backend, key)

    def set(self, backend: str, key: str, value: str) -> None:
        """指定 backend / key に認証情報を保存する。空文字は delete と同義。

        Phase E-2: キーが変わったら `verified` フラグを自動で False に戻す
        (再認証必須にして、古い verified 状態を引きずらない)。
        """
        self._credentials_store().set(backend, key, value)
        self._config.set("credentials", "verified", backend, False)

    def delete(self, backend: str, key: str) -> None:
        """指定 backend / key の認証情報を削除する。"""
        self._credentials_store().delete(backend, key)

    def has(self, backend: str, key: str) -> bool:
        """指定 backend / key の認証情報が設定済みか。"""
        return self._credentials_store().get(backend, key) is not None

    # ---- verified フラグ ----
    def is_backend_verified(self, backend_name: str) -> bool:
        """指定 backend が認証済みかを返す(ConfigStore で永続化)。

        `set()` 後に `verify_and_save()` が成功すると True になる。
        キー再入力 / `invalidate_verification` で False に戻る。
        """
        return bool(
            self._config.get("credentials", "verified", backend_name, default=False)
        )

    def invalidate_verification(self, backend_name: str) -> None:
        """サブスク切れ / API 401 等を観測したとき呼ぶ。`verified=False` に戻す。

        backend 実装の例外ハンドラから呼ばれて、次回 Start を gate する仕組み。
        """
        self._config.set("credentials", "verified", backend_name, False)

    def get_auth_state(self, layer: LayerKind, backend_name: str) -> AuthState:
        """指定 backend の認証準備状態を静的に判定する(インスタンス不要)。

        判定順は start 時の認証 gate(`_check_missing_credentials_gate`)と揃える:
        認証不要 → NOT_REQUIRED / spec の鍵が 1 つでも未保存 → MISSING /
        全鍵保存済みで verified=False → UNVERIFIED / それ以外 → VERIFIED。
        未登録 backend・spec 取得失敗は NOT_REQUIRED に縮退する(表示・ガードを
        誤判定で固めない。起動可否の最終判定は gate が行う)。
        """
        hint = self._registry.get_capability_hint(layer, backend_name)
        if hint is None or not hint.requires_credentials:
            return AuthState.NOT_REQUIRED
        cls = self._registry.get_backend_class(layer, backend_name)
        spec = []
        if cls is not None:
            try:
                spec = list(cls.credential_spec())
            except Exception:  # noqa: BLE001
                self._logger.exception(
                    "credential_spec の呼び出し失敗 backend=%s", backend_name
                )
        if any(not self.has(backend_name, f.key_name) for f in spec):
            return AuthState.MISSING
        if not self.is_backend_verified(backend_name):
            return AuthState.UNVERIFIED
        return AuthState.VERIFIED

    # ---- 疎通確認 ----
    def verify_and_save(
        self,
        layer: LayerKind,
        backend_name: str,
        values: dict[str, str],
    ) -> VerifyResult:
        """backend の `verify_credentials` を呼び、成功なら認証情報を保存する。

        1. backend クラスの `verify_credentials(values)` を呼ぶ
        2. 成功 → 各キーを保存(空欄=未編集はスキップ)、`credentials.verified.<backend>=True`
        3. 失敗 → 何も保存せず `VerifyResult` を返す(message を UI に表示)

        認証成功後に MISSING_CREDENTIALS 状態の backend を reload する後処理は
        ランタイム側(`AppController.verify_and_save_credentials`)が担う。
        """
        cls = self._registry.get_backend_class(layer, backend_name)
        if cls is None:
            return VerifyResult(
                ok=False,
                message=f"backend クラス未登録: layer={layer.value}, name={backend_name}",
            )
        try:
            result = cls.verify_credentials(values)
        except Exception as e:  # noqa: BLE001
            self._logger.exception(
                "verify_credentials で例外 backend=%s", backend_name
            )
            return VerifyResult(ok=False, message=f"検証中に例外: {e}")

        if not result.ok:
            return result

        # 保存。`set` は内部で verified=False に戻すので、後で True を立て直す。
        for key_name, value in values.items():
            if value == "":
                # 空欄(=未編集)はスキップ。既存値を消さない
                continue
            self.set(backend_name, key_name, value)
        self._config.set("credentials", "verified", backend_name, True)
        return result
