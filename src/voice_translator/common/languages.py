"""言語コード ⇔ 表示名の一元テーブル(内部標準 = ISO 639-3)。

役割: アプリ内部で扱う言語コードの単一の事実源。**内部標準は ISO 639-3**
(`"eng"`, `"jpn"`, `"yor"` …)。低資源言語の多くは ISO 639-1(2 文字)を持たず
639-3 しか持たないため、639-3 を正準に据えることで「翻訳/TTS が届くのに UI/設定に
載らない」蓋を外す。

設計判断:
- backend 側はコードのみで扱い(`"eng"`)、UI 表示で「(English)」を付ける変換はこの
  モジュールに集約(表示形式変更が 1 箇所で済む)。
- **正準は 639-3 一本**。外部境界(設定ファイルの legacy 値、Whisper/DeepL/NLLB 等の
  ベンダ形式)との変換はここで提供する:
  - `to_canonical()`: 入力(639-1 / 639-3 / "auto")→ 正準 639-3。legacy config の
    後方互換(`"ja"` → `"jpn"`)もこれで吸収する。
  - `iso1_to_iso3()` / `iso3_to_iso1()`: backend が 639-1 キーのネイティブ変換表を
    据え置いたまま、申告(639-3)とベンダ呼び出し(639-1)の境界を繋ぐための窓。
- ベンダ変換表(`ISO_TO_NLLB` 等)は各 backend 側に **639-1 キーのまま**残す。これらは
  「内部コード → ベンダ形式」の表で、Whisper の 639-1 もその一形態に過ぎない。正準を
  639-3 にしても、境界で 639-3↔639-1 を一段挟むだけで再キーは不要(churn 最小)。
- 表示名は **英語名で統一**(日本語名のローカライズは別途)。
- `"auto"` は ISO 639 ではないが、本アプリ固有の「自動検出」マーカーとしてこの表で扱う。
- 639-3 しか持たない低資源言語は、`LANGUAGE_NAMES` に 639-3 キーで直接足せる
  (`ISO1_TO_ISO3` に逆写像が無くても `iso3_to_iso1()` は passthrough する)。
"""

from __future__ import annotations

# ============================================================
# ISO 639-1(2 文字)→ ISO 639-3(正準)
# ============================================================
# Whisper の対応言語(99)を基準に、639-1 を持つものを採録。値が正準コード。
# Whisper 由来の非標準 639-1(`jw` = Javanese)も Whisper との往復のため採録する。
# 639-1 を持たない言語(yue 等)は `LANGUAGE_NAMES` に 639-3 で直接足す。
ISO1_TO_ISO3: dict[str, str] = {
    "en": "eng", "ja": "jpn", "zh": "zho", "ko": "kor", "es": "spa", "fr": "fra",
    "de": "deu", "it": "ita", "pt": "por", "ru": "rus", "ar": "ara", "hi": "hin",
    "th": "tha", "vi": "vie", "id": "ind", "tr": "tur",
    "af": "afr", "am": "amh", "as": "asm", "az": "aze", "ba": "bak", "be": "bel",
    "bg": "bul", "bn": "ben", "bo": "bod", "br": "bre", "bs": "bos", "ca": "cat",
    "cs": "ces", "cy": "cym", "da": "dan", "el": "ell", "et": "est", "eu": "eus",
    "fa": "fas", "fi": "fin", "fo": "fao", "ga": "gle", "gl": "glg", "gu": "guj",
    "ha": "hau", "he": "heb", "hr": "hrv", "ht": "hat", "hu": "hun", "hy": "hye",
    "is": "isl", "jw": "jav", "ka": "kat", "kk": "kaz", "km": "khm", "kn": "kan",
    "la": "lat", "lb": "ltz", "ln": "lin", "lo": "lao", "lt": "lit", "lv": "lav",
    "mg": "mlg", "mi": "mri", "mk": "mkd", "ml": "mal", "mn": "mon", "mr": "mar",
    "ms": "msa", "mt": "mlt", "my": "mya", "nb": "nob", "ne": "nep", "nl": "nld",
    "nn": "nno", "no": "nor", "oc": "oci", "pa": "pan", "pl": "pol", "ps": "pus",
    "ro": "ron", "sa": "san", "sd": "snd", "si": "sin", "sk": "slk", "sl": "slv",
    "sn": "sna", "so": "som", "sq": "sqi", "sr": "srp", "su": "sun", "sv": "swe",
    "sw": "swa", "ta": "tam", "te": "tel", "tg": "tgk", "tk": "tuk", "tl": "tgl",
    "tt": "tat", "uk": "ukr", "ur": "urd", "uz": "uzb", "yi": "yid", "yo": "yor",
}

# 逆写像(正準 639-3 → 639-1)。本表では複数 639-1 が同じ 639-3 に潰れることは無い。
ISO3_TO_ISO1: dict[str, str] = {v: k for k, v in ISO1_TO_ISO3.items()}


# ============================================================
# 言語コード(ISO 639-3, 正準)→ 英語表示名
# ============================================================
# `auto` は本アプリ固有の「自動検出」マーカー。`haw`(ハワイ語)は 639-1 を持たないので
# 639-3 キーで直接採録(639-3 直接採録の既存例)。
LANGUAGE_NAMES: dict[str, str] = {
    "auto": "Auto-detect",
    # === メジャー ===
    "eng": "English",
    "jpn": "Japanese",
    "zho": "Chinese",
    "kor": "Korean",
    "spa": "Spanish",
    "fra": "French",
    "deu": "German",
    "ita": "Italian",
    "por": "Portuguese",
    "rus": "Russian",
    "ara": "Arabic",
    "hin": "Hindi",
    "tha": "Thai",
    "vie": "Vietnamese",
    "ind": "Indonesian",
    "tur": "Turkish",
    # === Whisper 対応の残り ===
    "afr": "Afrikaans",
    "amh": "Amharic",
    "asm": "Assamese",
    "aze": "Azerbaijani",
    "bak": "Bashkir",
    "bel": "Belarusian",
    "bul": "Bulgarian",
    "ben": "Bengali",
    "bod": "Tibetan",
    "bre": "Breton",
    "bos": "Bosnian",
    "cat": "Catalan",
    "ces": "Czech",
    "cym": "Welsh",
    "dan": "Danish",
    "ell": "Greek",
    "est": "Estonian",
    "eus": "Basque",
    "fas": "Persian",
    "fin": "Finnish",
    "fao": "Faroese",
    "gle": "Irish",
    "glg": "Galician",
    "guj": "Gujarati",
    "hau": "Hausa",
    "haw": "Hawaiian",
    "heb": "Hebrew",
    "hrv": "Croatian",
    "hat": "Haitian Creole",
    "hun": "Hungarian",
    "hye": "Armenian",
    "isl": "Icelandic",
    "jav": "Javanese",
    "kat": "Georgian",
    "kaz": "Kazakh",
    "khm": "Khmer",
    "kan": "Kannada",
    "lat": "Latin",
    "ltz": "Luxembourgish",
    "lin": "Lingala",
    "lao": "Lao",
    "lit": "Lithuanian",
    "lav": "Latvian",
    "mlg": "Malagasy",
    "mri": "Maori",
    "mkd": "Macedonian",
    "mal": "Malayalam",
    "mon": "Mongolian",
    "mar": "Marathi",
    "msa": "Malay",
    "mlt": "Maltese",
    "mya": "Burmese",
    "nob": "Norwegian Bokmål",
    "nep": "Nepali",
    "nld": "Dutch",
    "nno": "Norwegian Nynorsk",
    "nor": "Norwegian",
    "oci": "Occitan",
    "pan": "Punjabi",
    "pol": "Polish",
    "pus": "Pashto",
    "ron": "Romanian",
    "san": "Sanskrit",
    "snd": "Sindhi",
    "sin": "Sinhala",
    "slk": "Slovak",
    "slv": "Slovenian",
    "sna": "Shona",
    "som": "Somali",
    "sqi": "Albanian",
    "srp": "Serbian",
    "sun": "Sundanese",
    "swe": "Swedish",
    "swa": "Swahili",
    "tam": "Tamil",
    "tel": "Telugu",
    "tgk": "Tajik",
    "tuk": "Turkmen",
    "tgl": "Tagalog",
    "tat": "Tatar",
    "ukr": "Ukrainian",
    "urd": "Urdu",
    "uzb": "Uzbek",
    "yid": "Yiddish",
    "yor": "Yoruba",
}


# ============================================================
# 正準化 / 境界変換
# ============================================================
def to_canonical(code: str) -> str:
    """任意の言語コードを正準(ISO 639-3)に正規化する。

    - `"auto"` / 既に正準(`LANGUAGE_NAMES` のキー)はそのまま。
    - legacy ISO 639-1(`"ja"`)は 639-3(`"jpn"`)へ。**config の後方互換はこれで吸収**。
    - 未知コードは passthrough(将来 639-3 で足す低資源言語を壊さない)。
    """
    if not code:
        return code
    if code == "auto" or code in LANGUAGE_NAMES:
        return code
    return ISO1_TO_ISO3.get(code, code)


def iso1_to_iso3(code: str) -> str:
    """639-1 → 正準 639-3。未知/既に 639-3 は passthrough。

    backend が 639-1 で書かれた対応言語リスト/変換表のキーを、申告(639-3)へ
    持ち上げるための窓。
    """
    if code == "auto":
        return code
    return ISO1_TO_ISO3.get(code, code)


def iso3_to_iso1(code: str) -> str:
    """正準 639-3 → 639-1。逆写像が無ければ passthrough。

    639-1 キーのネイティブ変換表(Whisper/NLLB/DeepL 等)を引く直前に、正準コードを
    表のキー方式へ落とすための窓。639-3 しか持たない言語は passthrough され、
    その場合 backend 側の表が 639-3 キーで引けることを期待する。
    """
    if code == "auto":
        return code
    return ISO3_TO_ISO1.get(code, code)


def language_name(code: str) -> str:
    """正準コードの英語名を返す。未知コードはコードそのまま。"""
    return LANGUAGE_NAMES.get(code, code)


def format_language(code: str) -> str:
    """UI 表示用に `"eng (English)"` 形式で返す。

    未知コードはコードのみを返す(冗長表示を避ける)。`auto` は `"auto (Auto-detect)"`。
    """
    name = LANGUAGE_NAMES.get(code)
    if name is None:
        return code
    return f"{code} ({name})"


def parse_language(label: str) -> str:
    """`"eng (English)"` から `"eng"` を取り出す逆変換。

    UI から受け取った表示ラベルを内部コードに戻すのに使う。
    `"eng (English)"` も `"eng"` も両方受け付ける。
    """
    if " " in label:
        return label.split(" ", 1)[0]
    return label
