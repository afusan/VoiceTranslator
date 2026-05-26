"""SoundcardOutputBackend: soundcard ライブラリを使った音声再生。

役割: 指定された出力デバイスへ、Utterance.tts_pcm を再生する。
PCM のサンプルレートは Utterance.tts_samplerate を参照する。
入力レイヤと別デバイスが選ばれている前提(DeviceValidator で保証)。
"""

from __future__ import annotations

import numpy as np
import soundcard as sc

from voice_translator.common.errors import FatalError, SkipError
from voice_translator.common.types import (
    INTERNAL_CHANNELS,
    INTERNAL_SAMPLE_RATE,
    BackendCapabilities,
    OutputDevice,
)
from voice_translator.common.utterance import Utterance

from .backend import AudioOutputBackend


class SoundcardOutputBackend(AudioOutputBackend):
    """soundcard ベースの AudioOutputBackend。

    役割: スピーカデバイスを列挙し、選ばれたデバイスへ Utterance の TTS 音声を流す。
    再生時のサンプルレート/チャネル数は Utterance のメタ情報に従う。
    """

    def __init__(self) -> None:
        self._speaker: sc._Speaker | None = None  # type: ignore[name-defined]

    # ----------------------------------------------------------
    def list_devices(self) -> list[OutputDevice]:
        return [
            OutputDevice(device_id=str(spk.id), display_name=spk.name)
            for spk in sc.all_speakers()
        ]

    # ----------------------------------------------------------
    def start(self, device_id: str) -> None:
        """指定 device_id のスピーカを保持する。

        player は再生時に毎回オープンする(TTSのサンプルレートが発話ごとに違う可能性に対応)。
        """
        for spk in sc.all_speakers():
            if str(spk.id) == device_id:
                self._speaker = spk
                return
        raise FatalError(f"指定された出力デバイスが見つかりません: {device_id}")

    # ----------------------------------------------------------
    def play(self, utterance: Utterance) -> None:
        """utterance.tts_pcm を utterance.tts_samplerate で再生する。

        - tts_pcm が None または空なら SKIP として何もせず例外を出す。
        - tts_samplerate が 0 の場合は内部標準 (16kHz) と仮定する。
        - 1次元(mono)/ 2次元(numframes,channels) のどちらも受け付ける。
        """
        if self._speaker is None:
            raise RuntimeError("start() を呼んでから play() してください")

        pcm = utterance.tts_pcm
        if pcm is None or (hasattr(pcm, "size") and pcm.size == 0):
            raise SkipError("再生対象の TTS PCM が空です")

        if not isinstance(pcm, np.ndarray):
            raise FatalError(f"tts_pcm は np.ndarray が必要: 受領={type(pcm).__name__}")

        samplerate = utterance.tts_samplerate or INTERNAL_SAMPLE_RATE
        channels = pcm.shape[1] if pcm.ndim == 2 else INTERNAL_CHANNELS

        try:
            with self._speaker.player(samplerate=samplerate, channels=channels) as player:
                player.play(pcm.astype(np.float32, copy=False))
        except Exception as e:  # noqa: BLE001 - デバイス問題は FATAL
            raise FatalError(f"音声再生に失敗: {e}", cause=e) from e

    # ----------------------------------------------------------
    def stop(self) -> None:
        """セッションを閉じる。本実装ではデバイス参照を解放するだけ。"""
        self._speaker = None

    # ----------------------------------------------------------
    def capabilities(self) -> BackendCapabilities:
        return BackendCapabilities(
            requires_gpu=False,
            notes="soundcard ベース。複数サンプルレートを発話ごとに切替可能。",
        )
