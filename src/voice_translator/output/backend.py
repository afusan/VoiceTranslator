"""AudioOutputBackend 抽象基底。

役割: 合成 PCM を指定された出力デバイスで再生する I/F。
入力デバイスと異なるデバイスが選ばれている前提(DeviceValidator で保証)。
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from voice_translator.common.types import BackendCapabilities, OutputDevice
from voice_translator.common.utterance import Utterance


class AudioOutputBackend(ABC):
    """音声出力バックエンドの抽象基底。

    実装は soundcard 等(MVPは soundcard)。
    """

    @abstractmethod
    def list_devices(self) -> list[OutputDevice]:
        """利用可能な出力デバイスを列挙する。"""

    @abstractmethod
    def start(self, device_id: str) -> None:
        """指定デバイスへの再生セッションを開く。"""

    @abstractmethod
    def play(self, utterance: Utterance) -> None:
        """utterance.tts_pcm を再生する。同期/ブロッキングは実装依存。"""

    @abstractmethod
    def stop(self) -> None:
        """再生セッションを閉じる。複数回呼ばれても安全。"""

    def capabilities(self) -> BackendCapabilities:
        """このバックエンドのメタ情報。既定は空。"""
        return BackendCapabilities()
