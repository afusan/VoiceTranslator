"""Whisper 系 backend の対応言語コード(ISO 639-1)。

役割: faster-whisper / openai-whisper / OpenAI Whisper API の 3 backend は
いずれも Whisper の同じ言語セット(99 言語)に対応する。リストを 1 箇所に
集約して全 backend から共有する(上流更新時の追従漏れを防ぐ)。

`common/languages.py` の `LANGUAGE_NAMES` に存在するコードのみを採用している
(共通言語テーブルに名前が登録されていないコードは UI 表示できないため)。
"""

from __future__ import annotations


# 99 言語(`whisper.tokenizer.LANGUAGES` 由来、ソート済み)
WHISPER_INPUT_LANGUAGES: tuple[str, ...] = (
    "af", "am", "ar", "as", "az", "ba", "be", "bg", "bn", "bo",
    "br", "bs", "ca", "cs", "cy", "da", "de", "el", "en", "es",
    "et", "eu", "fa", "fi", "fo", "fr", "gl", "gu", "ha", "haw",
    "he", "hi", "hr", "ht", "hu", "hy", "id", "is", "it", "ja",
    "jw", "ka", "kk", "km", "kn", "ko", "la", "lb", "ln", "lo",
    "lt", "lv", "mg", "mi", "mk", "ml", "mn", "mr", "ms", "mt",
    "my", "ne", "nl", "nn", "no", "oc", "pa", "pl", "ps", "pt",
    "ro", "ru", "sa", "sd", "si", "sk", "sl", "sn", "so", "sq",
    "sr", "su", "sv", "sw", "ta", "te", "tg", "th", "tk", "tl",
    "tr", "tt", "uk", "ur", "uz", "vi", "yi", "yo", "zh",
)
