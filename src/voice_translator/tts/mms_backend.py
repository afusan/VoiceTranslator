"""MmsTtsBackend: Meta MMS-TTS(多言語ローカル TTS)。

役割: `facebook/mms-tts-<iso639_3>` の VITS モデルを HuggingFace から**言語単位で
遅延取得・ロード**し、テキストを synthesize して float32 PCM を返す。1,100+ 言語に
対応し、低資源言語(アフリカ系等)の読み上げを担う多言語 TTS の主軸。

設計:
- **言語ごとにモデルが異なる**ため「言語単位の遅延ロード」を行う。内部に
  `{iso639_1: _LoadedVoice}` の LRU キャッシュを持ち、上限を超えたら古い言語を破棄する
  (1 言語 ~0.5〜1GB の RAM を食うため既定上限は小さい)。
- **DL を発話スレッドで起こすと会話が固まる**ため、出力言語が確定した時点で
  `prefetch_language()` を裏で呼び事前確保する配線を想定(GUI 側の責務)。`synthesize()`
  でも未ロードなら同期ロードする(prefetch されなかった場合の縮退)。
- 追加ライブラリは実質不要(transformers/torch は base 依存)。一部スクリプトの言語は
  入力のローマ字化(uroman)が要るため、必要時のみ `tts-mms` extras を遅延 import する。
- モデルは **CC-BY-NC 4.0(非商用)**。NLLB と同じ扱いで README/LICENSE/同意表示に明示する。

言語コードについて:
- backend の I/F は ISO 639-1(本アプリ内部標準)。MMS のチェックポイントは ISO 639-3 の
  ため、`_ISO1_TO_MMS` で対応付ける。低資源言語の多くは 639-1 を持たない(639-3 のみ)ため、
  現状の対応表は **639-1 で表現できる高信頼の初期集合**に限る。639-3 までの開放は
  言語コード体系の拡張(横断課題)で行う(`docs/design/feature-mms-multilingual/Plan.md`)。
"""

from __future__ import annotations

import threading
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any

import numpy as np

from voice_translator.common.errors import FatalError, SkipError
from voice_translator.common.languages import LANGUAGE_NAMES
from voice_translator.common.types import BackendCapabilities, ModelStatus

from .backend import TtsBackend

# ISO 639-1 → MMS チェックポイントの言語コード(ISO 639-3)。
# 注意: これは「639-1 で表現できる」かつ「MMS にチェックポイントが存在する確度が高い」
# 初期集合。MMS は個別言語/マクロ言語の区別(例: ペルシャ語 fas→pes、ネパール語 ne→npi)が
# あり、思い込みで増やすと load 時に 404(NOT_DOWNLOADED)になる。拡張は MMS の言語一覧と
# 突き合わせて行うこと(横断課題: 言語コード体系の 639-3 拡張)。
_ISO1_TO_MMS: dict[str, str] = {
    "en": "eng",
    "es": "spa",
    "fr": "fra",
    "de": "deu",
    "it": "ita",
    "pt": "por",
    "ru": "rus",
    "ko": "kor",
    "vi": "vie",
    "tr": "tur",
    "sw": "swh",  # スワヒリ語
    "yo": "yor",  # ヨルバ語
    "ha": "hau",  # ハウサ語
    "am": "amh",  # アムハラ語
}

_REPO_TEMPLATE = "facebook/mms-tts-{code}"


@dataclass
class _LoadedVoice:
    """ロード済み 1 言語分のモデル一式。"""

    model: Any        # transformers VitsModel
    tokenizer: Any    # transformers VitsTokenizer
    samplerate: int
    is_uroman: bool   # 入力のローマ字化が必要か


class MmsTtsBackend(TtsBackend):
    """Meta MMS-TTS バックエンド(ローカル / 無認証 / 多言語 / 言語単位の遅延ロード)。

    役割: 出力言語ごとに `facebook/mms-tts-*` を遅延ロードしてキャッシュし、
    synthesize() でテキストから float32 PCM を生成する。voice モデルは初回利用時に
    HF からダウンロード(以後は HF キャッシュ)。
    """

    @classmethod
    def supported_output_languages(cls) -> list[str]:
        """対応する読み上げ言語(ISO 639-1)。

        `_ISO1_TO_MMS` のうち言語テーブル(`LANGUAGE_NAMES`)で表示可能なものに限る。
        未ロードでも答える必要があるため、ここでは transformers を import しない。
        """
        return sorted(c for c in _ISO1_TO_MMS if c in LANGUAGE_NAMES)

    def __init__(
        self,
        *,
        device: str = "auto",
        max_cached_languages: int = 2,
    ) -> None:
        super().__init__()  # BackendBase: status=INIT
        self._set_status(ModelStatus.LOADING)
        self._device_pref = device
        self._max_cached = max(1, int(max_cached_languages))
        # LRU: 末尾が最近使用。アクセスは _cache_lock で保護(synthesize と prefetch が別スレッド)。
        self._cache: "OrderedDict[str, _LoadedVoice]" = OrderedDict()
        self._cache_lock = threading.Lock()

        # transformers/torch は base 依存だが、環境破損時に分かりやすく落とすため遅延 import で確認。
        try:
            import torch  # type: ignore  # noqa: F401
            from transformers import VitsModel  # type: ignore  # noqa: F401
        except Exception as e:  # noqa: BLE001
            self.record_error(e, context="mms import")
            raise FatalError(
                f"MMS-TTS の依存(transformers/torch)ロードに失敗: {e}", cause=e,
            ) from e

        self._device = self._resolve_device()
        # エンジン準備完了(言語モデルはオンデマンド)。
        self._set_status(ModelStatus.LOADED)

    # ----------------------------------------------------------
    def _resolve_device(self) -> str:
        """`device="auto"` を実デバイスへ解決する(コードパスは 1 本)。"""
        if self._device_pref in ("cpu", "cuda"):
            return self._device_pref
        try:
            import torch  # type: ignore

            return "cuda" if torch.cuda.is_available() else "cpu"
        except Exception:  # noqa: BLE001
            return "cpu"

    # ----------------------------------------------------------
    def prefetch_language(self, tgt_lang: str) -> None:
        """出力言語の確定時に裏で呼ぶ事前確保(DL + ロード)。

        会話中の初回発話で 100〜150MB の DL が走るとパイプラインが固まるため、GUI は
        出力言語の選択を契機にバックグラウンドでこれを呼ぶ。未対応言語は黙って no-op
        (`supported_output_languages` 外は事前確保しても使われない)。失敗は呼び出し側で握る。
        """
        code = (tgt_lang or "").strip()
        if code not in _ISO1_TO_MMS:
            return
        self._ensure_language(code)

    # ----------------------------------------------------------
    def _ensure_language(self, lang: str) -> _LoadedVoice:
        """指定言語のモデルをロード済みにして返す(キャッシュ + LRU)。"""
        with self._cache_lock:
            voice = self._cache.get(lang)
            if voice is not None:
                self._cache.move_to_end(lang)  # 最近使用に更新
                return voice

        # ロードはロック外で(重い DL/構築中に他スレッドを止めない)。
        voice = self._load_voice(lang)

        with self._cache_lock:
            # 競合ロードで既に入っていればそちらを優先(重複構築は捨てる)。
            existing = self._cache.get(lang)
            if existing is not None:
                self._cache.move_to_end(lang)
                return existing
            self._cache[lang] = voice
            self._cache.move_to_end(lang)
            while len(self._cache) > self._max_cached:
                self._cache.popitem(last=False)  # 最も古い言語を破棄
        return voice

    # ----------------------------------------------------------
    def _load_voice(self, lang: str) -> _LoadedVoice:
        """`facebook/mms-tts-<code>` を DL → VitsModel/Tokenizer をロードする。"""
        mms_code = _ISO1_TO_MMS.get(lang)
        if mms_code is None:
            raise SkipError(f"MMS は出力言語 {lang} に対応していません")
        repo = _REPO_TEMPLATE.format(code=mms_code)

        import torch  # type: ignore
        from transformers import AutoTokenizer, VitsModel  # type: ignore

        # 未キャッシュなら from_pretrained が DL する。DL とロードを状態で区別する。
        self._set_status(ModelStatus.DOWNLOADING)
        try:
            model = VitsModel.from_pretrained(repo)
            tokenizer = AutoTokenizer.from_pretrained(repo)
        except Exception as e:  # noqa: BLE001
            self.record_error(e, context=f"mms load {repo}")
            self._set_status(ModelStatus.NOT_DOWNLOADED)
            raise FatalError(
                f"MMS voice ロード失敗 ({repo}): {e}", cause=e,
            ) from e

        self._set_status(ModelStatus.LOADING)
        try:
            model = model.to(self._device)
            model.eval()
            samplerate = int(model.config.sampling_rate)
            is_uroman = bool(getattr(tokenizer, "is_uroman", False))
        except Exception as e:  # noqa: BLE001
            self.record_error(e, context=f"mms init {repo}")
            self._set_status(ModelStatus.NOT_DOWNLOADED)
            raise FatalError(f"MMS voice 初期化失敗 ({repo}): {e}", cause=e) from e

        self._set_status(ModelStatus.LOADED)
        return _LoadedVoice(
            model=model, tokenizer=tokenizer, samplerate=samplerate, is_uroman=is_uroman,
        )

    # ----------------------------------------------------------
    def _romanize(self, text: str) -> str:
        """uroman でローマ字化する(`is_uroman` な言語のみ)。

        uroman は `tts-mms` extras。未導入なら分かりやすく落とす。
        """
        try:
            import uroman as ur  # type: ignore
        except Exception as e:  # noqa: BLE001
            raise FatalError(
                "この言語は入力のローマ字化(uroman)が必要です。"
                "`uv sync --extra tts-mms` でインストールしてください",
                cause=e,
            ) from e
        romanizer = ur.Uroman()
        return romanizer.romanize_string(text)

    # ----------------------------------------------------------
    def synthesize(self, text: str, tgt_lang: str) -> tuple[np.ndarray, int]:
        """テキストを MMS voice で合成し、(float32 PCM, samplerate) を返す。"""
        text = (text or "").strip()
        if not text:
            raise SkipError("TTS入力テキストが空です")

        lang = (tgt_lang or "").strip()
        if lang not in _ISO1_TO_MMS:
            raise SkipError(f"MMS は出力言語 {lang} に対応していません")

        voice = self._ensure_language(lang)

        import torch  # type: ignore

        synth_text = self._romanize(text) if voice.is_uroman else text
        try:
            inputs = voice.tokenizer(synth_text, return_tensors="pt")
            inputs = {k: v.to(self._device) for k, v in inputs.items()}
            with torch.no_grad():
                waveform = voice.model(**inputs).waveform
        except Exception as e:  # noqa: BLE001
            raise FatalError(f"MMS 合成失敗 ({lang}): {e}", cause=e) from e

        pcm = waveform.squeeze(0).detach().to("cpu").to(torch.float32).numpy()
        if pcm.size == 0:
            raise SkipError("MMS の出力が空です")
        return pcm.astype(np.float32, copy=False), voice.samplerate

    # ----------------------------------------------------------
    def capabilities(self) -> BackendCapabilities:
        return BackendCapabilities(
            is_cloud=False,
            requires_credentials=False,
            supported_languages=tuple(self.supported_output_languages()),
            notes=(
                "Meta MMS-TTS (VITS)。言語単位の遅延ロード(HF キャッシュ)。"
                f"device={self._device}, cached<= {self._max_cached} langs。"
                "モデルは CC-BY-NC 4.0(非商用)。"
            ),
        )
