"""locale_switch(言語切替の判断ロジック)の単体テスト(small・tk/モック不要)。"""

from __future__ import annotations

from voice_translator.gui.logic.locale_switch import (
    can_switch_locale,
    resolve_initial_locale,
    resolve_target_locale,
)


class TestResolveInitialLocale:
    def test_valid_saved_value(self) -> None:
        assert resolve_initial_locale("en", ["ja", "en"]) == "en"

    def test_unknown_value_falls_back_to_ja(self) -> None:
        assert resolve_initial_locale("zz", ["ja", "en"]) == "ja"

    def test_empty_available_falls_back(self) -> None:
        assert resolve_initial_locale("en", []) == "ja"


class TestCanSwitchLocale:
    def test_stopped_allows_switch(self) -> None:
        assert can_switch_locale(is_running=False) is True

    def test_running_blocks_switch(self) -> None:
        assert can_switch_locale(is_running=True) is False


class TestResolveTargetLocale:
    _MAP = {"日本語": "ja", "English": "en"}

    def test_switch_to_different(self) -> None:
        assert resolve_target_locale("English", self._MAP, "ja") == "en"

    def test_same_locale_is_noop(self) -> None:
        assert resolve_target_locale("English", self._MAP, "en") is None

    def test_unknown_display_is_noop(self) -> None:
        assert resolve_target_locale("Klingon", self._MAP, "ja") is None
