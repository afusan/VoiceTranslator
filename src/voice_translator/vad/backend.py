"""VadBackend 抽象基底。

役割: PCM ストリームから発話区間を検出し、発話単位 (Utterance) に切り出す I/F。
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from voice_translator.common.types import BackendCapabilities, PcmChunk
from voice_translator.common.utterance import Utterance


class VadBackend(ABC):
    """発話区切り検出の抽象基底。

    実装は Silero-VAD 等(MVPは silero)。
    """

    @abstractmethod
    def process(self, chunk: PcmChunk) -> list[Utterance]:
        """1チャンクを投入し、確定した発話を返す(0個以上)。

        まだ発話が途中なら空リスト。チャンクの末尾を超えて確定した時点で
        Utterance(pcm 付き, t_capture/t_vad_end 記録済み) を返す。
        """

    @abstractmethod
    def reset(self) -> None:
        """内部状態(進行中の発話バッファ等)をリセットする。"""

    def capabilities(self) -> BackendCapabilities:
        """このバックエンドのメタ情報。既定は空。"""
        return BackendCapabilities()
