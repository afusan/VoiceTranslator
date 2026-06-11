"""FasterWhisperTranslateBackend: faster-whisper の task=translate による ASR+翻訳複合。

役割: 発話 PCM を 1 回の Whisper 推論で「書き起こし + 英語へ翻訳」する複合バックエンド
(ASR + Translator の 2 ロールを担う)。モデルのロード・device 解決は
`FasterWhisperAsrBackend` をそのまま継承する。

制約(Whisper translate の仕様):
- 翻訳先は **英語固定**(`supported_target_languages() == ["en"]`)。
- 源言語テキストは出力されない(`src_text` は空文字)。翻訳結果のみが得られる。
"""

from __future__ import annotations

from typing import Any

from voice_translator.common.errors import FatalError, SkipError
from voice_translator.common.messages import PayloadKind
from voice_translator.common.types import BackendCapabilities, LayerKind

from .backend import AsrTranslatorBackend
from .faster_whisper_backend import FasterWhisperAsrBackend


class FasterWhisperTranslateBackend(FasterWhisperAsrBackend, AsrTranslatorBackend):
    """faster-whisper(task=translate)で ASR+翻訳を一括実行する複合バックエンド。

    `FasterWhisperAsrBackend` からモデル管理(ロード / device / 推奨モデル一覧)を、
    `AsrTranslatorBackend` から複合の契約(transcribe_translate / 編成申告)を継承する。
    """

    # ---- 複合の契約 ----
    def transcribe_translate(
        self, pcm: Any, src_lang_hint: str = "auto", tgt_lang: str = "en"
    ) -> tuple[str, str, str, str]:
        """1 回の推論で書き起こし + 英語翻訳。(src_text="", src_lang, tgt_text, "en")。

        `tgt_lang` は無視する(Whisper translate は英語固定。UI 側は
        `supported_target_languages()` により "en" 以外を選ばせない)。
        """
        if pcm is None or (hasattr(pcm, "size") and pcm.size == 0):
            raise SkipError("ASR入力PCMが空です")

        language = None if src_lang_hint in ("auto", "", None) else src_lang_hint
        try:
            segments_iter, info = self._model.transcribe(
                pcm,
                language=language,
                task="translate",
                beam_size=self._beam_size,
            )
            text = " ".join(seg.text.strip() for seg in segments_iter if seg.text)
        except Exception as e:  # noqa: BLE001
            raise FatalError(f"faster-whisper 推論失敗: {e}", cause=e) from e

        if src_lang_hint in ("auto", "", None):
            detected = getattr(info, "language", None) or ""
            src_lang = detected or "auto"
        else:
            src_lang = src_lang_hint
        return "", src_lang, text.strip(), "en"

    @classmethod
    def supported_target_languages(cls) -> list[str]:
        """Whisper translate は英語のみ。"""
        return ["en"]

    # ---- 編成申告(MRO 先頭の FasterWhisperAsrBackend(単体 ASR)を上書き) ----
    @classmethod
    def covers_roles(cls) -> tuple[LayerKind, ...]:
        return (LayerKind.ASR, LayerKind.TRANSLATOR)

    @classmethod
    def produces_payload(cls) -> PayloadKind:
        return PayloadKind.TRANSLATED

    # ---- メタ情報 ----
    def capabilities(self) -> BackendCapabilities:
        return BackendCapabilities(
            supported_languages=(),
            requires_gpu=False,
            is_cloud=False,
            requires_credentials=False,
            notes=(
                f"faster-whisper model={self._model_size}, task=translate"
                "(ASR+翻訳の複合、英語固定)"
            ),
        )
