"""AudioOutputBackend 抽象基底。

役割: 合成 PCM を指定された出力デバイスで再生する I/F。
入力デバイスと異なるデバイスが選ばれている前提(DeviceValidator で保証)。

R-2 でプリミティブ I/F に変更: Utterance 依存をやめ、(pcm, samplerate) を受ける。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from voice_translator.common.backend_base import BackendBase
from voice_translator.common.types import BackendCapabilities, OutputDevice


class AudioOutputBackend(BackendBase, ABC):
    """音声出力バックエンドの抽象基底。

    実装は soundcard 等(MVPは soundcard)。
    `BackendBase` から状態管理/購読/エラー履歴の機能を継承する。
    """

    @abstractmethod
    def list_devices(self) -> list[OutputDevice]:
        """利用可能な出力デバイスを列挙する。"""

    @abstractmethod
    def start(self, device_id: str) -> None:
        """指定デバイスへの再生セッションを開く。"""

    @abstractmethod
    def play(self, pcm: Any, samplerate: int) -> None:
        """`pcm` を `samplerate` Hz で再生する。同期/ブロッキングは実装依存。

        - pcm: np.ndarray(1次元 mono か (N, ch))、dtype は実装側で float32 化。
              None や空は SkipError(発話単位の破棄)。
        - samplerate: 0 以下は内部標準 (16kHz) と仮定する実装でよい。
        """

    @abstractmethod
    def stop(self) -> None:
        """再生セッションを閉じる。複数回呼ばれても安全。"""

    def capabilities(self) -> BackendCapabilities:
        """このバックエンドのメタ情報。既定は空。"""
        return BackendCapabilities()
