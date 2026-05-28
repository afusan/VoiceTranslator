"""デフォルトのバックエンドを BackendRegistry に登録するヘルパ。

役割: アプリ起動時に MVP の標準バックエンド(soundcard/silero/faster-whisper/
NLLB-200/SAPI/soundcard)をレイヤごとに登録する。`config` を渡すと、各バックエンド
固有のパラメータ(SAPI rate 等)を `config.yaml` から読み込んで反映する。
後で別実装を追加するときはここを拡張する。
"""

from __future__ import annotations

from typing import Any

from .backend_registry import BackendRegistry
from .config_store import ConfigStore
from .types import LayerKind


def register_default_backends(
    registry: BackendRegistry,
    config: ConfigStore | None = None,
) -> None:
    """MVP の標準バックエンドを一括登録する。

    `config` を渡すと、SAPI rate などのバックエンド固有設定を反映する。
    渡さない場合は各バックエンドのコンストラクタ既定値が使われる。
    """
    from voice_translator.asr.faster_whisper_backend import FasterWhisperAsrBackend
    from voice_translator.capture.soundcard_backend import SoundcardCaptureBackend
    from voice_translator.output.soundcard_backend import SoundcardOutputBackend
    from voice_translator.translator.nllb200_backend import Nllb200TranslatorBackend
    from voice_translator.tts.sapi_backend import SapiTtsBackend
    from voice_translator.vad.silero_backend import SileroVadBackend

    registry.register(LayerKind.CAPTURE, "soundcard", SoundcardCaptureBackend)

    # Silero VAD は config から発話区切り関連パラメータを読み込む
    vad_threshold = _read_float(
        config, ("backends_config", "silero", "threshold"), default=0.5
    )
    vad_min_silence_ms = _read_int(
        config, ("backends_config", "silero", "min_silence_ms"), default=500
    )
    vad_speech_pad_ms = _read_int(
        config, ("backends_config", "silero", "speech_pad_ms"), default=100
    )
    vad_max_speech_sec = _read_float(
        config, ("backends_config", "silero", "max_speech_sec"), default=8.0
    )
    registry.register(
        LayerKind.VAD,
        "silero",
        lambda: SileroVadBackend(
            threshold=vad_threshold,
            min_silence_ms=vad_min_silence_ms,
            speech_pad_ms=vad_speech_pad_ms,
            max_speech_sec=vad_max_speech_sec,
        ),
    )
    registry.register(LayerKind.ASR, "faster_whisper", FasterWhisperAsrBackend)
    registry.register(LayerKind.TRANSLATOR, "nllb200", Nllb200TranslatorBackend)

    # SAPI は config から rate を取って渡す(設定なしなら既定 180)
    sapi_rate = _read_int(config, ("backends_config", "sapi", "rate"), default=180)
    registry.register(
        LayerKind.TTS, "sapi", lambda: SapiTtsBackend(rate=sapi_rate)
    )

    registry.register(LayerKind.OUTPUT, "soundcard", SoundcardOutputBackend)


def _read_int(config: ConfigStore | None, keys: tuple[str, ...], *, default: int) -> int:
    """config から int 値を取り出す。失敗時は default。"""
    if config is None:
        return default
    value: Any = config.get(*keys, default=default)
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _read_float(config: ConfigStore | None, keys: tuple[str, ...], *, default: float) -> float:
    """config から float 値を取り出す。失敗時は default。"""
    if config is None:
        return default
    value: Any = config.get(*keys, default=default)
    try:
        return float(value)
    except (TypeError, ValueError):
        return default
