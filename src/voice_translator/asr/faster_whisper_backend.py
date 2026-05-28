"""FasterWhisperAsrBackend: faster-whisper による書き起こし。

役割: 発話単位の PCM(16kHz/mono/float32) を Whisper モデルで書き起こす。
タスクは transcribe 固定(translate は使わない — 翻訳は別レイヤの責務)。
device は "auto" / "cuda" / "cpu" を受け、利用可能ならアクセラレータを自動選択。
"""

from __future__ import annotations

from typing import Any

from voice_translator.common.device import (
    resolve_ctranslate2_compute_type,
    resolve_ctranslate2_device,
)
from voice_translator.common.errors import FatalError, SkipError
from voice_translator.common.types import BackendCapabilities

from .backend import AsrBackend


class FasterWhisperAsrBackend(AsrBackend):
    """faster-whisper を使った書き起こしバックエンド。

    役割: 初期化時にモデルをロードし、transcribe(pcm, hint) で
    (text, lang) を返す。初回は大きなモデルDLが走るので時間がかかる。
    """

    def __init__(
        self,
        *,
        model_size: str = "small",
        device: str = "auto",
        compute_type: str = "auto",
        beam_size: int = 1,
    ) -> None:
        try:
            from faster_whisper import WhisperModel  # type: ignore
        except Exception as e:  # noqa: BLE001
            raise FatalError(f"faster-whisper のロードに失敗: {e}", cause=e) from e

        # device / compute_type の解決(auto → 実値)
        self._device = resolve_ctranslate2_device(device)
        self._compute_type = resolve_ctranslate2_compute_type(
            self._device, compute_type
        )

        try:
            self._model = WhisperModel(
                model_size, device=self._device, compute_type=self._compute_type
            )
        except Exception as e:  # noqa: BLE001
            # GPU 未利用環境 + compute_type=float16 などで失敗した場合の保険:
            # CPU + int8 へフォールバックして再試行
            if self._device != "cpu":
                try:
                    self._device = "cpu"
                    self._compute_type = "int8"
                    self._model = WhisperModel(
                        model_size, device="cpu", compute_type="int8"
                    )
                except Exception as e2:  # noqa: BLE001
                    raise FatalError(
                        f"faster-whisper モデルの初期化に失敗 (size={model_size}): {e2}",
                        cause=e2,
                    ) from e2
            else:
                raise FatalError(
                    f"faster-whisper モデルの初期化に失敗 (size={model_size}): {e}",
                    cause=e,
                ) from e

        self._model_size = model_size
        self._beam_size = beam_size

    @property
    def device(self) -> str:
        """実際に使用しているデバイス名(診断/テスト用)。"""
        return self._device

    @property
    def compute_type(self) -> str:
        """実際に使用している compute_type(診断/テスト用)。"""
        return self._compute_type

    # ----------------------------------------------------------
    def transcribe(self, pcm: Any, src_lang_hint: str = "auto") -> tuple[str, str]:
        """pcm を書き起こし (text, lang) を返す。"""
        if pcm is None or (hasattr(pcm, "size") and pcm.size == 0):
            raise SkipError("ASR入力PCMが空です")

        language = None if src_lang_hint in ("auto", "", None) else src_lang_hint
        try:
            segments_iter, info = self._model.transcribe(
                pcm,
                language=language,
                task="transcribe",
                beam_size=self._beam_size,
            )
            # segments_iter はジェネレータ。すべて取得して連結。
            text = " ".join(seg.text.strip() for seg in segments_iter if seg.text)
        except Exception as e:  # noqa: BLE001
            raise FatalError(f"faster-whisper 推論失敗: {e}", cause=e) from e

        text = text.strip()
        if src_lang_hint in ("auto", "", None):
            detected = getattr(info, "language", None) or ""
            lang_out = detected or "auto"
        else:
            lang_out = src_lang_hint
        return text, lang_out

    # ----------------------------------------------------------
    def capabilities(self) -> BackendCapabilities:
        return BackendCapabilities(
            supported_languages=(),  # Whisper は約100言語対応。明示列挙は省略。
            requires_gpu=False,      # int8/CPU で動作。GPUにすれば高速。
            notes=f"faster-whisper model={self._model_size}, task=transcribe 固定",
        )
