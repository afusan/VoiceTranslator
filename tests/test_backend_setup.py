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
        ("voice_translator.capture.proctap_backend", "ProcTapCaptureBackend"),
        ("voice_translator.vad.silero_backend", "SileroVadBackend"),
        ("voice_translator.vad.webrtc_backend", "WebRtcVadBackend"),
        ("voice_translator.vad.pyannote_backend", "PyannoteVadBackend"),
        ("voice_translator.vad.pvcobra_backend", "PvcobraVadBackend"),
        ("voice_translator.asr.faster_whisper_backend", "FasterWhisperAsrBackend"),
        ("voice_translator.asr.openai_whisper_backend", "OpenAiWhisperAsrBackend"),
        ("voice_translator.asr.openai_whisper_api_backend", "OpenAiWhisperApiAsrBackend"),
        ("voice_translator.asr.google_stt_backend", "GoogleSttAsrBackend"),
        ("voice_translator.asr.deepgram_backend", "DeepgramAsrBackend"),
        ("voice_translator.translator.nllb200_backend", "Nllb200TranslatorBackend"),
        ("voice_translator.translator.deepl_backend", "DeepLTranslatorBackend"),
        ("voice_translator.translator.openai_gpt_backend", "OpenAiGptTranslatorBackend"),
        ("voice_translator.translator.anthropic_claude_backend", "AnthropicClaudeTranslatorBackend"),
        ("voice_translator.tts.sapi_backend", "SapiTtsBackend"),
        ("voice_translator.tts.piper_backend", "PiperTtsBackend"),
        ("voice_translator.tts.elevenlabs_backend", "ElevenLabsTtsBackend"),
        ("voice_translator.tts.openai_tts_backend", "OpenAiTtsBackend"),
        ("voice_translator.tts.google_cloud_tts_backend", "GoogleCloudTtsBackend"),
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

        assert registry.list_names(LayerKind.CAPTURE) == ["soundcard", "proctap"]
        # Phase F1 で VAD に webrtcvad / pyannote / pvcobra を追加。silero が先頭(MVP)。
        assert registry.list_names(LayerKind.VAD) == [
            "silero", "webrtcvad", "pyannote", "pvcobra",
        ]
        # Phase F2 で openai-whisper(公式)+ OpenAI Whisper API + Google STT + Deepgram を追加。
        # MVP の faster-whisper が先頭。
        assert registry.list_names(LayerKind.ASR) == [
            "faster_whisper", "openai_whisper", "openai_whisper_api",
            "google_stt", "deepgram",
        ]
        # Phase F2 で DeepL / OpenAI GPT / Anthropic Claude を追加。
        # MVP の nllb200 が先頭。
        assert registry.list_names(LayerKind.TRANSLATOR) == [
            "nllb200", "deepl", "openai_gpt", "anthropic_claude",
        ]
        # Phase F2 で Piper / ElevenLabs / OpenAI TTS / Google Cloud TTS を追加。
        # MVP の sapi が先頭。
        assert registry.list_names(LayerKind.TTS) == [
            "sapi", "piper", "elevenlabs", "openai_tts", "google_tts",
        ]
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
        # model_size はデフォルト "small"(Phase: model dropdown 対応)
        patched_backend_setup["FasterWhisperAsrBackend"].assert_called_with(
            model_size="small", device="auto", compute_type="auto"
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
            model_size="small", device="cuda", compute_type="float16"
        )

    def test_model_size_read_from_config(
        self, patched_backend_setup, tmp_path
    ) -> None:
        """設定から model_size を変更すると、その値で WhisperModel が呼ばれる。"""
        from voice_translator.common.backend_setup import register_default_backends
        from voice_translator.common.config_store import ConfigStore

        config = ConfigStore(tmp_path / "cfg.yaml")
        config.set("backends_config", "faster_whisper", "model_size", "medium")

        registry = BackendRegistry()
        register_default_backends(registry, config)
        registry.create(LayerKind.ASR, "faster_whisper")
        patched_backend_setup["FasterWhisperAsrBackend"].assert_called_with(
            model_size="medium", device="auto", compute_type="auto"
        )

    def test_empty_model_size_falls_back_to_default(
        self, patched_backend_setup, tmp_path
    ) -> None:
        from voice_translator.common.backend_setup import register_default_backends
        from voice_translator.common.config_store import ConfigStore

        config = ConfigStore(tmp_path / "cfg.yaml")
        config.set("backends_config", "faster_whisper", "model_size", "  ")

        registry = BackendRegistry()
        register_default_backends(registry, config)
        registry.create(LayerKind.ASR, "faster_whisper")
        patched_backend_setup["FasterWhisperAsrBackend"].assert_called_with(
            model_size="small", device="auto", compute_type="auto"
        )


class TestNllb200ConfigIntegration:
    """NLLB-200 の device が config から読まれることを検証。"""

    def test_default_uses_auto(self, patched_backend_setup) -> None:
        from voice_translator.common.backend_setup import register_default_backends

        registry = BackendRegistry()
        register_default_backends(registry)
        registry.create(LayerKind.TRANSLATOR, "nllb200")
        patched_backend_setup["Nllb200TranslatorBackend"].assert_called_with(
            model_name="facebook/nllb-200-distilled-600M", device="auto",
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
            model_name="facebook/nllb-200-distilled-600M", device="cuda",
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
            model_name="facebook/nllb-200-distilled-600M", device="auto",
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


# ============================================================
# Phase F1: 新 VAD backend 登録の検証
# ============================================================
class TestWebRtcVadRegistration:
    """WebRTC VAD backend が登録され、config からパラメータが渡ることを検証。"""

    def test_default_params(self, patched_backend_setup) -> None:
        from voice_translator.common.backend_setup import register_default_backends

        registry = BackendRegistry()
        register_default_backends(registry)
        registry.create(LayerKind.VAD, "webrtcvad")
        patched_backend_setup["WebRtcVadBackend"].assert_called_with(
            aggressiveness=2,
            frame_ms=30,
            min_speech_ms=60,
            min_silence_ms=500,
            speech_pad_ms=100,
            max_speech_sec=8.0,
        )

    def test_params_from_config(self, patched_backend_setup, tmp_path) -> None:
        from voice_translator.common.backend_setup import register_default_backends
        from voice_translator.common.config_store import ConfigStore

        config = ConfigStore(tmp_path / "cfg.yaml")
        config.set("backends_config", "webrtcvad", "aggressiveness", 3)
        config.set("backends_config", "webrtcvad", "frame_ms", 20)
        config.set("backends_config", "webrtcvad", "max_speech_sec", 5.0)

        registry = BackendRegistry()
        register_default_backends(registry, config)
        registry.create(LayerKind.VAD, "webrtcvad")
        patched_backend_setup["WebRtcVadBackend"].assert_called_with(
            aggressiveness=3,
            frame_ms=20,
            min_speech_ms=60,
            min_silence_ms=500,
            speech_pad_ms=100,
            max_speech_sec=5.0,
        )

    def test_capabilities_hint_registered(self, patched_backend_setup) -> None:
        from voice_translator.common.backend_setup import register_default_backends

        registry = BackendRegistry()
        register_default_backends(registry)
        hint = registry.get_capability_hint(LayerKind.VAD, "webrtcvad")
        # webrtcvad は無認証
        assert hint is not None
        assert hint.requires_credentials is False

    def test_backend_class_registered(self, patched_backend_setup) -> None:
        from voice_translator.common.backend_setup import register_default_backends

        registry = BackendRegistry()
        register_default_backends(registry)
        cls = registry.get_backend_class(LayerKind.VAD, "webrtcvad")
        assert cls is patched_backend_setup["WebRtcVadBackend"]


class TestPyannoteVadRegistration:
    """pyannote.audio backend が登録され、HF token を CredentialsStore から取ることを検証。"""

    def test_default_params(
        self, patched_backend_setup, tmp_path, monkeypatch
    ) -> None:
        from voice_translator.common.backend_setup import register_default_backends
        from voice_translator.common.config_store import ConfigStore

        # `_get_credential` は CredentialsStore(use_local_file=True) を生成し、
        # file_path 既定値が **相対パス** `local.secrets` のため cwd に依存する。
        # プロジェクト root の実 `local.secrets`(ユーザの本物の HF token を持つ)を
        # 読まないよう、cwd を tmp_path に切り替えて隔離する。
        monkeypatch.chdir(tmp_path)
        config = ConfigStore(tmp_path / "cfg.yaml")
        config.set("credentials", "use_local_file", True)

        registry = BackendRegistry()
        register_default_backends(registry, config)
        registry.create(LayerKind.VAD, "pyannote")
        call_kwargs = patched_backend_setup["PyannoteVadBackend"].call_args.kwargs
        # HF token は未保存なので None
        assert call_kwargs["hf_token"] is None
        # 既定モデルは segmentation-3.0(pyannote 4.x の標準パターン)。
        # voice-activity-detection pipeline は HF 上の config が古い @revision 構文を含み
        # 4.x で動かないため使わない。
        assert call_kwargs["model_id"] == "pyannote/segmentation-3.0"
        assert call_kwargs["device"] == "auto"

    def test_capabilities_requires_credentials(self, patched_backend_setup) -> None:
        from voice_translator.common.backend_setup import register_default_backends

        registry = BackendRegistry()
        register_default_backends(registry)
        hint = registry.get_capability_hint(LayerKind.VAD, "pyannote")
        assert hint is not None
        assert hint.requires_credentials is True
        assert "pyannote" in (hint.service_name or "").lower()


class TestPvcobraRegistration:
    """Picovoice Cobra backend が登録され、access_key を CredentialsStore から取ることを検証。"""

    def test_default_params(
        self, patched_backend_setup, tmp_path, monkeypatch
    ) -> None:
        from voice_translator.common.backend_setup import register_default_backends
        from voice_translator.common.config_store import ConfigStore

        # 実 local.secrets を読まないよう cwd 隔離(pyannote 側と同じ)
        monkeypatch.chdir(tmp_path)
        config = ConfigStore(tmp_path / "cfg.yaml")
        config.set("credentials", "use_local_file", True)

        registry = BackendRegistry()
        register_default_backends(registry, config)
        registry.create(LayerKind.VAD, "pvcobra")
        call_kwargs = patched_backend_setup["PvcobraVadBackend"].call_args.kwargs
        assert call_kwargs["access_key"] is None
        assert call_kwargs["threshold"] == 0.5
        assert call_kwargs["max_speech_sec"] == 8.0

    def test_capabilities_requires_credentials(self, patched_backend_setup) -> None:
        from voice_translator.common.backend_setup import register_default_backends

        registry = BackendRegistry()
        register_default_backends(registry)
        hint = registry.get_capability_hint(LayerKind.VAD, "pvcobra")
        assert hint is not None
        assert hint.requires_credentials is True
        assert hint.is_cloud is False  # ローカル動作

    def test_threshold_read_from_config(
        self, patched_backend_setup, tmp_path, monkeypatch
    ) -> None:
        from voice_translator.common.backend_setup import register_default_backends
        from voice_translator.common.config_store import ConfigStore

        monkeypatch.chdir(tmp_path)
        config = ConfigStore(tmp_path / "cfg.yaml")
        config.set("credentials", "use_local_file", True)
        config.set("backends_config", "pvcobra", "threshold", 0.7)

        registry = BackendRegistry()
        register_default_backends(registry, config)
        registry.create(LayerKind.VAD, "pvcobra")
        call_kwargs = patched_backend_setup["PvcobraVadBackend"].call_args.kwargs
        assert call_kwargs["threshold"] == 0.7
