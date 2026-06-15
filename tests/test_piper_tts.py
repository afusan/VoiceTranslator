"""PiperTtsBackend の単体テスト(small)。

piper / huggingface_hub は重い実依存のため、`sys.modules` 差し替えでモック化する。
voice モデル DL の挙動 / synthesize の PCM 形式 / 空テキスト処理を中心に検証。
"""

from __future__ import annotations

import sys
from unittest.mock import MagicMock

import numpy as np
import pytest

from voice_translator.common.errors import FatalError, SkipError


@pytest.fixture()
def fake_piper(monkeypatch):
    """piper.PiperVoice と huggingface_hub.hf_hub_download をモック差し替え。"""
    fake_piper_mod = MagicMock()
    fake_voice = MagicMock(name="PiperVoice")
    fake_voice.config.sample_rate = 22050
    # synthesize_stream_raw は int16 PCM の bytes をジェネレータで返す
    fake_voice.synthesize_stream_raw = MagicMock(
        return_value=iter([np.zeros(512, dtype=np.int16).tobytes()])
    )
    fake_piper_mod.PiperVoice = MagicMock()
    fake_piper_mod.PiperVoice.load = MagicMock(return_value=fake_voice)
    monkeypatch.setitem(sys.modules, "piper", fake_piper_mod)

    fake_hub = MagicMock()
    fake_hub.hf_hub_download = MagicMock(return_value="/tmp/voice.onnx")
    monkeypatch.setitem(sys.modules, "huggingface_hub", fake_hub)
    return fake_piper_mod, fake_voice, fake_hub


class TestSupportedOutputLanguages:
    def test_returns_major_piper_languages(self) -> None:
        from voice_translator.tts.piper_backend import PiperTtsBackend
        langs = PiperTtsBackend.supported_output_languages()
        # 申告は正準(ISO 639-3)
        assert "eng" in langs
        assert "deu" in langs
        assert "fra" in langs
        # 日本語は piper-voices 標準配布なし → 含まれない
        assert "jpn" not in langs


class TestInitialization:
    def test_loads_default_voice(self, fake_piper) -> None:
        from voice_translator.tts.piper_backend import PiperTtsBackend
        backend = PiperTtsBackend()
        # voice ロードまで完走すれば __init__ は成功
        _, _, fake_hub = fake_piper
        assert fake_hub.hf_hub_download.call_count >= 2  # .onnx + .onnx.json

    def test_fails_when_piper_missing(self, monkeypatch) -> None:
        """piper が import 不能なら FatalError("uv sync --extra tts-piper")。"""
        monkeypatch.setitem(sys.modules, "piper", None)
        from voice_translator.tts import piper_backend
        import importlib
        importlib.reload(piper_backend)
        with pytest.raises(FatalError, match="piper-tts"):
            piper_backend.PiperTtsBackend()

    def test_invalid_voice_name_format(self, fake_piper) -> None:
        from voice_translator.tts.piper_backend import PiperTtsBackend
        with pytest.raises(FatalError, match="voice_name の形式"):
            PiperTtsBackend(voice_name="bad")  # 区切り無し

    def test_hf_download_failure_wraps_fatal(self, fake_piper, monkeypatch) -> None:
        """HF DL が落ちたら FatalError でラップ。"""
        _, _, fake_hub = fake_piper
        fake_hub.hf_hub_download.side_effect = OSError("network down")
        from voice_translator.tts.piper_backend import PiperTtsBackend
        with pytest.raises(FatalError, match="Piper voice"):
            PiperTtsBackend()


class TestSynthesize:
    def test_empty_text_raises_skip(self, fake_piper) -> None:
        from voice_translator.tts.piper_backend import PiperTtsBackend
        backend = PiperTtsBackend()
        with pytest.raises(SkipError):
            backend.synthesize("", "en")

    def test_returns_float32_pcm(self, fake_piper) -> None:
        from voice_translator.tts.piper_backend import PiperTtsBackend
        backend = PiperTtsBackend()
        pcm, sr = backend.synthesize("hello", "en")
        assert isinstance(pcm, np.ndarray)
        assert pcm.dtype == np.float32
        assert sr == 22050  # voice.config.sample_rate

    def test_empty_audio_raises_skip(self, fake_piper) -> None:
        """voice が空の音声を返したら SkipError(再生する意味がない)。"""
        _, fake_voice, _ = fake_piper
        fake_voice.synthesize_stream_raw = MagicMock(return_value=iter([]))
        from voice_translator.tts.piper_backend import PiperTtsBackend
        backend = PiperTtsBackend()
        with pytest.raises(SkipError):
            backend.synthesize("hello", "en")

    def test_runtime_failure_wrapped_fatal(self, fake_piper) -> None:
        _, fake_voice, _ = fake_piper
        fake_voice.synthesize_stream_raw = MagicMock(side_effect=RuntimeError("decoder crash"))
        from voice_translator.tts.piper_backend import PiperTtsBackend
        backend = PiperTtsBackend()
        with pytest.raises(FatalError, match="Piper 合成失敗"):
            backend.synthesize("hello", "en")
