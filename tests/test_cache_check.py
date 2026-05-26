"""cache_check モジュールの単体テスト。

huggingface_hub.try_to_load_from_cache をモックして挙動を検証する。
"""

from __future__ import annotations

import sys
from unittest.mock import MagicMock

import pytest

from voice_translator.common import cache_check
from voice_translator.common.types import ModelStatus


@pytest.fixture()
def fake_hub(monkeypatch):
    fake_module = MagicMock()
    fake_module.try_to_load_from_cache = MagicMock(return_value=None)
    monkeypatch.setitem(sys.modules, "huggingface_hub", fake_module)
    return fake_module


class TestFasterWhisperCheck:
    def test_cache_present_returns_loaded(self, fake_hub) -> None:
        fake_hub.try_to_load_from_cache.return_value = "/some/path"
        assert cache_check.check_faster_whisper("small") == ModelStatus.LOADED

    def test_cache_missing_returns_not_downloaded(self, fake_hub) -> None:
        fake_hub.try_to_load_from_cache.return_value = None
        assert cache_check.check_faster_whisper("small") == ModelStatus.NOT_DOWNLOADED

    def test_repo_id_uses_size(self, fake_hub) -> None:
        cache_check.check_faster_whisper("medium")
        args, _ = fake_hub.try_to_load_from_cache.call_args
        assert "medium" in args[0]

    def test_exception_returns_not_downloaded(self, fake_hub) -> None:
        fake_hub.try_to_load_from_cache.side_effect = RuntimeError("oops")
        assert cache_check.check_faster_whisper() == ModelStatus.NOT_DOWNLOADED


class TestNllbCheck:
    def test_cache_present(self, fake_hub) -> None:
        fake_hub.try_to_load_from_cache.return_value = "/p"
        assert cache_check.check_nllb200() == ModelStatus.LOADED

    def test_cache_missing(self, fake_hub) -> None:
        fake_hub.try_to_load_from_cache.return_value = None
        assert cache_check.check_nllb200() == ModelStatus.NOT_DOWNLOADED


class TestAlwaysLoaded:
    def test_silero_always_loaded(self) -> None:
        assert cache_check.check_silero() == ModelStatus.LOADED

    def test_sapi_always_loaded(self) -> None:
        assert cache_check.check_sapi() == ModelStatus.LOADED

    def test_soundcard_always_loaded(self) -> None:
        assert cache_check.check_soundcard() == ModelStatus.LOADED
