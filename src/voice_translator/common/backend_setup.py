"""デフォルトのバックエンドを BackendRegistry に登録するヘルパ。

役割: アプリ起動時に MVP の標準バックエンド(soundcard/silero/faster-whisper/
NLLB-200/SAPI/soundcard)をレイヤごとに登録する。後で別実装を追加するときは
ここを拡張する。
"""

from __future__ import annotations

from .backend_registry import BackendRegistry
from .types import LayerKind


def register_default_backends(registry: BackendRegistry) -> None:
    """MVP の標準バックエンドを一括登録する。

    各クラスのコンストラクタは引数なしで呼べる(設定は外から渡さず既定値)。
    実引数を渡したい場合はラッパファクトリで登録すること。
    """
    from voice_translator.asr.faster_whisper_backend import FasterWhisperAsrBackend
    from voice_translator.capture.soundcard_backend import SoundcardCaptureBackend
    from voice_translator.output.soundcard_backend import SoundcardOutputBackend
    from voice_translator.translator.nllb200_backend import Nllb200TranslatorBackend
    from voice_translator.tts.sapi_backend import SapiTtsBackend
    from voice_translator.vad.silero_backend import SileroVadBackend

    registry.register(LayerKind.CAPTURE, "soundcard", SoundcardCaptureBackend)
    registry.register(LayerKind.VAD, "silero", SileroVadBackend)
    registry.register(LayerKind.ASR, "faster_whisper", FasterWhisperAsrBackend)
    registry.register(LayerKind.TRANSLATOR, "nllb200", Nllb200TranslatorBackend)
    registry.register(LayerKind.TTS, "sapi", SapiTtsBackend)
    registry.register(LayerKind.OUTPUT, "soundcard", SoundcardOutputBackend)
