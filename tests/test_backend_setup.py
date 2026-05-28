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


class TestSapiRateConfigIntegration:
    """SAPI バックエンドの rate が config から読まれることを検証。"""

    def test_default_rate_without_config(self, patched_backend_setup) -> None:
        from voice_translator.common.backend_setup import register_default_backends

        registry = BackendRegistry()
        register_default_backends(registry)  # config なし
        registry.create(LayerKind.TTS, "sapi")
        # SapiTtsBackend が rate=180 で呼ばれる
        patched_backend_setup["SapiTtsBackend"].assert_called_with(rate=180)

    def test_rate_read_from_config(self, patched_backend_setup, tmp_path) -> None:
        from voice_translator.common.backend_setup import register_default_backends
        from voice_translator.common.config_store import ConfigStore

        config = ConfigStore(tmp_path / "cfg.yaml")
        config.set("backends_config", "sapi", "rate", 250)

        registry = BackendRegistry()
        register_default_backends(registry, config)
        registry.create(LayerKind.TTS, "sapi")
        patched_backend_setup["SapiTtsBackend"].assert_called_with(rate=250)

    def test_invalid_rate_falls_back_to_default(self, patched_backend_setup, tmp_path) -> None:
        from voice_translator.common.backend_setup import register_default_backends
        from voice_translator.common.config_store import ConfigStore

        config = ConfigStore(tmp_path / "cfg.yaml")
        config.set("backends_config", "sapi", "rate", "not-a-number")

        registry = BackendRegistry()
        register_default_backends(registry, config)
        registry.create(LayerKind.TTS, "sapi")
        # 不正値は既定 180 にフォールバック
        patched_backend_setup["SapiTtsBackend"].assert_called_with(rate=180)


class TestFasterWhisperConfigIntegration:
    """faster-whisper の device/compute_type が config から読まれることを検証。"""

    def test_default_uses_auto(self, patched_backend_setup) -> None:
        from voice_translator.common.backend_setup import register_default_backends

        registry = BackendRegistry()
        register_default_backends(registry)
        registry.create(LayerKind.ASR, "faster_whisper")
        patched_backend_setup["FasterWhisperAsrBackend"].assert_called_with(
            device="auto", compute_type="auto"
        )

    def test_device_read_from_config(self, patched_backend_setup, tmp_path) -> None:
        from voice_translator.common.backend_setup import register_default_backends
        from voice_translator.common.config_store import ConfigStore

        config = ConfigStore(tmp_path / "cfg.yaml")
        config.set("backends_config", "faster_whisper", "device", "cuda")
        config.set("backends_config", "faster_whisper", "compute_type", "float16")

        registry = BackendRegistry()
        register_default_backends(registry, config)
        registry.create(LayerKind.ASR, "faster_whisper")
        patched_backend_setup["FasterWhisperAsrBackend"].assert_called_with(
            device="cuda", compute_type="float16"
        )


class TestNllb200ConfigIntegration:
    """NLLB-200 の device が config から読まれることを検証。"""

    def test_default_uses_auto(self, patched_backend_setup) -> None:
        from voice_translator.common.backend_setup import register_default_backends

        registry = BackendRegistry()
        register_default_backends(registry)
        registry.create(LayerKind.TRANSLATOR, "nllb200")
        patched_backend_setup["Nllb200TranslatorBackend"].assert_called_with(
            device="auto"
        )

    def test_device_read_from_config(self, patched_backend_setup, tmp_path) -> None:
        from voice_translator.common.backend_setup import register_default_backends
        from voice_translator.common.config_store import ConfigStore

        config = ConfigStore(tmp_path / "cfg.yaml")
        config.set("backends_config", "nllb200", "device", "cuda")

        registry = BackendRegistry()
        register_default_backends(registry, config)
        registry.create(LayerKind.TRANSLATOR, "nllb200")
        patched_backend_setup["Nllb200TranslatorBackend"].assert_called_with(
            device="cuda"
        )

    def test_empty_string_falls_back_to_default(
        self, patched_backend_setup, tmp_path
    ) -> None:
        from voice_translator.common.backend_setup import register_default_backends
        from voice_translator.common.config_store import ConfigStore

        config = ConfigStore(tmp_path / "cfg.yaml")
        config.set("backends_config", "nllb200", "device", "   ")  # 空白のみ

        registry = BackendRegistry()
        register_default_backends(registry, config)
        registry.create(LayerKind.TRANSLATOR, "nllb200")
        patched_backend_setup["Nllb200TranslatorBackend"].assert_called_with(
            device="auto"
        )


class TestSileroVadConfigIntegration:
    """Silero VAD のパラメータが config から読まれることを検証。"""

    def test_default_params_without_config(self, patched_backend_setup) -> None:
        from voice_translator.common.backend_setup import register_default_backends

        registry = BackendRegistry()
        register_default_backends(registry)  # config なし
        registry.create(LayerKind.VAD, "silero")
        # 既定値で呼ばれる
        patched_backend_setup["SileroVadBackend"].assert_called_with(
            threshold=0.5,
            min_silence_ms=500,
            speech_pad_ms=100,
            max_speech_sec=8.0,
        )

    def test_params_read_from_config(self, patched_backend_setup, tmp_path) -> None:
        from voice_translator.common.backend_setup import register_default_backends
        from voice_translator.common.config_store import ConfigStore

        config = ConfigStore(tmp_path / "cfg.yaml")
        config.set("backends_config", "silero", "threshold", 0.6)
        config.set("backends_config", "silero", "min_silence_ms", 200)
        config.set("backends_config", "silero", "speech_pad_ms", 50)
        config.set("backends_config", "silero", "max_speech_sec", 5.0)

        registry = BackendRegistry()
        register_default_backends(registry, config)
        registry.create(LayerKind.VAD, "silero")
        patched_backend_setup["SileroVadBackend"].assert_called_with(
            threshold=0.6,
            min_silence_ms=200,
            speech_pad_ms=50,
            max_speech_sec=5.0,
        )

    def test_invalid_values_fall_back_to_defaults(
        self, patched_backend_setup, tmp_path
    ) -> None:
        from voice_translator.common.backend_setup import register_default_backends
        from voice_translator.common.config_store import ConfigStore

        config = ConfigStore(tmp_path / "cfg.yaml")
        config.set("backends_config", "silero", "threshold", "bad")
        config.set("backends_config", "silero", "min_silence_ms", "bad")
        config.set("backends_config", "silero", "max_speech_sec", None)

        registry = BackendRegistry()
        register_default_backends(registry, config)
        registry.create(LayerKind.VAD, "silero")
        patched_backend_setup["SileroVadBackend"].assert_called_with(
            threshold=0.5,
            min_silence_ms=500,
            speech_pad_ms=100,
            max_speech_sec=8.0,
        )
