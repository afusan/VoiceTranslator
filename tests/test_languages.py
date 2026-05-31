"""言語コード共通テーブル(`common/languages.py`)の単体テスト。"""

from __future__ import annotations

import pytest

from voice_translator.common.languages import (
    LANGUAGE_NAMES,
    format_language,
    language_name,
    parse_language,
)


class TestLanguageName:
    def test_known_code_returns_english_name(self) -> None:
        assert language_name("en") == "English"
        assert language_name("ja") == "Japanese"
        assert language_name("zh") == "Chinese"

    def test_auto_returns_auto_detect(self) -> None:
        assert language_name("auto") == "Auto-detect"

    def test_unknown_code_returns_code_unchanged(self) -> None:
        assert language_name("xx") == "xx"


class TestFormatLanguage:
    def test_known_code_returns_code_paren_name(self) -> None:
        assert format_language("en") == "en (English)"
        assert format_language("ja") == "ja (Japanese)"

    def test_auto_format(self) -> None:
        assert format_language("auto") == "auto (Auto-detect)"

    def test_unknown_code_returns_code_only(self) -> None:
        # 未知は "xx (xx)" にならず、単に "xx"
        assert format_language("xx") == "xx"


class TestParseLanguage:
    def test_parse_formatted_label(self) -> None:
        assert parse_language("en (English)") == "en"
        assert parse_language("ja (Japanese)") == "ja"

    def test_parse_bare_code(self) -> None:
        assert parse_language("en") == "en"
        assert parse_language("auto") == "auto"


class TestTableSanity:
    def test_no_duplicate_names(self) -> None:
        names = list(LANGUAGE_NAMES.values())
        assert len(names) == len(set(names)), "言語名に重複あり"

    def test_codes_lowercase(self) -> None:
        for code in LANGUAGE_NAMES:
            assert code == code.lower(), f"コード {code} は小文字でない"

    @pytest.mark.parametrize("required", ["en", "ja", "zh", "ko", "auto"])
    def test_required_codes_present(self, required: str) -> None:
        assert required in LANGUAGE_NAMES
