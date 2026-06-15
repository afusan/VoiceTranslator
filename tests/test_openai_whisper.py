"""OpenAiWhisperAsrBackend の単体テスト。whisper モジュールを完全モック化。"""

from __future__ import annotations

import sys
from unittest.mock import MagicMock

import numpy as np
import pytest

from voice_translator.common.errors import FatalError, SkipError


@pytest.fixture()
def fake_whisper(monkeypatch):
    """whisper.load_model をモックに差し替える。"""
    fake_module = MagicMock()
    fake_model = MagicMock(name="whisper_model")
    fake_model.transcribe = MagicMock(
        return_value={"text": "  hello world  ", "language": "en"}
    )
    fake_module.load_model = MagicMock(return_value=fake_model)
    monkeypatch.setitem(sys.modules, "whisper", fake_module)
    return fake_module, fake_model


@pytest.fixture()
def patch_cache_to_loaded(monkeypatch):
    """キャッシュ判定を「ロード済み」にして DOWNLOADING 状態を回避。"""
    monkeypatch.setattr(
        "voice_translator.asr.openai_whisper_backend._check_openai_whisper_cache",
        lambda model_size: __import__(
            "voice_translator.common.types", fromlist=["ModelStatus"]
        ).ModelStatus.LOADED,
    )


class TestInitialization:
    def test_calls_load_model_with_size_and_device(
        self, fake_whisper, patch_cache_to_loaded
    ) -> None:
        fake_module, _ = fake_whisper
        from voice_translator.asr.openai_whisper_backend import (
            OpenAiWhisperAsrBackend,
        )

        OpenAiWhisperAsrBackend(model_size="tiny", device="cpu")
        fake_module.load_model.assert_called_once_with("tiny", device="cpu")

    def test_init_failure_raises_fatal(self, monkeypatch, patch_cache_to_loaded) -> None:
        fake_module = MagicMock()
        fake_module.load_model = MagicMock(side_effect=RuntimeError("boom"))
        monkeypatch.setitem(sys.modules, "whisper", fake_module)

        from voice_translator.asr.openai_whisper_backend import (
            OpenAiWhisperAsrBackend,
        )

        with pytest.raises(FatalError):
            OpenAiWhisperAsrBackend(model_size="tiny", device="cpu")

    def test_gpu_init_failure_falls_back_to_cpu(
        self, monkeypatch, patch_cache_to_loaded
    ) -> None:
        fake_module = MagicMock()
        fake_model_cpu = MagicMock(name="cpu_model")
        call_log: list[dict] = []

        def loader(*args, **kwargs):
            call_log.append(kwargs)
            if kwargs.get("device") == "cuda":
                raise RuntimeError("CUDA not available")
            return fake_model_cpu

        fake_module.load_model = MagicMock(side_effect=loader)
        monkeypatch.setitem(sys.modules, "whisper", fake_module)

        from voice_translator.asr.openai_whisper_backend import (
            OpenAiWhisperAsrBackend,
        )

        backend = OpenAiWhisperAsrBackend(model_size="tiny", device="cuda")
        assert backend.device == "cpu"
        assert len(call_log) == 2  # cuda 失敗 → cpu 成功
        assert call_log[0]["device"] == "cuda"
        assert call_log[1]["device"] == "cpu"


class TestTranscribe:
    def test_returns_text_and_detected_language(
        self, fake_whisper, patch_cache_to_loaded
    ) -> None:
        _, fake_model = fake_whisper
        from voice_translator.asr.openai_whisper_backend import (
            OpenAiWhisperAsrBackend,
        )

        backend = OpenAiWhisperAsrBackend(model_size="tiny", device="cpu")
        text, lang = backend.transcribe(np.zeros(160, dtype=np.float32), src_lang_hint="auto")

        assert text == "hello world"
        assert lang == "eng"  # 検出言語(639-1)を正準 639-3 へ持ち上げて返す
        # language 引数は auto なら省略される
        kwargs = fake_model.transcribe.call_args.kwargs
        assert "language" not in kwargs
        assert kwargs["task"] == "transcribe"

    def test_passes_language_when_hint_given(
        self, fake_whisper, patch_cache_to_loaded
    ) -> None:
        _, fake_model = fake_whisper
        from voice_translator.asr.openai_whisper_backend import (
            OpenAiWhisperAsrBackend,
        )

        backend = OpenAiWhisperAsrBackend(model_size="tiny", device="cpu")
        text, lang = backend.transcribe(np.zeros(160, dtype=np.float32), src_lang_hint="jpn")

        assert lang == "jpn"  # hint(正準 639-3)が優先される
        kwargs = fake_model.transcribe.call_args.kwargs
        assert kwargs["language"] == "ja"  # Whisper には 639-1 で渡す

    def test_empty_pcm_raises_skip(
        self, fake_whisper, patch_cache_to_loaded
    ) -> None:
        from voice_translator.asr.openai_whisper_backend import (
            OpenAiWhisperAsrBackend,
        )

        backend = OpenAiWhisperAsrBackend(model_size="tiny", device="cpu")
        with pytest.raises(SkipError):
            backend.transcribe(np.zeros(0, dtype=np.float32))

    def test_transcribe_failure_raises_fatal(
        self, fake_whisper, patch_cache_to_loaded
    ) -> None:
        _, fake_model = fake_whisper
        fake_model.transcribe = MagicMock(side_effect=RuntimeError("boom"))

        from voice_translator.asr.openai_whisper_backend import (
            OpenAiWhisperAsrBackend,
        )

        backend = OpenAiWhisperAsrBackend(model_size="tiny", device="cpu")
        with pytest.raises(FatalError):
            backend.transcribe(np.zeros(160, dtype=np.float32))


class TestSupportedInputLanguages:
    def test_returns_whisper_99_languages(self) -> None:
        from voice_translator.asr.openai_whisper_backend import (
            OpenAiWhisperAsrBackend,
        )

        langs = OpenAiWhisperAsrBackend.supported_input_languages()
        assert "eng" in langs  # 正準 639-3 で申告
        assert "jpn" in langs
        assert "auto" not in langs
        assert len(langs) >= 90  # 99 言語のはず

    def test_supports_auto_detect(self) -> None:
        from voice_translator.asr.openai_whisper_backend import (
            OpenAiWhisperAsrBackend,
        )

        assert OpenAiWhisperAsrBackend.supports_auto_detect() is True

    def test_no_load_required(self, monkeypatch) -> None:
        """whisper モジュール未インストール環境でもクラスメソッドは呼べる。"""
        monkeypatch.delitem(sys.modules, "whisper", raising=False)
        from voice_translator.asr.openai_whisper_backend import (
            OpenAiWhisperAsrBackend,
        )

        langs = OpenAiWhisperAsrBackend.supported_input_languages()
        assert len(langs) >= 90


class TestCapabilities:
    def test_capabilities_local_no_credentials(
        self, fake_whisper, patch_cache_to_loaded
    ) -> None:
        from voice_translator.asr.openai_whisper_backend import (
            OpenAiWhisperAsrBackend,
        )

        backend = OpenAiWhisperAsrBackend(model_size="tiny", device="cpu")
        caps = backend.capabilities()
        assert caps.is_cloud is False
        assert caps.requires_credentials is False


class TestRecommendedModels:
    def test_lists_tiny_to_large_v3(self) -> None:
        from voice_translator.asr.openai_whisper_backend import (
            OpenAiWhisperAsrBackend,
        )

        names = [m.name for m in OpenAiWhisperAsrBackend.recommended_models()]
        assert names == ["tiny", "base", "small", "medium", "large-v3"]
