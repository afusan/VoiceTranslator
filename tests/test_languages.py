"""言語コード共通テーブル(`common/languages.py`)の単体テスト。

内部標準 = ISO 639-3。legacy 639-1 との境界変換(`to_canonical` /
`iso1_to_iso3` / `iso3_to_iso1`)も検証する。
"""

from __future__ import annotations

import pytest

from voice_translator.common.languages import (
    LANGUAGE_NAMES,
    format_language,
    iso1_to_iso3,
    iso3_to_iso1,
    language_name,
    parse_language,
    to_canonical,
)


class TestLanguageName:
    def test_known_code_returns_english_name(self) -> None:
        assert language_name("eng") == "English"
        assert language_name("jpn") == "Japanese"
        assert language_name("zho") == "Chinese"

    def test_auto_returns_auto_detect(self) -> None:
        assert language_name("auto") == "Auto-detect"

    def test_unknown_code_returns_code_unchanged(self) -> None:
        assert language_name("xyz") == "xyz"


class TestFormatLanguage:
    def test_known_code_returns_code_paren_name(self) -> None:
        assert format_language("eng") == "eng (English)"
        assert format_language("jpn") == "jpn (Japanese)"

    def test_auto_format(self) -> None:
        assert format_language("auto") == "auto (Auto-detect)"

    def test_unknown_code_returns_code_only(self) -> None:
        # 未知は "xyz (xyz)" にならず、単に "xyz"
        assert format_language("xyz") == "xyz"


class TestParseLanguage:
    def test_parse_formatted_label(self) -> None:
        assert parse_language("eng (English)") == "eng"
        assert parse_language("jpn (Japanese)") == "jpn"

    def test_parse_bare_code(self) -> None:
        assert parse_language("eng") == "eng"
        assert parse_language("auto") == "auto"


class TestBoundaryConversion:
    def test_iso1_to_iso3(self) -> None:
        assert iso1_to_iso3("ja") == "jpn"
        assert iso1_to_iso3("en") == "eng"
        assert iso1_to_iso3("auto") == "auto"

    def test_iso1_to_iso3_passthrough_unknown(self) -> None:
        # 既に 639-3 / 未知は素通し(haw は 639-1 を持たない)
        assert iso1_to_iso3("haw") == "haw"
        assert iso1_to_iso3("zzz") == "zzz"

    def test_iso3_to_iso1_roundtrip(self) -> None:
        assert iso3_to_iso1("jpn") == "ja"
        assert iso3_to_iso1("eng") == "en"
        assert iso3_to_iso1("auto") == "auto"

    def test_iso3_to_iso1_passthrough_when_no_inverse(self) -> None:
        assert iso3_to_iso1("zzz") == "zzz"

    def test_to_canonical_normalizes_legacy_639_1(self) -> None:
        assert to_canonical("ja") == "jpn"
        assert to_canonical("en") == "eng"

    def test_to_canonical_keeps_canonical_and_auto(self) -> None:
        assert to_canonical("jpn") == "jpn"
        assert to_canonical("auto") == "auto"

    def test_to_canonical_passthrough_unknown(self) -> None:
        assert to_canonical("zzz") == "zzz"


class TestTableSanity:
    def test_no_duplicate_names(self) -> None:
        names = list(LANGUAGE_NAMES.values())
        assert len(names) == len(set(names)), "言語名に重複あり"

    def test_codes_lowercase(self) -> None:
        for code in LANGUAGE_NAMES:
            assert code == code.lower(), f"コード {code} は小文字でない"

    @pytest.mark.parametrize("required", ["eng", "jpn", "zho", "kor", "auto"])
    def test_required_codes_present(self, required: str) -> None:
        assert required in LANGUAGE_NAMES
