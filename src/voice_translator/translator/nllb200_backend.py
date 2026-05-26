"""Nllb200TranslatorBackend: NLLB-200 distilled 600M を使った翻訳。

役割: 書き起こしテキストを src 言語 → tgt 言語 に翻訳する。
言語コードは ISO 639-1 を NLLB の "<lang>_<script>" 形式に内部で変換する。
"""

from __future__ import annotations

from voice_translator.common.errors import FatalError, SkipError
from voice_translator.common.types import BackendCapabilities
from voice_translator.common.utterance import Utterance

from .backend import TranslatorBackend


# ISO 639-1 → NLLB-200 言語コード(必要に応じて追加)。
# 詳しい一覧は https://github.com/facebookresearch/flores/blob/main/flores200/README.md
ISO_TO_NLLB: dict[str, str] = {
    "en": "eng_Latn",
    "ja": "jpn_Jpan",
    "zh": "zho_Hans",
    "ko": "kor_Hang",
    "es": "spa_Latn",
    "fr": "fra_Latn",
    "de": "deu_Latn",
    "it": "ita_Latn",
    "pt": "por_Latn",
    "ru": "rus_Cyrl",
    "ar": "arb_Arab",
    "hi": "hin_Deva",
    "th": "tha_Thai",
    "vi": "vie_Latn",
    "id": "ind_Latn",
    "tr": "tur_Latn",
    "nl": "nld_Latn",
    "pl": "pol_Latn",
    "sv": "swe_Latn",
    "fi": "fin_Latn",
    "da": "dan_Latn",
}


def _to_nllb_code(iso: str, *, fallback: str) -> str:
    """ISO 639-1 を NLLB-200 のコードに変換。未知/auto は fallback。"""
    if not iso or iso == "auto":
        return fallback
    return ISO_TO_NLLB.get(iso.lower(), fallback)


class Nllb200TranslatorBackend(TranslatorBackend):
    """NLLB-200 (Hugging Face transformers) ベースの翻訳バックエンド。

    役割: 初期化時にモデル+トークナイザをロードし、translate() で
    src_text → tgt_text を埋める。初回は約2GBのモデルDLが走るため時間がかかる。
    """

    def __init__(
        self,
        *,
        model_name: str = "facebook/nllb-200-distilled-600M",
        max_length: int = 512,
    ) -> None:
        try:
            from transformers import AutoModelForSeq2SeqLM, AutoTokenizer  # type: ignore
        except Exception as e:  # noqa: BLE001
            raise FatalError(f"transformers のロードに失敗: {e}", cause=e) from e

        try:
            self._tokenizer = AutoTokenizer.from_pretrained(model_name)
            self._model = AutoModelForSeq2SeqLM.from_pretrained(model_name)
        except Exception as e:  # noqa: BLE001
            raise FatalError(
                f"NLLB-200 モデルのロードに失敗 ({model_name}): {e}", cause=e
            ) from e

        self._model_name = model_name
        self._max_length = max_length

    # ----------------------------------------------------------
    def translate(self, utterance: Utterance, tgt_lang: str) -> Utterance:
        """utterance.src_text を tgt_lang に翻訳して tgt_text/tgt_lang を埋める。"""
        text = (utterance.src_text or "").strip()
        if not text:
            # 空入力は翻訳せずそのまま返す(下流で SKIP 判定される)
            utterance.tgt_text = ""
            utterance.tgt_lang = tgt_lang
            return utterance

        src_nllb = _to_nllb_code(utterance.src_lang, fallback="eng_Latn")
        tgt_nllb = _to_nllb_code(tgt_lang, fallback="jpn_Jpan")

        try:
            self._tokenizer.src_lang = src_nllb
            inputs = self._tokenizer(text, return_tensors="pt")
            translated = self._model.generate(
                **inputs,
                forced_bos_token_id=self._tokenizer.convert_tokens_to_ids(tgt_nllb),
                max_length=self._max_length,
            )
            result = self._tokenizer.batch_decode(
                translated, skip_special_tokens=True
            )[0]
        except Exception as e:  # noqa: BLE001
            raise FatalError(f"NLLB-200 翻訳失敗: {e}", cause=e) from e

        result = (result or "").strip()
        if not result:
            raise SkipError("翻訳結果が空です")

        utterance.tgt_text = result
        utterance.tgt_lang = tgt_lang
        return utterance

    # ----------------------------------------------------------
    def capabilities(self) -> BackendCapabilities:
        return BackendCapabilities(
            supported_languages=tuple(ISO_TO_NLLB.keys()),
            requires_gpu=False,  # GPU があれば高速だが必須ではない
            notes=f"NLLB-200 ({self._model_name})。200言語対応(マッピング表は要拡張)",
        )
