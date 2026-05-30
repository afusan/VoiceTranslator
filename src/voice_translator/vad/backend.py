"""VadBackend 抽象基底。

役割: PCM ストリームから発話区間を検出し、発話単位の PCM セグメントに切り出す I/F。

R-3 でプリミティブ I/F に変更: Utterance ではなく `VadSegment(pcm, started_at)` を返す。
横断メタは UtteranceLedger 側で seq_id をキーに集約する。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

from voice_translator.common.backend_base import BackendBase
from voice_translator.common.types import BackendCapabilities, PcmChunk


@dataclass(frozen=True)
class VadSegment:
    """VAD が確定した1発話分の PCM + 発話開始時刻(monotonic)。

    役割: Input スレッドが ledger に t_capture を記録するために、
    VAD 検出時点の正確な「発話開始時刻」を一緒に運ぶ。
    """

    pcm: Any                  # np.ndarray[float32]
    started_at_monotonic: float


class VadBackend(BackendBase, ABC):
    """発話区切り検出の抽象基底。

    実装は Silero-VAD 等(MVPは silero)。
    `BackendBase` から状態管理/購読/エラー履歴の機能を継承する。
    """

    @abstractmethod
    def process(self, chunk: PcmChunk) -> list[VadSegment]:
        """1チャンクを投入し、確定した発話を返す(0個以上)。

        まだ発話が途中なら空リスト。チャンクの末尾を超えて確定した時点で
        `VadSegment(pcm, started_at_monotonic)` を返す。
        """

    @abstractmethod
    def reset(self) -> None:
        """内部状態(進行中の発話バッファ等)をリセットする。"""

    def capabilities(self) -> BackendCapabilities:
        """このバックエンドのメタ情報。既定は空。"""
        return BackendCapabilities()
