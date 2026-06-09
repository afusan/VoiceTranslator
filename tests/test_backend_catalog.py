"""BackendCatalog の単体テスト(P3 / refactor-ui-3move)。

AppController から移管されたメタ問合せの縮退規約
(未登録 / クラス未提供 / 例外 → 安全側既定値)を実体クラスで検証する。
互換窓(AppController の同名メソッド)経由の検証は test_app_controller.py 側に残る。
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from voice_translator.common.backend_catalog import BackendCatalog
from voice_translator.common.backend_registry import BackendRegistry
from voice_translator.common.types import (
    BackendCapabilities,
    CaptureKind,
    CredentialField,
    LayerKind,
)


class _MetaBackendCls:
    """全メタ API を宣言するテスト用 backend クラス。"""

    @classmethod
    def capture_kind(cls) -> CaptureKind:
        return CaptureKind.PROCESS

    @classmethod
    def supported_input_languages(cls) -> list[str]:
        return ["en", "ja"]

    @classmethod
    def supports_auto_detect(cls) -> bool:
        return True

    @classmethod
    def supported_target_languages(cls) -> list[str]:
        return ["ja", "fr"]

    @classmethod
    def supported_output_languages(cls) -> list[str]:
        return ["en"]

    @classmethod
    def credential_spec(cls) -> list[CredentialField]:
        return [CredentialField(key_name="api_key", label="API Key")]


class _BrokenBackendCls:
    """全メタ API が例外を吐くテスト用 backend クラス。"""

    @classmethod
    def capture_kind(cls):
        raise RuntimeError("boom")

    @classmethod
    def supported_input_languages(cls):
        raise RuntimeError("boom")

    @classmethod
    def supports_auto_detect(cls):
        raise RuntimeError("boom")

    @classmethod
    def supported_target_languages(cls):
        raise RuntimeError("boom")

    @classmethod
    def supported_output_languages(cls):
        raise RuntimeError("boom")

    @classmethod
    def credential_spec(cls):
        raise RuntimeError("boom")


@pytest.fixture()
def catalog_with_meta():
    """全レイヤに _MetaBackendCls を登録した catalog。"""
    reg = BackendRegistry()
    for layer in (
        LayerKind.CAPTURE, LayerKind.ASR, LayerKind.TRANSLATOR, LayerKind.TTS,
    ):
        reg.register(layer, "meta", lambda: MagicMock(), backend_cls=_MetaBackendCls)
    return BackendCatalog(reg)


@pytest.fixture()
def catalog_with_broken():
    reg = BackendRegistry()
    for layer in (
        LayerKind.CAPTURE, LayerKind.ASR, LayerKind.TRANSLATOR, LayerKind.TTS,
    ):
        reg.register(
            layer, "broken", lambda: MagicMock(), backend_cls=_BrokenBackendCls,
        )
    return BackendCatalog(reg)


@pytest.fixture()
def catalog_without_cls():
    """backend_cls を渡さず factory のみ登録した catalog(旧式登録の互換)。"""
    reg = BackendRegistry()
    for layer in (
        LayerKind.CAPTURE, LayerKind.ASR, LayerKind.TRANSLATOR, LayerKind.TTS,
    ):
        reg.register(layer, "no_cls", lambda: MagicMock())
    return BackendCatalog(reg)


class TestRegisteredClass:
    def test_capture_kind(self, catalog_with_meta) -> None:
        assert catalog_with_meta.get_capture_kind("meta") == CaptureKind.PROCESS

    def test_supported_input_languages(self, catalog_with_meta) -> None:
        assert catalog_with_meta.get_supported_input_languages("meta") == ["en", "ja"]

    def test_supports_auto_detect(self, catalog_with_meta) -> None:
        assert catalog_with_meta.supports_auto_detect("meta") is True

    def test_supported_target_languages(self, catalog_with_meta) -> None:
        assert catalog_with_meta.get_supported_target_languages("meta") == ["ja", "fr"]

    def test_supported_output_languages(self, catalog_with_meta) -> None:
        assert catalog_with_meta.get_supported_output_languages("meta") == ["en"]

    def test_credential_spec(self, catalog_with_meta) -> None:
        spec = catalog_with_meta.get_credential_spec(LayerKind.ASR, "meta")
        assert len(spec) == 1
        assert spec[0].key_name == "api_key"

    def test_capability_hint(self) -> None:
        reg = BackendRegistry()
        cap = BackendCapabilities(is_cloud=True, requires_credentials=True)
        reg.register(LayerKind.ASR, "cloud", lambda: None, capabilities=cap)
        catalog = BackendCatalog(reg)
        hint = catalog.get_capability_hint(LayerKind.ASR, "cloud")
        assert hint is not None
        assert hint.is_cloud is True


class TestUnregisteredFallsBack:
    """未登録 backend は安全側の既定値に縮退する。"""

    def test_defaults(self) -> None:
        catalog = BackendCatalog(BackendRegistry())
        assert catalog.get_capture_kind("unknown") == CaptureKind.DEVICE
        assert catalog.get_supported_input_languages("unknown") == []
        assert catalog.supports_auto_detect("unknown") is False
        assert catalog.get_supported_target_languages("unknown") == []
        assert catalog.get_supported_output_languages("unknown") == []
        assert catalog.get_credential_spec(LayerKind.ASR, "unknown") == []
        assert catalog.get_capability_hint(LayerKind.ASR, "unknown") is None


class TestClassNotProvidedFallsBack:
    """factory のみ登録(backend_cls なし)は既定値に縮退する。"""

    def test_defaults(self, catalog_without_cls) -> None:
        assert catalog_without_cls.get_capture_kind("no_cls") == CaptureKind.DEVICE
        assert catalog_without_cls.get_supported_input_languages("no_cls") == []
        assert catalog_without_cls.supports_auto_detect("no_cls") is False
        assert catalog_without_cls.get_credential_spec(LayerKind.TTS, "no_cls") == []


class TestExceptionFallsBack:
    """クラスメソッドの例外は飲んで既定値に縮退する(GUI の防御縮退が依存)。"""

    def test_defaults(self, catalog_with_broken) -> None:
        assert catalog_with_broken.get_capture_kind("broken") == CaptureKind.DEVICE
        assert catalog_with_broken.get_supported_input_languages("broken") == []
        assert catalog_with_broken.supports_auto_detect("broken") is False
        assert catalog_with_broken.get_supported_target_languages("broken") == []
        assert catalog_with_broken.get_supported_output_languages("broken") == []
        assert catalog_with_broken.get_credential_spec(LayerKind.ASR, "broken") == []


class TestNonCaptureKindValuePassesThrough:
    """capture_kind が CaptureKind 以外を返した場合はそのまま返す(判定は呼び出し側)。"""

    def test_pass_through(self) -> None:
        class _OddCls:
            @classmethod
            def capture_kind(cls):
                return "not-a-kind"

        reg = BackendRegistry()
        reg.register(
            LayerKind.CAPTURE, "odd", lambda: MagicMock(), backend_cls=_OddCls,
        )
        catalog = BackendCatalog(reg)
        assert catalog.get_capture_kind("odd") == "not-a-kind"
