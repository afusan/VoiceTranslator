"""SoundcardOutputBackend: soundcard ライブラリを使った音声再生。

役割: 指定された出力デバイスへ、与えられた PCM を再生する。
入力レイヤと別デバイスが選ばれている前提(DeviceValidator で保証)。
"""

from __future__ import annotations

from typing import Any

import numpy as np
import soundcard as sc

from voice_translator.common.errors import FatalError, SkipError
from voice_translator.common.types import (
    INTERNAL_CHANNELS,
    INTERNAL_SAMPLE_RATE,
    BackendCapabilities,
    OutputDevice,
)

from .backend import AudioOutputBackend


class SoundcardOutputBackend(AudioOutputBackend):
    """soundcard ベースの AudioOutputBackend。

    役割: スピーカデバイスを列挙し、選ばれたデバイスへ TTS 音声を流す。
    再生時のサンプルレート/チャネル数は呼び出し時の引数に従う。
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
    def play(self, pcm: Any, samplerate: int) -> None:
        """pcm を samplerate Hz で再生する。

        - pcm が None または空なら SkipError。
        - pcm が np.ndarray でない場合は FatalError。
        - 1次元(mono)/ 2次元(numframes,channels) のどちらも受け付ける。
        - samplerate が 0 以下なら内部標準 (16kHz) を使う。
        """
        if self._speaker is None:
            raise RuntimeError("start() を呼んでから play() してください")

        if pcm is None or (hasattr(pcm, "size") and pcm.size == 0):
            raise SkipError("再生対象の TTS PCM が空です")

        if not isinstance(pcm, np.ndarray):
            raise FatalError(f"tts_pcm は np.ndarray が必要: 受領={type(pcm).__name__}")

        sr = samplerate if samplerate and samplerate > 0 else INTERNAL_SAMPLE_RATE
        channels = pcm.shape[1] if pcm.ndim == 2 else INTERNAL_CHANNELS

        try:
            with self._speaker.player(samplerate=sr, channels=channels) as player:
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
