"""PiperTtsBackend の large テスト(実 voice モデル DL + 合成)。

実行条件:
- `uv sync --extra tts-piper` 済み(piper-tts / onnxruntime / huggingface_hub)
- ネットワーク接続あり(Hugging Face から voice モデル DL)

`@pytest.mark.large` 付き = 既定の `pytest` 実行では skip される。
手動確認: `py -m uv run pytest -m large tests/test_piper_tts_large.py`
"""

from __future__ import annotations

import importlib.util

import numpy as np
import pytest


_PIPER_AVAILABLE = (
    importlib.util.find_spec("piper") is not None
    and importlib.util.find_spec("huggingface_hub") is not None
)


@pytest.mark.large
@pytest.mark.skipif(
    not _PIPER_AVAILABLE,
    reason="piper-tts 未インストール(`uv sync --extra tts-piper` でインストール)",
)
class TestPiperRealVoice:
    def test_load_default_voice_and_synthesize(self) -> None:
        """既定 voice (en_US-amy-low) を実 DL → synthesize で float32 PCM が返る。"""
        from voice_translator.tts.piper_backend import PiperTtsBackend

        backend = PiperTtsBackend()  # 既定 voice
        pcm, sr = backend.synthesize("Hello world.", "en")
        assert isinstance(pcm, np.ndarray)
        assert pcm.dtype == np.float32
        assert pcm.size > 0
        # voice の native sample rate は voice.config に依存(amy-low は 16000)
        assert sr > 0
