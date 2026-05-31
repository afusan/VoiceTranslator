"""PiperTtsBackend: Piper TTS(ONNX 軽量ローカル)。

役割: voice モデル(.onnx + .onnx.json)を Hugging Face `rhasspy/piper-voices`
から取得し、テキストを synthesize して float32 PCM を返す。
CPU で 1 秒未満の応答性、マルチ OS(Windows/Mac/Linux)で動作する
ローカル TTS の主軸。
"""

from __future__ import annotations

from typing import Any

import numpy as np

from voice_translator.common.errors import FatalError, SkipError
from voice_translator.common.types import BackendCapabilities, ModelStatus

from .backend import TtsBackend

# layer_settings_schema の dropdown 候補として使う代表 voice 群。
# 各 voice は `<lang_country>-<speaker>-<quality>` 形式で、
# `rhasspy/piper-voices` の `<lang>/<lang_country>/<speaker>/<quality>/` に置かれている。
RECOMMENDED_VOICES: tuple[str, ...] = (
    "en_US-amy-low",
    "en_US-amy-medium",
    "en_US-libritts-high",
    "en_GB-alan-low",
    "de_DE-thorsten-low",
    "de_DE-thorsten-medium",
    "fr_FR-siwis-low",
    "es_ES-mls_9972-low",
    "it_IT-riccardo-x_low",
    "zh_CN-huayan-medium",
    "ru_RU-ruslan-medium",
)

_DEFAULT_VOICE = "en_US-amy-low"


class PiperTtsBackend(TtsBackend):
    """Piper TTS バックエンド(ローカル / 無認証 / マルチ OS)。

    役割: PiperVoice を遅延ロードし、synthesize() でテキストから
    int16 PCM を生成 → float32 に変換して (pcm, samplerate) を返す。
    voice モデルは初回利用時に HF からダウンロード(以後は HF キャッシュ)。
    """

    @classmethod
    def supported_output_languages(cls) -> list[str]:
        """`rhasspy/piper-voices` で配布される主要言語(ISO 639-1)。

        注意: 日本語(ja)は piper-voices に標準配布されていない
        (2026-05 時点)。日本語 TTS は SAPI / OpenAI / Google / ElevenLabs を使う。
        他言語 voice を独自にダウンロード/学習している環境でも
        宣言ベースのため一律 false 表示になるリスクは受容。
        """
        return [
            "ar", "ca", "cs", "cy", "da", "de", "el", "en", "es", "fa", "fi",
            "fr", "hu", "is", "it", "ka", "kk", "lb", "ne", "nl", "no", "pl",
            "pt", "ro", "ru", "sk", "sl", "sr", "sv", "sw", "tr", "uk", "vi",
            "zh",
        ]

    def __init__(
        self,
        *,
        voice_name: str = _DEFAULT_VOICE,
        device: str = "auto",
    ) -> None:
        super().__init__()  # BackendBase: status=INIT
        self._set_status(ModelStatus.LOADING)
        self._voice_name = voice_name
        self._device = device

        # 遅延 import(extras `tts-piper` 未インストール環境で死なないため)
        try:
            from piper import PiperVoice  # type: ignore  # noqa: F401
        except Exception as e:  # noqa: BLE001
            self.record_error(e, context="piper import")
            raise FatalError(
                f"piper-tts のロードに失敗: {e}. "
                "`uv sync --extra tts-piper` でインストールしてください",
                cause=e,
            ) from e

        try:
            self._voice = self._load_voice(voice_name)
        except FatalError:
            raise
        except Exception as e:  # noqa: BLE001
            self.record_error(e, context="piper voice load")
            raise FatalError(
                f"Piper voice ロード失敗 ({voice_name}): {e}", cause=e,
            ) from e

        # voice.config.sample_rate は voice モデルの設定から取れる
        try:
            self._samplerate = int(self._voice.config.sample_rate)
        except Exception:  # noqa: BLE001
            self._samplerate = 22050  # piper の一般的な既定

        self._set_status(ModelStatus.LOADED)

    # ----------------------------------------------------------
    def _load_voice(self, voice_name: str) -> Any:
        """HF `rhasspy/piper-voices` から voice ファイルを DL → PiperVoice.load。

        voice_name は `"<lang_country>-<speaker>-<quality>"` 形式
        (例: `"en_US-amy-low"`)。
        HF パス: `<lang>/<lang_country>/<speaker>/<quality>/<voice_name>.onnx` + `.onnx.json`。
        """
        from huggingface_hub import hf_hub_download  # type: ignore
        from piper import PiperVoice  # type: ignore

        parts = voice_name.split("-")
        if len(parts) < 3:
            raise FatalError(
                f"voice_name の形式が不正: {voice_name} "
                "(`<lang_country>-<speaker>-<quality>` 形式が必要)"
            )
        lang_country = parts[0]
        speaker = parts[1]
        quality = parts[2]
        lang = lang_country.split("_")[0]
        hf_dir = f"{lang}/{lang_country}/{speaker}/{quality}"

        self._set_status(ModelStatus.DOWNLOADING)
        try:
            onnx_local = hf_hub_download(
                repo_id="rhasspy/piper-voices",
                filename=f"{hf_dir}/{voice_name}.onnx",
            )
            hf_hub_download(
                repo_id="rhasspy/piper-voices",
                filename=f"{hf_dir}/{voice_name}.onnx.json",
            )
        except Exception as e:  # noqa: BLE001
            raise FatalError(
                f"Piper voice DL 失敗 ({voice_name}): {e}", cause=e,
            ) from e

        self._set_status(ModelStatus.LOADING)
        return PiperVoice.load(onnx_local, use_cuda=(self._device == "cuda"))

    # ----------------------------------------------------------
    def synthesize(self, text: str, tgt_lang: str) -> tuple[np.ndarray, int]:
        """テキストを Piper voice で合成し、(float32 PCM, samplerate) を返す。"""
        text = (text or "").strip()
        if not text:
            raise SkipError("TTS入力テキストが空です")

        try:
            # piper の synthesize_stream_raw は int16 mono PCM bytes を yield
            audio_bytes = b"".join(self._voice.synthesize_stream_raw(text))
        except Exception as e:  # noqa: BLE001
            raise FatalError(f"Piper 合成失敗: {e}", cause=e) from e

        if not audio_bytes:
            raise SkipError("Piper の出力が空です")

        pcm = np.frombuffer(audio_bytes, dtype=np.int16).astype(np.float32) / 32768.0
        return pcm, self._samplerate

    # ----------------------------------------------------------
    def capabilities(self) -> BackendCapabilities:
        return BackendCapabilities(
            is_cloud=False,
            requires_credentials=False,
            notes=(
                f"Piper TTS (ONNX). voice={self._voice_name}, "
                f"sr={self._samplerate}, device={self._device}"
            ),
        )
