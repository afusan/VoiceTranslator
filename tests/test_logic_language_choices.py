"""gui/logic/language_choices.py の単体テスト(純関数、GUI/モック不要)。

旧 test_settings_panel_lang.py の shim 方式テストから判断ロジックのシナリオを移植
(P1 / refactor-ui-3move)。widget への配線は test_settings_panel_lang.py 側に残る
wiring smoke で検証する。
"""

from __future__ import annotations

from voice_translator.gui.logic.language_choices import (
    compute_src_selection,
    compute_tgt_selection,
    format_src_fallback_message,
    format_tgt_fallback_message,
    format_tts_warning_message,
    tts_warning_needed,
)

# 旧 settings_panel._TGT_LANG_CHOICES / _FALLBACK_INPUT_LANGS 相当(テスト用の代表値)
_POOL = [
    "en", "ja", "zh", "ko", "es", "fr", "de", "it", "pt", "ru", "ar",
    "hi", "th", "vi", "id", "tr",
]


class TestComputeSrcSelection:
    def test_uses_backend_supported_languages_with_auto_first(self) -> None:
        sel = compute_src_selection(
            ["en", "ja", "fr"], supports_auto=True, current="en", fallback_pool=_POOL,
        )
        assert sel.codes[0] == "auto"
        assert sel.codes[1:] == ["en", "fr", "ja"]  # sorted
        assert sel.selected == "en"
        assert sel.fallback_from is None

    def test_no_auto_when_backend_not_supports_auto(self) -> None:
        sel = compute_src_selection(
            ["en"], supports_auto=False, current="en", fallback_pool=_POOL,
        )
        assert "auto" not in sel.codes

    def test_fallback_pool_when_backend_returns_empty(self) -> None:
        sel = compute_src_selection(
            [], supports_auto=False, current="en", fallback_pool=_POOL,
        )
        assert "en" in sel.codes
        assert set(sel.codes) == set(_POOL)

    def test_keeps_current_setting_if_supported(self) -> None:
        sel = compute_src_selection(
            ["en", "ja"], supports_auto=True, current="ja", fallback_pool=_POOL,
        )
        assert sel.selected == "ja"
        assert sel.fallback_from is None

    def test_falls_back_to_auto_when_current_unsupported_and_auto_available(self) -> None:
        sel = compute_src_selection(
            ["en"], supports_auto=True, current="fr", fallback_pool=_POOL,
        )
        assert sel.selected == "auto"
        assert sel.fallback_from == "fr"

    def test_falls_back_to_first_lang_when_no_auto(self) -> None:
        sel = compute_src_selection(
            ["ja", "en"], supports_auto=False, current="fr", fallback_pool=_POOL,
        )
        # sorted 済み先頭(= "en")に fallback
        assert sel.selected == "en"
        assert sel.fallback_from == "fr"

    def test_deduplicates_and_sorts(self) -> None:
        sel = compute_src_selection(
            ["ja", "en", "ja", "en"], supports_auto=False, current="en",
            fallback_pool=_POOL,
        )
        assert sel.codes == ["en", "ja"]


class TestComputeTgtSelection:
    def test_excludes_auto_from_choices(self) -> None:
        sel = compute_tgt_selection(
            ["auto", "en", "ja", "fr"], current="ja", fallback_pool=_POOL,
        )
        assert "auto" not in sel.codes
        assert sel.codes == ["en", "fr", "ja"]

    def test_keeps_current_if_supported(self) -> None:
        sel = compute_tgt_selection(["en", "ja"], current="ja", fallback_pool=_POOL)
        assert sel.selected == "ja"
        assert sel.fallback_from is None

    def test_fallback_prefers_japanese(self) -> None:
        sel = compute_tgt_selection(
            ["eng", "jpn", "fra"], current="xxx", fallback_pool=_POOL,
        )
        assert sel.selected == "jpn"
        assert sel.fallback_from == "xxx"

    def test_fallback_to_english_when_no_japanese(self) -> None:
        sel = compute_tgt_selection(
            ["eng", "fra", "deu"], current="xxx", fallback_pool=_POOL,
        )
        assert sel.selected == "eng"

    def test_fallback_to_first_when_no_en_no_ja(self) -> None:
        sel = compute_tgt_selection(["fr", "de", "es"], current="xx", fallback_pool=_POOL)
        # sorted 順の先頭 = "de"
        assert sel.selected == "de"

    def test_empty_backend_response_uses_fallback_pool(self) -> None:
        sel = compute_tgt_selection([], current="ja", fallback_pool=_POOL)
        assert "ja" in sel.codes
        assert sel.selected == "ja"


class TestTtsWarningNeeded:
    def test_warns_when_tts_does_not_support_current_tgt(self) -> None:
        assert tts_warning_needed(
            tts_backend="fake_tts", supported=["en", "ja"], current_tgt="fr",
        ) is True

    def test_no_warn_when_tts_supports_current_tgt(self) -> None:
        assert tts_warning_needed(
            tts_backend="fake_tts", supported=["en", "ja"], current_tgt="ja",
        ) is False

    def test_no_warn_when_supported_list_empty(self) -> None:
        """未知(空リスト)backend は警告しない(誤検知より沈黙)。"""
        assert tts_warning_needed(
            tts_backend="fake_tts", supported=[], current_tgt="fr",
        ) is False

    def test_no_warn_when_no_tts_backend(self) -> None:
        assert tts_warning_needed(
            tts_backend="", supported=["en"], current_tgt="fr",
        ) is False

    def test_no_warn_when_tts_none(self) -> None:
        """TTS=(なし)(text_only)のときは読み上げ言語の警告を出さない。"""
        assert tts_warning_needed(
            tts_backend="none", supported=["en"], current_tgt="fr",
        ) is False

    def test_warns_when_current_tgt_empty(self) -> None:
        """tgt が空文字でも supported 確認はできないので警告対象(移行元の挙動)。"""
        assert tts_warning_needed(
            tts_backend="fake_tts", supported=["en"], current_tgt="",
        ) is True


class TestMessageFormatting:
    """通知バナー文言(移行元と一字一句一致することを固定文字列で検証)。"""

    def test_src_fallback_message(self) -> None:
        assert format_src_fallback_message("fra", "auto", "whisper") == (
            "入力言語を fra (French) から auto (Auto-detect) に変更しました"
            "(whisper が fra に対応していないため)"
        )

    def test_tgt_fallback_message(self) -> None:
        assert format_tgt_fallback_message("xyz", "jpn", "nllb200") == (
            "出力言語を xyz から jpn (Japanese) に変更しました"
            "(nllb200 が xyz に対応していないため)"
        )

    def test_tts_warning_message(self) -> None:
        assert format_tts_warning_message("fra", "sapi") == (
            "TTS バックエンド sapi は読み上げ言語 fra (French) に対応していません"
            "(Translator 出力言語を変えるか、別の TTS バックエンドに切り替えてください)"
        )


class TestRestrictToTts:
    """出力言語候補の「翻訳 ∩ TTS」絞り込み。"""

    def test_intersects_with_tts_languages(self) -> None:
        from voice_translator.gui.logic.language_choices import restrict_to_tts

        assert restrict_to_tts(["en", "ja", "fr"], ["ja", "en", "de"]) == ["en", "ja"]

    def test_empty_tts_means_no_restriction(self) -> None:
        """TTS の対応言語が不明(空)/ TTS なしのときは絞らない。"""
        from voice_translator.gui.logic.language_choices import restrict_to_tts

        assert restrict_to_tts(["en", "ja"], []) == ["en", "ja"]

    def test_empty_intersection_degrades_to_original(self) -> None:
        """積が空になる組合せはプルダウンを空にせず、元の候補のまま(警告に委ねる)。"""
        from voice_translator.gui.logic.language_choices import restrict_to_tts

        assert restrict_to_tts(["en"], ["ja"]) == ["en"]

    def test_preserves_order_of_codes(self) -> None:
        from voice_translator.gui.logic.language_choices import restrict_to_tts

        assert restrict_to_tts(["fr", "ja", "en"], ["en", "fr"]) == ["fr", "en"]
