"""register_default_backends のテスト。

実バックエンドのコンストラクタは重い(モデル初期化)ため、
各クラスをモックに差し替えて「登録だけ」が行われることを検証する。
"""

from __future__ import annotations

import importlib
import sys
from unittest.mock import MagicMock

import pytest

from voice_translator.common.backend_registry import BackendRegistry
from voice_translator.common.types import LayerKind


@pytest.fixture()
def patched_backend_setup(monkeypatch):
    """各バックエンドモジュールをモック差し替え。register後にimportが走るので、
    sys.modules への差し替えで対応する。"""
    fake_classes = {}
    for path in [
        ("voice_translator.capture.soundcard_backend", "SoundcardCaptureBackend"),
        ("voice_translator.vad.silero_backend", "SileroVadBackend"),
        ("voice_translator.asr.faster_whisper_backend", "FasterWhisperAsrBackend"),
        ("voice_translator.translator.nllb200_backend", "Nllb200TranslatorBackend"),
        ("voice_translator.tts.sapi_backend", "SapiTtsBackend"),
        ("voice_translator.output.soundcard_backend", "SoundcardOutputBackend"),
    ]:
        mod_name, cls_name = path
        fake_module = MagicMock()
        fake_class = MagicMock(name=cls_name)
        setattr(fake_module, cls_name, fake_class)
        monkeypatch.setitem(sys.modules, mod_name, fake_module)
        fake_classes[cls_name] = fake_class

    # backend_setup を再 import して、差し替えた module 参照を取り込ませる
    if "voice_translator.common.backend_setup" in sys.modules:
        importlib.reload(sys.modules["voice_translator.common.backend_setup"])
    return fake_classes


class TestRegisterDefaultBackends:
    def test_all_layers_registered(self, patched_backend_setup) -> None:
        from voice_translator.common.backend_setup import register_default_backends

        registry = BackendRegistry()
        register_default_backends(registry)

        assert registry.list_names(LayerKind.CAPTURE) == ["soundcard"]
        assert registry.list_names(LayerKind.VAD) == ["silero"]
        assert registry.list_names(LayerKind.ASR) == ["faster_whisper"]
        assert registry.list_names(LayerKind.TRANSLATOR) == ["nllb200"]
        assert registry.list_names(LayerKind.TTS) == ["sapi"]
        assert registry.list_names(LayerKind.OUTPUT) == ["soundcard"]

    def test_factory_create_invokes_class(self, patched_backend_setup) -> None:
        from voice_translator.common.backend_setup import register_default_backends

        registry = BackendRegistry()
        register_default_backends(registry)

        registry.create(LayerKind.ASR, "faster_whisper")
        patched_backend_setup["FasterWhisperAsrBackend"].assert_called_once()
