"""Nllb200TranslatorBackend: NLLB-200 distilled 600M を使った翻訳。

役割: 書き起こしテキストを src 言語 → tgt 言語 に翻訳する。
言語コードは ISO 639-1 を NLLB の "<lang>_<script>" 形式に内部で変換する。
device は "auto" / "cuda" / "mps" / "cpu" を受け、利用可能なアクセラレータを
自動選択する(CPU を floor とする配布方針に従う)。
"""

from __future__ import annotations

from voice_translator.common.cache_check import check_nllb200
from voice_translator.common.device import resolve_torch_device
from voice_translator.common.errors import FatalError, SkipError
from voice_translator.common.types import BackendCapabilities, ModelInfo, ModelStatus

from .backend import TranslatorBackend


# NLLB-200 推奨モデルの目安値(暫定)。
_RECOMMENDED_MODELS: tuple[ModelInfo, ...] = (
    ModelInfo(
        name="facebook/nllb-200-distilled-600M",
        display_name="distilled-600M (~2.3GB, 既定)",
        ram_gb=3.0,
        vram_gb_if_gpu=2.0,
        download_size_gb=2.3,
        target_proc_ms_per_sec_audio=None,  # 翻訳は音声長と無関係
    ),
    ModelInfo(
        name="facebook/nllb-200-distilled-1.3B",
        display_name="distilled-1.3B (~5GB)",
        ram_gb=6.0,
        vram_gb_if_gpu=4.0,
        download_size_gb=5.0,
    ),
    ModelInfo(
        name="facebook/nllb-200-1.3B",
        display_name="1.3B (~5GB, 非 distilled)",
        ram_gb=6.0,
        vram_gb_if_gpu=4.0,
        download_size_gb=5.0,
    ),
)


# ISO 639-1 → NLLB-200 言語コード。
# 詳しい一覧は https://github.com/facebookresearch/flores/blob/main/flores200/README.md
# NLLB-200 本体は 200 言語対応だが、UI に並べて意味があるメジャー言語(ISO 639-1
# 表現可能 + 共通言語テーブルに英語名がある言語)に絞っている。上流追加時はここを追従。
ISO_TO_NLLB: dict[str, str] = {
    # MVP の 17 言語
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
    # ヨーロッパ系
    "pl": "pol_Latn",
    "sv": "swe_Latn",
    "fi": "fin_Latn",
    "da": "dan_Latn",
    "no": "nob_Latn",  # Norwegian Bokmål
    "nn": "nno_Latn",  # Nynorsk
    "cs": "ces_Latn",
    "sk": "slk_Latn",
    "el": "ell_Grek",
    "hu": "hun_Latn",
    "ro": "ron_Latn",
    "bg": "bul_Cyrl",
    "uk": "ukr_Cyrl",
    "be": "bel_Cyrl",
    "sr": "srp_Cyrl",
    "hr": "hrv_Latn",
    "bs": "bos_Latn",
    "sl": "slv_Latn",
    "et": "est_Latn",
    "lv": "lvs_Latn",
    "lt": "lit_Latn",
    "ca": "cat_Latn",
    "gl": "glg_Latn",
    "eu": "eus_Latn",
    "is": "isl_Latn",
    "mt": "mlt_Latn",
    "ga": "gle_Latn",
    "cy": "cym_Latn",
    "sq": "als_Latn",
    "mk": "mkd_Cyrl",
    # 中東 / アフリカ
    "he": "heb_Hebr",
    "fa": "pes_Arab",
    "ur": "urd_Arab",
    "ps": "pbt_Arab",
    "sw": "swh_Latn",
    "am": "amh_Ethi",
    "yo": "yor_Latn",
    "ha": "hau_Latn",
    "so": "som_Latn",
    # アジア
    "bn": "ben_Beng",
    "ta": "tam_Taml",
    "te": "tel_Telu",
    "ml": "mal_Mlym",
    "mr": "mar_Deva",
    "gu": "guj_Gujr",
    "pa": "pan_Guru",
    "kn": "kan_Knda",
    "ne": "npi_Deva",
    "si": "sin_Sinh",
    "my": "mya_Mymr",
    "km": "khm_Khmr",
    "lo": "lao_Laoo",
    "ka": "kat_Geor",
    "hy": "hye_Armn",
    "mn": "khk_Cyrl",
    "kk": "kaz_Cyrl",
    "uz": "uzn_Latn",
    "az": "azj_Latn",
    "ms": "zsm_Latn",
    "tl": "tgl_Latn",
}


def _to_nllb_code(iso: str, *, fallback: str) -> str:
    """ISO 639-1 を NLLB-200 のコードに変換。未知/auto は fallback。"""
    if not iso or iso == "auto":
        return fallback
    return ISO_TO_NLLB.get(iso.lower(), fallback)


class Nllb200TranslatorBackend(TranslatorBackend):
    """NLLB-200 (Hugging Face transformers) ベースの翻訳バックエンド。

    役割: 初期化時にモデル+トークナイザをロードし、translate(src_text, src_lang, tgt_lang) で
    翻訳テキストを返す。初回は約2GBのモデルDLが走るため時間がかかる。
    """

    def __init__(
        self,
        *,
        model_name: str = "facebook/nllb-200-distilled-600M",
        max_length: int = 512,
        num_beams: int = 4,
        no_repeat_ngram_size: int = 3,
        repetition_penalty: float = 1.1,
        early_stopping: bool = True,
        device: str = "auto",
    ) -> None:
        super().__init__()  # BackendBase: status=INIT
        try:
            from transformers import AutoModelForSeq2SeqLM, AutoTokenizer  # type: ignore
        except Exception as e:  # noqa: BLE001
            self.record_error(e, context="transformers import")
            raise FatalError(f"transformers のロードに失敗: {e}", cause=e) from e

        # device を解決("auto" → cuda/mps/cpu)。明示指定はそのまま使う。
        self._device = resolve_torch_device(device)

        # キャッシュ事前判定で DOWNLOADING / LOADING を出し分ける(R-3 / R2-1)。
        cache_status = check_nllb200(model_name)
        if cache_status == ModelStatus.LOADED:
            self._set_status(ModelStatus.LOADING)
        else:
            self._set_status(ModelStatus.DOWNLOADING)

        try:
            self._tokenizer = AutoTokenizer.from_pretrained(model_name)
            self._model = AutoModelForSeq2SeqLM.from_pretrained(model_name)
            # 解決した device にモデルを移送(失敗時は CPU フォールバック)
            try:
                self._model = self._model.to(self._device)
            except Exception:  # noqa: BLE001 - GPU OOM / 未対応で落ちたら CPU で続行
                self._device = "cpu"
                self._model = self._model.to("cpu")
        except Exception as e:  # noqa: BLE001
            self.record_error(e, context="model load")
            raise FatalError(
                f"NLLB-200 モデルのロードに失敗 ({model_name}): {e}", cause=e
            ) from e

        self._model_name = model_name
        self._max_length = max_length
        # 退化(degenerate output)抑止のための生成パラメータ。
        # 既定値のまま(greedy + 抑止なし)だと長文かつ語彙反復の多い入力で
        # 同じ n-gram に吸着し、max_length まで延々と繰り返してしまう
        # (translations.jsonl L184 で観測された症状)。
        self._num_beams = num_beams
        self._no_repeat_ngram_size = no_repeat_ngram_size
        self._repetition_penalty = repetition_penalty
        self._early_stopping = early_stopping
        self._set_status(ModelStatus.LOADED)

    @property
    def device(self) -> str:
        """実際に使用しているデバイス名(診断/テスト用)。"""
        return self._device

    # ----------------------------------------------------------
    def translate(self, src_text: str, src_lang: str, tgt_lang: str) -> str:
        """src_text を tgt_lang に翻訳した文字列を返す。"""
        text = (src_text or "").strip()
        if not text:
            # 空入力は翻訳せず空文字を返す(呼び出し側で SKIP 判定)
            return ""

        src_nllb = _to_nllb_code(src_lang, fallback="eng_Latn")
        tgt_nllb = _to_nllb_code(tgt_lang, fallback="jpn_Jpan")

        try:
            self._tokenizer.src_lang = src_nllb
            inputs = self._tokenizer(text, return_tensors="pt")
            # 入力テンソルを model と同じデバイスへ移送(CPU のときは no-op に近い)
            inputs = {k: v.to(self._device) for k, v in inputs.items()}
            translated = self._model.generate(
                **inputs,
                forced_bos_token_id=self._tokenizer.convert_tokens_to_ids(tgt_nllb),
                max_length=self._max_length,
                num_beams=self._num_beams,
                no_repeat_ngram_size=self._no_repeat_ngram_size,
                repetition_penalty=self._repetition_penalty,
                early_stopping=self._early_stopping,
            )
            result = self._tokenizer.batch_decode(
                translated, skip_special_tokens=True
            )[0]
        except Exception as e:  # noqa: BLE001
            raise FatalError(f"NLLB-200 翻訳失敗: {e}", cause=e) from e

        result = (result or "").strip()
        if not result:
            raise SkipError("翻訳結果が空です")
        return result

    # ----------------------------------------------------------
    def capabilities(self) -> BackendCapabilities:
        return BackendCapabilities(
            supported_languages=tuple(ISO_TO_NLLB.keys()),
            requires_gpu=False,  # GPU があれば高速だが必須ではない
            is_cloud=False,
            requires_credentials=False,
            notes=(
                f"NLLB-200 ({self._model_name}) / device={self._device}。"
                "200言語対応(マッピング表は要拡張)"
            ),
        )

    def list_recommended_models(self) -> list[ModelInfo]:
        """NLLB-200 の代表サイズ一覧を返す。"""
        return list(_RECOMMENDED_MODELS)

    # ----------------------------------------------------------
    # 対応言語の宣言(UI の出力言語プルダウン連動用)
    # ----------------------------------------------------------
    @classmethod
    def supported_target_languages(cls) -> list[str]:
        """ISO_TO_NLLB に登録した ISO 639-1 コードを返す(ソート済み)。

        クラスメソッド: UI が backend をロードせずに問い合わせる。
        本 backend は対称(同じセットで src/tgt 双方向 OK)なので
        supported_source_languages はオーバーライドせず default を使う。
        """
        return sorted(ISO_TO_NLLB.keys())
