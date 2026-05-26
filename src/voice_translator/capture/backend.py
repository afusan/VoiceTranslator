"""AudioCaptureBackend 抽象基底。

役割: 音声デバイスから PCM チャンクを取得し、内部標準フォーマット
(16kHz/mono/float32) に正規化してパイプラインに供給するレイヤの I/F。
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from voice_translator.common.types import (
    BackendCapabilities,
    CaptureSource,
    PcmChunk,
)


class AudioCaptureBackend(ABC):
    """音声取得バックエンドの抽象基底。

    実装は OS/ライブラリ別に作る(MVPは soundcard ベース)。
    """

    @abstractmethod
    def list_sources(self) -> list[CaptureSource]:
        """取得可能なソース(デバイス/プロセス等)を列挙する。"""

    @abstractmethod
    def start(self, source_id: str) -> None:
        """指定ソースからの取得を開始する。冪等ではない(stop 後に再 start 可)。"""

    @abstractmethod
    def read_chunk(self, timeout: float = 0.1) -> PcmChunk | None:
        """次のPCMチャンクを返す。

        - 戻り値は 16kHz / mono / float32 の np.ndarray(リサンプル済み)。
        - timeout 内にチャンクが揃わなければ None を返す。
        - start 前/stop 後に呼ばれた場合は RuntimeError。
        """

    @abstractmethod
    def stop(self) -> None:
        """取得を停止する。複数回呼ばれても安全。"""

    def capabilities(self) -> BackendCapabilities:
        """このバックエンドのメタ情報。既定は空。必要に応じてオーバーライド。"""
        return BackendCapabilities()
