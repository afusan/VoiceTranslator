"""OpenAiWhisperAsrBackend: openai-whisper(公式)による書き起こし。

役割: 発話単位の PCM(16kHz/mono/float32)を OpenAI 公式 Whisper(PyTorch 直)で
書き起こす。faster-whisper の代替/比較対象。タスクは transcribe 固定。
device は "auto" / "cuda" / "mps" / "cpu" を受け、利用可能ならアクセラレータを自動選択。

設計判断:
- faster-whisper と並走する別 backend として独立(I/F は同じ AsrBackend)
- 対応言語リストは `common/whisper_languages.py` から共有
- 重い whisper / torch の import は __init__ 内に閉じ込め、設定ダイアログだけで
  ロードが走らないようにする(supported_input_languages はクラスメソッド)
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from voice_translator.common.device import resolve_torch_device
from voice_translator.common.errors import FatalError, SkipError
from voice_translator.common.types import BackendCapabilities, ModelInfo, ModelStatus

from .backend import AsrBackend


# 推奨モデルの目安値(faster-whisper と概ね同等。CPU では openai-whisper の方が
# 重い傾向があるため target_proc_ms_per_sec_audio は少し大きめに設定)。
_RECOMMENDED_MODELS: tuple[ModelInfo, ...] = (
    ModelInfo(
        name="tiny",
        display_name="tiny (~75MB, 軽量)",
        ram_gb=1.0,
        vram_gb_if_gpu=1.0,
        download_size_gb=0.08,
        target_proc_ms_per_sec_audio=80.0,
    ),
    ModelInfo(
        name="base",
        display_name="base (~140MB)",
        ram_gb=1.2,
        vram_gb_if_gpu=1.0,
        download_size_gb=0.15,
        target_proc_ms_per_sec_audio=120.0,
    ),
    ModelInfo(
        name="small",
        display_name="small (~460MB, 既定)",
        ram_gb=2.0,
        vram_gb_if_gpu=2.0,
        download_size_gb=0.46,
        target_proc_ms_per_sec_audio=250.0,
    ),
    ModelInfo(
        name="medium",
        display_name="medium (~1.5GB)",
        ram_gb=5.0,
        vram_gb_if_gpu=5.0,
        download_size_gb=1.5,
        target_proc_ms_per_sec_audio=500.0,
    ),
    ModelInfo(
        name="large-v3",
        display_name="large-v3 (~2.9GB, 高精度)",
        ram_gb=10.0,
        vram_gb_if_gpu=10.0,
        download_size_gb=2.9,
        target_proc_ms_per_sec_audio=1000.0,
    ),
)


def _check_openai_whisper_cache(model_size: str) -> ModelStatus:
    """openai-whisper のキャッシュフォルダにモデルファイルがあるかで状態を返す。

    既定キャッシュ: `~/.cache/whisper/<model_size>.pt`
    存在すれば LOADED 相当(=次の遷移は LOADING)、無ければ NOT_DOWNLOADED 相当
    (=次の遷移は DOWNLOADING)。
    """
    cache_path = Path.home() / ".cache" / "whisper" / f"{model_size}.pt"
    if cache_path.exists() and cache_path.stat().st_size > 0:
        return ModelStatus.LOADED
    return ModelStatus.NOT_DOWNLOADED


class OpenAiWhisperAsrBackend(AsrBackend):
    """openai-whisper(公式)を使った書き起こしバックエンド。

    役割: 初期化時にモデルをロードし、transcribe(pcm, hint) で
    (text, lang) を返す。初回は大きなモデル DL が走るので時間がかかる。
    """

    def __init__(
        self,
        *,
        model_size: str = "small",
        device: str = "auto",
    ) -> None:
        super().__init__()  # BackendBase: status=INIT
        try:
            import whisper  # type: ignore
        except Exception as e:  # noqa: BLE001
            raise FatalError(
                f"openai-whisper のロードに失敗(`uv pip install openai-whisper` を確認): {e}",
                cause=e,
            ) from e

        # device 解決(auto → cuda/mps/cpu)
        self._device = resolve_torch_device(device)

        # キャッシュ事前判定で DOWNLOADING / LOADING を出し分ける
        cache_status = _check_openai_whisper_cache(model_size)
        if cache_status == ModelStatus.LOADED:
            self._set_status(ModelStatus.LOADING)
        else:
            self._set_status(ModelStatus.DOWNLOADING)

        try:
            self._model = whisper.load_model(model_size, device=self._device)
        except Exception as e:  # noqa: BLE001
            # GPU 未利用環境で失敗した場合の保険: CPU へフォールバック
            if self._device != "cpu":
                try:
                    self._device = "cpu"
                    self._model = whisper.load_model(model_size, device="cpu")
                except Exception as e2:  # noqa: BLE001
                    self.record_error(e2, context="model load (cpu fallback)")
                    raise FatalError(
                        f"openai-whisper モデルの初期化に失敗 (size={model_size}): {e2}",
                        cause=e2,
                    ) from e2
            else:
                self.record_error(e, context="model load")
                raise FatalError(
                    f"openai-whisper モデルの初期化に失敗 (size={model_size}): {e}",
                    cause=e,
                ) from e

        self._model_size = model_size
        self._set_status(ModelStatus.LOADED)

    @property
    def device(self) -> str:
        """実際に使用しているデバイス名(診断/テスト用)。"""
        return self._device

    # ----------------------------------------------------------
    def transcribe(self, pcm: Any, src_lang_hint: str = "auto") -> tuple[str, str]:
        """pcm を書き起こし (text, lang) を返す。"""
        if pcm is None or (hasattr(pcm, "size") and pcm.size == 0):
            raise SkipError("ASR入力PCMが空です")

        language = None if src_lang_hint in ("auto", "", None) else src_lang_hint
        try:
            # openai-whisper の transcribe は dict を返す。
            # task="transcribe" 固定(翻訳は別レイヤの責務)。
            kwargs: dict[str, Any] = {"task": "transcribe", "fp16": self._device != "cpu"}
            if language is not None:
                kwargs["language"] = language
            result = self._model.transcribe(pcm, **kwargs)
        except Exception as e:  # noqa: BLE001
            raise FatalError(f"openai-whisper 推論失敗: {e}", cause=e) from e

        text = str(result.get("text", "")).strip()
        if src_lang_hint in ("auto", "", None):
            detected = result.get("language") or ""
            lang_out = detected or "auto"
        else:
            lang_out = src_lang_hint
        return text, lang_out

    # ----------------------------------------------------------
    def capabilities(self) -> BackendCapabilities:
        return BackendCapabilities(
            supported_languages=(),
            requires_gpu=False,
            is_cloud=False,
            requires_credentials=False,
            notes=f"openai-whisper (公式) model={self._model_size}, task=transcribe 固定",
        )

    def list_recommended_models(self) -> list[ModelInfo]:
        return self.recommended_models()

    @classmethod
    def recommended_models(cls) -> list[ModelInfo]:
        """インスタンス無しでも推奨モデル一覧を引ける(GUI 詳細ダイアログ用)。"""
        return list(_RECOMMENDED_MODELS)

    # ----------------------------------------------------------
    # 対応言語の宣言(UI の言語プルダウン連動用)
    # ----------------------------------------------------------
    @classmethod
    def supported_input_languages(cls) -> list[str]:
        from voice_translator.common.whisper_languages import WHISPER_INPUT_LANGUAGES
        return list(WHISPER_INPUT_LANGUAGES)

    @classmethod
    def supports_auto_detect(cls) -> bool:
        return True
