"""FasterWhisperAsrBackend: faster-whisper による書き起こし。

役割: 発話単位の PCM(16kHz/mono/float32) を Whisper モデルで書き起こす。
タスクは transcribe 固定(translate は使わない — 翻訳は別レイヤの責務)。
"""

from __future__ import annotations

from voice_translator.common.errors import FatalError, SkipError
from voice_translator.common.types import BackendCapabilities
from voice_translator.common.utterance import Utterance

from .backend import AsrBackend


class FasterWhisperAsrBackend(AsrBackend):
    """faster-whisper を使った書き起こしバックエンド。

    役割: 初期化時にモデルをロードし、transcribe で utterance.pcm を
    src_text に変換する。初回は大きなモデルDLが走るので時間がかかる。
    """

    def __init__(
        self,
        *,
        model_size: str = "small",
        device: str = "cpu",
        compute_type: str = "int8",
        beam_size: int = 1,
    ) -> None:
        try:
            from faster_whisper import WhisperModel  # type: ignore
        except Exception as e:  # noqa: BLE001
            raise FatalError(f"faster-whisper のロードに失敗: {e}", cause=e) from e

        try:
            self._model = WhisperModel(
                model_size, device=device, compute_type=compute_type
            )
        except Exception as e:  # noqa: BLE001
            raise FatalError(
                f"faster-whisper モデルの初期化に失敗 (size={model_size}): {e}",
                cause=e,
            ) from e

        self._model_size = model_size
        self._beam_size = beam_size

    # ----------------------------------------------------------
    def transcribe(self, utterance: Utterance, src_lang: str = "auto") -> Utterance:
        """utterance.pcm を書き起こし、src_text/src_lang を埋めて返す。"""
        if utterance.pcm is None or (hasattr(utterance.pcm, "size") and utterance.pcm.size == 0):
            raise SkipError("ASR入力PCMが空です")

        language = None if src_lang in ("auto", "", None) else src_lang
        try:
            segments_iter, info = self._model.transcribe(
                utterance.pcm,
                language=language,
                task="transcribe",
                beam_size=self._beam_size,
            )
            # segments_iter はジェネレータ。すべて取得して連結。
            text = " ".join(seg.text.strip() for seg in segments_iter if seg.text)
        except Exception as e:  # noqa: BLE001
            raise FatalError(f"faster-whisper 推論失敗: {e}", cause=e) from e

        utterance.src_text = text.strip()
        if src_lang in ("auto", "", None):
            detected = getattr(info, "language", None)
            if detected:
                utterance.src_lang = detected
        return utterance

    # ----------------------------------------------------------
    def capabilities(self) -> BackendCapabilities:
        return BackendCapabilities(
            supported_languages=(),  # Whisper は約100言語対応。明示列挙は省略。
            requires_gpu=False,      # int8/CPU で動作。GPUにすれば高速。
            notes=f"faster-whisper model={self._model_size}, task=transcribe 固定",
        )
