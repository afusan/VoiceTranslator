"""CredentialsService の単体テスト(P3 / refactor-ui-3move)。

AppController から移管された認証フロー(保管 / verified 管理 / verify_and_save)を
実体クラスで検証する。互換窓(AppController の同名メソッド)+ Phase F1 後処理は
test_app_controller.py / test_credential_flow.py 側に残る。
"""

from __future__ import annotations

from unittest.mock import MagicMock

import keyring
import pytest

from tests._fixtures import InMemoryKeyring
from voice_translator.common.backend_registry import BackendRegistry
from voice_translator.common.config_store import ConfigStore
from voice_translator.common.credentials_service import CredentialsService
from voice_translator.common.types import LayerKind, VerifyResult


class _VerifyOkCls:
    seen_values: dict | None = None

    @classmethod
    def verify_credentials(cls, values: dict[str, str]) -> VerifyResult:
        cls.seen_values = dict(values)
        return VerifyResult(ok=True, message="ok")


class _VerifyNgCls:
    @classmethod
    def verify_credentials(cls, values: dict[str, str]) -> VerifyResult:
        return VerifyResult(ok=False, message="invalid key")


class _VerifyRaisesCls:
    @classmethod
    def verify_credentials(cls, values: dict[str, str]) -> VerifyResult:
        raise RuntimeError("network down")


@pytest.fixture()
def service():
    """InMemoryKeyring を注入した CredentialsService(レジストリに verify 用クラス登録)。"""
    keyring.set_keyring(InMemoryKeyring())
    reg = BackendRegistry()
    reg.register(LayerKind.ASR, "ok_backend", lambda: MagicMock(), backend_cls=_VerifyOkCls)
    reg.register(LayerKind.ASR, "ng_backend", lambda: MagicMock(), backend_cls=_VerifyNgCls)
    reg.register(
        LayerKind.ASR, "raise_backend", lambda: MagicMock(), backend_cls=_VerifyRaisesCls,
    )
    config = ConfigStore(path="dummy", data={})
    return CredentialsService(registry=reg, config=config, logger=None)


class TestStoreBasics:
    def test_set_get_has_delete(self, service) -> None:
        assert service.has("a", "k") is False
        service.set("a", "k", "v")
        assert service.get("a", "k") == "v"
        assert service.has("a", "k") is True
        service.delete("a", "k")
        assert service.get("a", "k") is None

    def test_set_resets_verified_flag(self, service) -> None:
        service._config.set("credentials", "verified", "a", True)  # noqa: SLF001
        service.set("a", "k", "v")
        assert service.is_backend_verified("a") is False

    def test_invalidate_verification(self, service) -> None:
        service._config.set("credentials", "verified", "a", True)  # noqa: SLF001
        service.invalidate_verification("a")
        assert service.is_backend_verified("a") is False

    def test_verified_default_false(self, service) -> None:
        assert service.is_backend_verified("never_seen") is False


class TestVerifyAndSave:
    def test_success_saves_and_marks_verified(self, service) -> None:
        result = service.verify_and_save(
            LayerKind.ASR, "ok_backend", {"api_key": "sk-1"},
        )
        assert result.ok is True
        assert service.get("ok_backend", "api_key") == "sk-1"
        assert service.is_backend_verified("ok_backend") is True
        # verify_credentials に入力値がそのまま渡る
        assert _VerifyOkCls.seen_values == {"api_key": "sk-1"}

    def test_empty_value_is_skipped_keeping_existing(self, service) -> None:
        """空欄(=未編集)キーは保存をスキップし、既存値を消さない。"""
        service.set("ok_backend", "api_key", "sk-old")
        result = service.verify_and_save(
            LayerKind.ASR, "ok_backend", {"api_key": ""},
        )
        assert result.ok is True
        assert service.get("ok_backend", "api_key") == "sk-old"
        assert service.is_backend_verified("ok_backend") is True

    def test_failure_saves_nothing(self, service) -> None:
        result = service.verify_and_save(
            LayerKind.ASR, "ng_backend", {"api_key": "bad"},
        )
        assert result.ok is False
        assert "invalid key" in result.message
        assert service.get("ng_backend", "api_key") is None
        assert service.is_backend_verified("ng_backend") is False

    def test_unregistered_backend_returns_ng(self, service) -> None:
        result = service.verify_and_save(LayerKind.ASR, "nope", {"api_key": "x"})
        assert result.ok is False
        assert "未登録" in result.message

    def test_exception_in_verify_returns_ng(self, service) -> None:
        result = service.verify_and_save(
            LayerKind.ASR, "raise_backend", {"api_key": "x"},
        )
        assert result.ok is False
        assert "検証中に例外" in result.message
        assert service.is_backend_verified("raise_backend") is False


class TestLazyStoreInit:
    def test_use_local_file_flag_respected(self, tmp_path, monkeypatch) -> None:
        """`credentials.use_local_file=True` なら file モードで遅延生成される。"""
        monkeypatch.chdir(tmp_path)  # local.secrets が散らからないように
        config = ConfigStore(
            path="dummy", data={"credentials": {"use_local_file": True}},
        )
        service = CredentialsService(
            registry=BackendRegistry(), config=config, logger=None,
        )
        assert service._store is None  # noqa: SLF001 - まだ生成されない
        service.set("deepl", "api_key", "v")
        assert service._store is not None  # noqa: SLF001
        assert service._store.mode == "file"  # noqa: SLF001
