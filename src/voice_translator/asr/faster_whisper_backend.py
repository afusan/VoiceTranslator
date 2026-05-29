"""FasterWhisperAsrBackend: faster-whisper による書き起こし。

役割: 発話単位の PCM(16kHz/mono/float32) を Whisper モデルで書き起こす。
タスクは transcribe 固定(translate は使わない — 翻訳は別レイヤの責務)。
device は "auto" / "cuda" / "cpu" を受け、利用可能ならアクセラレータを自動選択。
"""

from __future__ import annotations

from typing import Any

from voice_translator.common.cache_check import check_faster_whisper
from voice_translator.common.device import (
    resolve_ctranslate2_compute_type,
    resolve_ctranslate2_device,
)
from voice_translator.common.errors import FatalError, SkipError
from voice_translator.common.types import BackendCapabilities, ModelInfo, ModelStatus

from .backend import AsrBackend


# faster-whisper 推奨モデルの目安値(暫定)。GUI の選択ドロップダウン + リソース目安表示用。
# 正確な値はモデル/環境で変動するが、ユーザが「明らかにダメ」を回避できる程度の目安として使う。
_RECOMMENDED_MODELS: tuple[ModelInfo, ...] = (
    ModelInfo(
        name="tiny",
        display_name="tiny (~75MB, 軽量)",
        ram_gb=0.5,
        vram_gb_if_gpu=0.5,
        download_size_gb=0.08,
        target_proc_ms_per_sec_audio=50.0,
    ),
    ModelInfo(
        name="base",
        display_name="base (~140MB)",
        ram_gb=0.7,
        vram_gb_if_gpu=0.7,
        download_size_gb=0.15,
        target_proc_ms_per_sec_audio=80.0,
    ),
    ModelInfo(
        name="small",
        display_name="small (~460MB, 既定)",
        ram_gb=1.5,
        vram_gb_if_gpu=1.0,
        download_size_gb=0.46,
        target_proc_ms_per_sec_audio=150.0,
    ),
    ModelInfo(
        name="medium",
        display_name="medium (~1.5GB)",
        ram_gb=3.0,
        vram_gb_if_gpu=2.0,
        download_size_gb=1.5,
        target_proc_ms_per_sec_audio=300.0,
    ),
    ModelInfo(
        name="large-v3",
        display_name="large-v3 (~2.9GB, 高精度)",
        ram_gb=5.0,
        vram_gb_if_gpu=4.0,
        download_size_gb=2.9,
        target_proc_ms_per_sec_audio=600.0,
    ),
)


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
        super().__init__()  # BackendBase: status=INIT
        try:
            from faster_whisper import WhisperModel  # type: ignore
        except Exception as e:  # noqa: BLE001
            raise FatalError(f"faster-whisper のロードに失敗: {e}", cause=e) from e

        # device / compute_type の解決(auto → 実値)
        self._device = resolve_ctranslate2_device(device)
        self._compute_type = resolve_ctranslate2_compute_type(
            self._device, compute_type
        )

        # キャッシュ事前判定で DOWNLOADING / LOADING を出し分ける(R-3 / R2-1)。
        # WhisperModel コンストラクタ内で実際の DL + メモリ展開が走るため、
        # 中間状態は購読者(Phase A2 以降)が拾える前提で正しい遷移を残す。
        cache_status = check_faster_whisper(model_size)
        if cache_status == ModelStatus.LOADED:
            self._set_status(ModelStatus.LOADING)
        else:
            self._set_status(ModelStatus.DOWNLOADING)

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
                    self.record_error(e2, context="model load (cpu fallback)")
                    raise FatalError(
                        f"faster-whisper モデルの初期化に失敗 (size={model_size}): {e2}",
                        cause=e2,
                    ) from e2
            else:
                self.record_error(e, context="model load")
                raise FatalError(
                    f"faster-whisper モデルの初期化に失敗 (size={model_size}): {e}",
                    cause=e,
                ) from e

        self._model_size = model_size
        self._beam_size = beam_size
        self._set_status(ModelStatus.LOADED)

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
            is_cloud=False,
            requires_credentials=False,
            notes=f"faster-whisper model={self._model_size}, task=transcribe 固定",
        )

    def list_recommended_models(self) -> list[ModelInfo]:
        """Whisper の代表サイズ一覧を返す。"""
        return list(_RECOMMENDED_MODELS)
