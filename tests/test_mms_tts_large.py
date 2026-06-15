"""MmsTtsBackend の large テスト(実モデル DL + 合成)。

実行条件:
- transformers / torch が導入済み(base 依存なので通常は満たす)
- ネットワーク接続あり(Hugging Face から `facebook/mms-tts-eng` を DL、~100MB 級)

`@pytest.mark.large` 付き = 既定の `pytest` 実行では skip される。
手動確認: `py -m uv run pytest -m large tests/test_mms_tts_large.py`

CLAUDE.md「実ロード large テスト」方針に対応(small テストはモック前提で依存の
バージョン乖離や checkpoint 404 を検出できないため、実物の DL + 合成まで確認する)。
"""

from __future__ import annotations

import importlib.util

import numpy as np
import pytest


_MMS_AVAILABLE = importlib.util.find_spec("transformers") is not None


@pytest.mark.large
@pytest.mark.skipif(
    not _MMS_AVAILABLE,
    reason="transformers 未インストール(base 依存。`uv sync` で導入)",
)
class TestMmsRealVoice:
    def test_load_english_and_synthesize(self) -> None:
        """英語(eng)を実 DL → synthesize で float32 PCM が返る。"""
        from voice_translator.tts.mms_backend import MmsTtsBackend

        backend = MmsTtsBackend(device="cpu")
        pcm, sr = backend.synthesize("Hello world.", "eng")  # 正準 639-3
        assert isinstance(pcm, np.ndarray)
        assert pcm.dtype == np.float32
        assert pcm.size > 0
        assert sr == 16000  # MMS は 16kHz

    def test_low_resource_language_loads(self) -> None:
        """低資源言語(スワヒリ swh)も実 DL→合成できる(拡張集合の検証)。"""
        from voice_translator.tts.mms_backend import MmsTtsBackend

        backend = MmsTtsBackend(device="cpu")
        pcm, sr = backend.synthesize("Habari za asubuhi.", "swh")
        assert pcm.size > 0
        assert sr == 16000

    def test_prefetch_then_cached(self) -> None:
        """prefetch 済み言語は再ロードなしで synthesize できる。"""
        from voice_translator.tts.mms_backend import MmsTtsBackend

        backend = MmsTtsBackend(device="cpu")
        backend.prefetch_language("eng")
        assert "eng" in backend._cache
        pcm, sr = backend.synthesize("Test.", "eng")
        assert pcm.size > 0
