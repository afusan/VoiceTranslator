"""MMS∩NLLB の検証済み言語セットから、languages.py / backends 用の dict リテラルを生成。

言語カバレッジ(`tts/mms_backend.py: _MMS_LANGS` / `translator/nllb200_backend.py:
CANONICAL_TO_NLLB` / `common/languages.py: LANGUAGE_NAMES` の拡張分)の**再生成元**。
HF レジストリの `facebook/mms-tts-*` 実在チェックポイントと NLLB tokenizer の FLORES
基底コードの積を取り、推測ゼロで対応言語を確定する。

実行: `py -m uv run python docs/design/done/feature-mms-multilingual/gen_lang_table.py`
新言語を増やすときは NAMES に英語名を足し、出力を各ソースへ反映する。
"""
from huggingface_hub import HfApi
from transformers import AutoTokenizer

api = HfApi()
mms_set = {m.id.split('mms-tts-')[-1] for m in api.list_models(author='facebook', search='mms-tts', limit=2000) if '/mms-tts-' in m.id}
tok = AutoTokenizer.from_pretrained('facebook/nllb-200-distilled-600M')
flores = [c for c in tok._extra_special_tokens if '_' in c]
base = {}
for f in flores:
    base.setdefault(f.split('_')[0], f)
inter = sorted(set(base) & mms_set)

# 99 言語の英語名(手動キュレーション、ISO 639-3 基準)
NAMES = {
 "ace":"Acehnese","aka":"Akan","amh":"Amharic","asm":"Assamese","awa":"Awadhi",
 "ayr":"Central Aymara","azb":"South Azerbaijani","bak":"Bashkir","bam":"Bambara",
 "ban":"Balinese","bem":"Bemba","ben":"Bengali","bod":"Tibetan","bul":"Bulgarian",
 "cat":"Catalan","ceb":"Cebuano","crh":"Crimean Tatar","cym":"Welsh","deu":"German",
 "dik":"Southwestern Dinka","dyu":"Dyula","dzo":"Dzongkha","ell":"Greek","eng":"English",
 "eus":"Basque","ewe":"Ewe","fao":"Faroese","fij":"Fijian","fin":"Finnish","fon":"Fon",
 "fra":"French","grn":"Guarani","guj":"Gujarati","hat":"Haitian Creole","hau":"Hausa",
 "heb":"Hebrew","hin":"Hindi","hne":"Chhattisgarhi","hun":"Hungarian","ilo":"Ilocano",
 "ind":"Indonesian","isl":"Icelandic","jav":"Javanese","kab":"Kabyle","kac":"Jingpho",
 "kan":"Kannada","kaz":"Kazakh","kbp":"Kabiye","khm":"Khmer","kik":"Kikuyu",
 "kin":"Kinyarwanda","kir":"Kyrgyz","kor":"Korean","lao":"Lao","lug":"Ganda",
 "mag":"Magahi","mai":"Maithili","mal":"Malayalam","mar":"Marathi","min":"Minangkabau",
 "mos":"Mossi","mya":"Burmese","nld":"Dutch","nus":"Nuer","nya":"Nyanja",
 "ory":"Odia","pag":"Pangasinan","pan":"Punjabi","pap":"Papiamento","pol":"Polish",
 "por":"Portuguese","quy":"Ayacucho Quechua","ron":"Romanian","run":"Rundi","rus":"Russian",
 "sag":"Sango","shn":"Shan","smo":"Samoan","sna":"Shona","som":"Somali","spa":"Spanish",
 "sun":"Sundanese","swe":"Swedish","swh":"Swahili","tam":"Tamil","taq":"Tamasheq",
 "tat":"Tatar","tel":"Telugu","tgk":"Tajik","tgl":"Tagalog","tha":"Thai","tir":"Tigrinya",
 "tpi":"Tok Pisin","tso":"Tsonga","tur":"Turkish","ukr":"Ukrainian","vie":"Vietnamese",
 "war":"Waray","yor":"Yoruba",
}

missing = [c for c in inter if c not in NAMES]
assert not missing, f"name 未定義: {missing}"
assert all(c in mms_set for c in inter), "MMS にない code がある"

# 出力 1: CANONICAL_TO_NLLB(99 → FLORES)
print("=== CANONICAL_TO_NLLB ===")
items = "".join(f'    "{c}": "{base[c]}",\n' for c in inter)
print("{\n"+items+"}")

# 出力 2: MMS canonical 言語タプル(checkpoint code = canonical)
print("=== MMS_LANGS ===")
print("(" + ", ".join(f'"{c}"' for c in inter) + ")")

# 出力 3: NAMES(全 99)
print("=== NAMES (canonical -> English) ===")
print("".join(f'    "{c}": "{NAMES[c]}",\n' for c in inter))
print("count=", len(inter))
