"""MmsTtsBackend の small テスト(モック中心、実モデル DL なし)。

実モデルのロード/合成は large テスト(tests/test_mms_tts_large.py)で検証する。
ここでは「言語コード申告」「LRU キャッシュ/退避」「未対応・空入力の縮退」を、
transformers/torch を sys.modules でモックして I/O 無しで確認する。
"""

from __future__ import annotations

import sys
from unittest.mock import MagicMock

import pytest

from voice_translator.common.errors import SkipError
from voice_translator.common.languages import LANGUAGE_NAMES


@pytest.fixture
def mocked_heavy_deps(monkeypatch):
    """torch / transformers を import 可能なモックに差し替える(実ロード回避)。"""
    torch_mock = MagicMock(name="torch")
    torch_mock.cuda.is_available.return_value = False
    monkeypatch.setitem(sys.modules, "torch", torch_mock)
    transformers_mock = MagicMock(name="transformers")
    monkeypatch.setitem(sys.modules, "transformers", transformers_mock)
    return torch_mock, transformers_mock


def _make_backend(mocked_heavy_deps, **kwargs):
    from voice_translator.tts.mms_backend import MmsTtsBackend

    return MmsTtsBackend(**kwargs)


class TestSupportedLanguages:
    def test_returns_sorted_iso639_1_subset(self) -> None:
        from voice_translator.tts.mms_backend import MmsTtsBackend

        langs = MmsTtsBackend.supported_output_languages()
        assert langs == sorted(langs)  # 安定ソート
        assert langs, "初期集合が空になっている"

    def test_all_codes_are_displayable(self) -> None:
        """申告言語はすべて言語テーブルで表示できる(format で素のコードに化けない)。"""
        from voice_translator.tts.mms_backend import MmsTtsBackend

        for code in MmsTtsBackend.supported_output_languages():
            assert code in LANGUAGE_NAMES

    def test_classmethod_does_not_need_heavy_deps(self, monkeypatch) -> None:
        """未ロードでも答えられる(transformers を import しない)。"""
        # transformers を import 不可にしても classmethod は動く
        monkeypatch.setitem(sys.modules, "transformers", None)
        from voice_translator.tts.mms_backend import MmsTtsBackend

        assert "eng" in MmsTtsBackend.supported_output_languages()


class TestLazyCacheLru:
    def test_caches_and_reuses(self, mocked_heavy_deps, monkeypatch) -> None:
        from voice_translator.tts import mms_backend as mod

        backend = _make_backend(mocked_heavy_deps, max_cached_languages=2)

        calls: list[str] = []

        def fake_load(lang: str) -> mod._LoadedVoice:
            calls.append(lang)
            return mod._LoadedVoice(
                model=MagicMock(), tokenizer=MagicMock(), samplerate=16000,
                is_uroman=False,
            )

        monkeypatch.setattr(backend, "_load_voice", fake_load)

        v1 = backend._ensure_language("en")
        v1b = backend._ensure_language("en")
        assert v1 is v1b
        assert calls == ["en"]  # 2 回目は再ロードしない

    def test_lru_evicts_oldest(self, mocked_heavy_deps, monkeypatch) -> None:
        from voice_translator.tts import mms_backend as mod

        backend = _make_backend(mocked_heavy_deps, max_cached_languages=2)

        def fake_load(lang: str) -> mod._LoadedVoice:
            return mod._LoadedVoice(
                model=MagicMock(), tokenizer=MagicMock(), samplerate=16000,
                is_uroman=False,
            )

        monkeypatch.setattr(backend, "_load_voice", fake_load)

        backend._ensure_language("en")
        backend._ensure_language("fr")
        backend._ensure_language("de")  # en が押し出される

        assert set(backend._cache.keys()) == {"fr", "de"}

    def test_prefetch_unsupported_is_noop(self, mocked_heavy_deps, monkeypatch) -> None:
        backend = _make_backend(mocked_heavy_deps)
        called = MagicMock()
        monkeypatch.setattr(backend, "_ensure_language", called)

        backend.prefetch_language("xx")  # 未対応
        called.assert_not_called()


class TestSynthesizeGuards:
    def test_empty_text_skips(self, mocked_heavy_deps) -> None:
        backend = _make_backend(mocked_heavy_deps)
        with pytest.raises(SkipError):
            backend.synthesize("", "en")

    def test_unsupported_language_skips(self, mocked_heavy_deps) -> None:
        backend = _make_backend(mocked_heavy_deps)
        with pytest.raises(SkipError):
            backend.synthesize("hello", "xx")
