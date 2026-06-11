"""BackendRegistry の単体テスト。"""

from __future__ import annotations

import pytest

from voice_translator.common.backend_registry import BackendRegistry
from voice_translator.common.types import LayerKind


class _Dummy:
    def __init__(self, label: str = "x") -> None:
        self.label = label


class TestBackendRegistry:
    def test_register_and_list(self) -> None:
        reg = BackendRegistry()
        reg.register(LayerKind.ASR, "faster_whisper", lambda: _Dummy("fw"))
        reg.register(LayerKind.ASR, "whisper_cpp", lambda: _Dummy("wc"))

        names = reg.list_names(LayerKind.ASR)
        assert names == ["faster_whisper", "whisper_cpp"]  # 登録順

    def test_is_registered(self) -> None:
        reg = BackendRegistry()
        reg.register(LayerKind.VAD, "silero", lambda: _Dummy())
        assert reg.is_registered(LayerKind.VAD, "silero") is True
        assert reg.is_registered(LayerKind.VAD, "other") is False
        assert reg.is_registered(LayerKind.ASR, "silero") is False  # 別レイヤ

    def test_create_returns_new_instance(self) -> None:
        reg = BackendRegistry()
        reg.register(LayerKind.TTS, "sapi", lambda: _Dummy("a"))
        a = reg.create(LayerKind.TTS, "sapi")
        b = reg.create(LayerKind.TTS, "sapi")
        assert isinstance(a, _Dummy) and isinstance(b, _Dummy)
        assert a is not b  # ファクトリ呼び出しごとに新規

    def test_create_unknown_raises_keyerror(self) -> None:
        reg = BackendRegistry()
        with pytest.raises(KeyError):
            reg.create(LayerKind.OUTPUT, "missing")

    def test_overwrite_registration(self) -> None:
        reg = BackendRegistry()
        reg.register(LayerKind.ASR, "x", lambda: _Dummy("v1"))
        reg.register(LayerKind.ASR, "x", lambda: _Dummy("v2"))
        instance = reg.create(LayerKind.ASR, "x")
        assert instance.label == "v2"


class TestRequiresModules:
    """opt-in extras backend の必要 import 名宣言(導入済み判定の材料)。"""

    def test_default_is_empty(self) -> None:
        """未宣言 = base 依存のみ(常に導入済み扱い)。"""
        reg = BackendRegistry()
        reg.register(LayerKind.ASR, "plain", lambda: _Dummy())
        assert reg.get_requires_modules(LayerKind.ASR, "plain") == ()

    def test_declared_modules_returned(self) -> None:
        reg = BackendRegistry()
        reg.register(
            LayerKind.ASR, "cloudy", lambda: _Dummy(),
            requires_modules=("httpx",),
        )
        assert reg.get_requires_modules(LayerKind.ASR, "cloudy") == ("httpx",)

    def test_unregistered_returns_empty(self) -> None:
        reg = BackendRegistry()
        assert reg.get_requires_modules(LayerKind.ASR, "nope") == ()

    def test_layer_isolation(self) -> None:
        reg = BackendRegistry()
        reg.register(LayerKind.ASR, "shared", lambda: _Dummy("asr"))
        reg.register(LayerKind.TTS, "shared", lambda: _Dummy("tts"))
        assert reg.create(LayerKind.ASR, "shared").label == "asr"
        assert reg.create(LayerKind.TTS, "shared").label == "tts"
