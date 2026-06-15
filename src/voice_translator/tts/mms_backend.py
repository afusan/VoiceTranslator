"""MmsTtsBackend: Meta MMS-TTS(多言語ローカル TTS)。

役割: `facebook/mms-tts-<iso639_3>` の VITS モデルを HuggingFace から**言語単位で
遅延取得・ロード**し、テキストを synthesize して float32 PCM を返す。1,100+ 言語に
対応し、低資源言語(アフリカ系等)の読み上げを担う多言語 TTS の主軸。

設計:
- **言語ごとにモデルが異なる**ため「言語単位の遅延ロード」を行う。内部に
  `{canonical639_3: _LoadedVoice}` の LRU キャッシュを持ち、上限を超えたら古い言語を破棄する
  (1 言語 ~0.5〜1GB の RAM を食うため既定上限は小さい)。
- **DL を発話スレッドで起こすと会話が固まる**ため、出力言語が確定した時点で
  `prefetch_language()` を裏で呼び事前確保する配線を想定(GUI 側の責務)。`synthesize()`
  でも未ロードなら同期ロードする(prefetch されなかった場合の縮退)。
- 追加ライブラリは実質不要(transformers/torch は base 依存)。一部スクリプトの言語は
  入力のローマ字化(uroman)が要るため、必要時のみ `tts-mms` extras を遅延 import する。
- モデルは **CC-BY-NC 4.0(非商用)**。NLLB と同じ扱いで README/LICENSE/同意表示に明示する。

言語コードについて:
- backend の I/F は本アプリ内部標準の **ISO 639-3**。MMS チェックポイント名(`mms-tts-<code>`)も
  639-3 なので、正準コードがそのままチェックポイント名になる(変換不要)。対応集合 `_MMS_LANGS`
  は **MMS 実在チェックポイント ∩ NLLB-200** を機械的に確定したもので、「翻訳でき、かつ
  読み上げできる」言語に限る(推測ゼロ。404 を出さない)。表示名は `common/languages.py`。
"""

from __future__ import annotations

import threading
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any

import numpy as np

from voice_translator.common.device import resolve_torch_device
from voice_translator.common.errors import FatalError, SkipError
from voice_translator.common.languages import LANGUAGE_NAMES
from voice_translator.common.types import BackendCapabilities, ModelStatus

from .backend import TtsBackend

# MMS が読み上げ可能な言語(正準 ISO 639-3)。MMS チェックポイント名は
# `facebook/mms-tts-<canonical>` で、コードは正準とそのまま一致する。
# この集合は **MMS-TTS(HF 上に実在するチェックポイント)∩ NLLB-200(翻訳可能)** を
# 機械的に突き合わせて確定したもの(推測ゼロ。HF レジストリで存在検証済み)。
# 「翻訳でき、かつ読み上げできる」言語に限るので、出力言語候補として実用になる。
# 更新手順: `facebook/mms-tts-*` の一覧と NLLB FLORES の基底コードの積を取り直す
# (`docs/design/feature-mms-multilingual/gen_lang_table.py` が生成スクリプト。
#  `common/languages.py` の名前表も追従)。
_MMS_LANGS: frozenset[str] = frozenset({
    "ace", "aka", "amh", "asm", "awa", "ayr", "azb", "bak", "bam", "ban", "bem",
    "ben", "bod", "bul", "cat", "ceb", "crh", "cym", "deu", "dik", "dyu", "dzo",
    "ell", "eng", "eus", "ewe", "fao", "fij", "fin", "fon", "fra", "grn", "guj",
    "hat", "hau", "heb", "hin", "hne", "hun", "ilo", "ind", "isl", "jav", "kab",
    "kac", "kan", "kaz", "kbp", "khm", "kik", "kin", "kir", "kor", "lao", "lug",
    "mag", "mai", "mal", "mar", "min", "mos", "mya", "nld", "nus", "nya", "ory",
    "pag", "pan", "pap", "pol", "por", "quy", "ron", "run", "rus", "sag", "shn",
    "smo", "sna", "som", "spa", "sun", "swe", "swh", "tam", "taq", "tat", "tel",
    "tgk", "tgl", "tha", "tir", "tpi", "tso", "tur", "ukr", "vie", "war", "yor",
})

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
        """対応する読み上げ言語を正準(ISO 639-3)で返す。

        `_MMS_LANGS` のうち言語テーブル(`LANGUAGE_NAMES`)で表示可能なものに限る
        (全コードに名前を用意しているので通常は全件)。未ロードでも答える必要があるため、
        ここでは transformers を import しない。
        """
        return sorted(c for c in _MMS_LANGS if c in LANGUAGE_NAMES)

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

        # device 解決は共有ヘルパに委ねる(配布方針「device 切替はコードパス1本」)。
        # auto → cuda → mps → cpu。明示指定(mps 含む)はそのまま通す。
        self._device = resolve_torch_device(self._device_pref)
        # エンジン準備完了(言語モデルはオンデマンド)。
        self._set_status(ModelStatus.LOADED)

    # ----------------------------------------------------------
    def prefetch_language(self, tgt_lang: str) -> None:
        """出力言語の確定時に裏で呼ぶ事前確保(DL + ロード)。

        会話中の初回発話で 100〜150MB の DL が走るとパイプラインが固まるため、GUI は
        出力言語の選択を契機にバックグラウンドでこれを呼ぶ。未対応言語は黙って no-op
        (`supported_output_languages` 外は事前確保しても使われない)。失敗は呼び出し側で握る。

        `tgt_lang` は正準(639-3)= MMS チェックポイント名。
        """
        lang = (tgt_lang or "").strip()
        if lang not in _MMS_LANGS:
            return
        self._ensure_language(lang)

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
        """`facebook/mms-tts-<code>` を DL → VitsModel/Tokenizer をロードする。

        `lang` は正準 639-3 = MMS チェックポイント名(両者は一致する集合に限定済み)。
        """
        if lang not in _MMS_LANGS:
            raise SkipError(f"MMS は出力言語 {lang} に対応していません")
        repo = _REPO_TEMPLATE.format(code=lang)

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

        # tgt_lang は正準(639-3)= MMS チェックポイント名。
        lang = (tgt_lang or "").strip()
        if lang not in _MMS_LANGS:
            raise SkipError(f"MMS は出力言語 {tgt_lang} に対応していません")

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
