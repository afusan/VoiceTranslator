"""言語コード ⇔ 表示名の一元テーブル。

役割: ISO 639-1 言語コードと英語表示名のマッピングを単一の事実源として保持する。
ASR backend 等が返す対応言語コード(`["en", "ja", ...]`)を UI 表示用に
`"en (English)"` 形式へ整形するためのユーティリティを提供する。

設計判断:
- backend 側はコードのみ(`"en"`)で扱う(計算ロジックの単純化)
- UI 表示で「(English)」を付ける変換はこのモジュールに集約(表示形式変更が 1 箇所で済む)
- 表示名は **英語名で統一**(日本語名のローカライズは別ブランチで検討)
- `"auto"` は ISO 639-1 ではないが、本アプリ固有の「自動検出」マーカーとしてこの表で扱う
"""

from __future__ import annotations

# ============================================================
# 言語コード → 英語表示名
# ============================================================
# Whisper の対応 99 言語を基準に、ISO 639-1 で表現可能なものを採録。
# 一部 ISO 639-1 を持たない言語(yue 等)は除外(必要になれば 639-3 を別キーで扱う)。
# `auto` は本アプリ固有の「自動検出」マーカー。
LANGUAGE_NAMES: dict[str, str] = {
    "auto": "Auto-detect",
    # === メジャー(MVP の _LANG_CHOICES から継承)===
    "en": "English",
    "ja": "Japanese",
    "zh": "Chinese",
    "ko": "Korean",
    "es": "Spanish",
    "fr": "French",
    "de": "German",
    "it": "Italian",
    "pt": "Portuguese",
    "ru": "Russian",
    "ar": "Arabic",
    "hi": "Hindi",
    "th": "Thai",
    "vi": "Vietnamese",
    "id": "Indonesian",
    "tr": "Turkish",
    # === Whisper 対応の残り(ISO 639-1 表現可能なもの)===
    "af": "Afrikaans",
    "am": "Amharic",
    "as": "Assamese",
    "az": "Azerbaijani",
    "ba": "Bashkir",
    "be": "Belarusian",
    "bg": "Bulgarian",
    "bn": "Bengali",
    "bo": "Tibetan",
    "br": "Breton",
    "bs": "Bosnian",
    "ca": "Catalan",
    "cs": "Czech",
    "cy": "Welsh",
    "da": "Danish",
    "el": "Greek",
    "et": "Estonian",
    "eu": "Basque",
    "fa": "Persian",
    "fi": "Finnish",
    "fo": "Faroese",
    "gl": "Galician",
    "gu": "Gujarati",
    "ha": "Hausa",
    "haw": "Hawaiian",
    "he": "Hebrew",
    "hr": "Croatian",
    "ht": "Haitian Creole",
    "hu": "Hungarian",
    "hy": "Armenian",
    "is": "Icelandic",
    "jw": "Javanese",
    "ka": "Georgian",
    "kk": "Kazakh",
    "km": "Khmer",
    "kn": "Kannada",
    "la": "Latin",
    "lb": "Luxembourgish",
    "ln": "Lingala",
    "lo": "Lao",
    "lt": "Lithuanian",
    "lv": "Latvian",
    "mg": "Malagasy",
    "mi": "Maori",
    "mk": "Macedonian",
    "ml": "Malayalam",
    "mn": "Mongolian",
    "mr": "Marathi",
    "ms": "Malay",
    "mt": "Maltese",
    "my": "Burmese",
    "ne": "Nepali",
    "nl": "Dutch",
    "nn": "Norwegian Nynorsk",
    "no": "Norwegian",
    "oc": "Occitan",
    "pa": "Punjabi",
    "pl": "Polish",
    "ps": "Pashto",
    "ro": "Romanian",
    "sa": "Sanskrit",
    "sd": "Sindhi",
    "si": "Sinhala",
    "sk": "Slovak",
    "sl": "Slovenian",
    "sn": "Shona",
    "so": "Somali",
    "sq": "Albanian",
    "sr": "Serbian",
    "su": "Sundanese",
    "sv": "Swedish",
    "sw": "Swahili",
    "ta": "Tamil",
    "te": "Telugu",
    "tg": "Tajik",
    "tk": "Turkmen",
    "tl": "Tagalog",
    "tt": "Tatar",
    "uk": "Ukrainian",
    "ur": "Urdu",
    "uz": "Uzbek",
    "yi": "Yiddish",
    "yo": "Yoruba",
}


def language_name(code: str) -> str:
    """ISO 639-1 コードの英語名を返す。未知コードはコードそのまま。"""
    return LANGUAGE_NAMES.get(code, code)


def format_language(code: str) -> str:
    """UI 表示用に `"en (English)"` 形式で返す。

    未知コードは `"xx (xx)"` 風にならないようコードのみを返す(冗長表示を避ける)。
    `auto` は `"auto (Auto-detect)"` になる。
    """
    name = LANGUAGE_NAMES.get(code)
    if name is None:
        return code
    return f"{code} ({name})"


def parse_language(label: str) -> str:
    """`"en (English)"` から `"en"` を取り出す逆変換。

    UI から受け取った表示ラベルを内部コードに戻すのに使う。
    `"en (English)"` も `"en"` も両方受け付ける。
    """
    if " " in label:
        return label.split(" ", 1)[0]
    return label
