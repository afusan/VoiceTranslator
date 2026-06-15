"""language_filter(検索絞り込み純関数)の small テスト。"""

from __future__ import annotations

from voice_translator.gui.logic.language_filter import filter_languages


class TestFilterLanguages:
    def test_empty_query_returns_all(self) -> None:
        codes = ["eng", "jpn", "swh"]
        assert filter_languages(codes, "") == codes
        assert filter_languages(codes, "   ") == codes

    def test_matches_by_code_prefix(self) -> None:
        # "fij"(Fijian)はコードも名前も "e" を含まない
        assert filter_languages(["eng", "ewe", "fij"], "e") == ["eng", "ewe"]

    def test_matches_by_name(self) -> None:
        # "swh" は Swahili。コードでなく名前で当たる。
        assert filter_languages(["swh", "swe", "yor"], "swahili") == ["swh"]

    def test_case_insensitive(self) -> None:
        assert filter_languages(["eng", "fra"], "ENG") == ["eng"]
        assert filter_languages(["eng", "fra"], "French") == ["fra"]

    def test_code_prefix_ranked_before_name_prefix(self) -> None:
        # query "sw": swe/swh はコード前方一致、Swahili/Swedish の名前前方一致もあるが
        # コード前方一致が先。入力順は維持。
        assert filter_languages(["swe", "swh"], "sw") == ["swe", "swh"]

    def test_substring_match_after_prefixes(self) -> None:
        # "ami" は "Central Aymara"? いや、部分一致の順序確認用に code 部分一致を使う。
        # "or" は yor(Yoruba)の code 部分一致(前方でない)。
        out = filter_languages(["ory", "yor"], "or")
        # ory はコード前方一致、yor はコード部分一致 → ory が先
        assert out == ["ory", "yor"]

    def test_no_match_returns_empty(self) -> None:
        assert filter_languages(["eng", "jpn"], "zzzzz") == []
