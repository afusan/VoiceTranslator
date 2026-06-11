"""AudioCaptureBackend 抽象基底。

役割: 音声デバイスから PCM チャンクを取得し、内部標準フォーマット
(16kHz/mono/float32) に正規化してパイプラインに供給するレイヤの I/F。
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from voice_translator.common.backend_base import BackendBase
from voice_translator.common.messages import PayloadKind
from voice_translator.common.types import (
    BackendCapabilities,
    CaptureKind,
    CaptureSource,
    LayerKind,
    PcmChunk,
)


class AudioCaptureBackend(BackendBase, ABC):
    """音声取得バックエンドの抽象基底。

    実装は OS/ライブラリ別に作る(MVPは soundcard ベース)。
    `BackendBase` から状態管理/購読/エラー履歴の機能を継承する。

    取得単位(kind): 各 backend は自身が「デバイス単位」「プロセス単位」のどちらを
    扱うかを `capture_kind()` クラスメソッドで宣言する。SettingsPanel はこの宣言を
    見て「取得単位」プルダウンを構築し、ユーザは kind を切り替えて取得方式を変更できる。
    既定は `DEVICE`(soundcard 系)。`PROCESS` は段階 2 で追加予定の ProcTap 等で使う。
    """

    @classmethod
    def capture_kind(cls) -> CaptureKind:
        """この backend が取得する単位を返す。サブクラスでオーバーライド可。既定は DEVICE。"""
        return CaptureKind.DEVICE

    # ---- パイプライン編成への申告(複合 backend はオーバーライド) ----
    @classmethod
    def covers_roles(cls) -> tuple[LayerKind, ...]:
        """この backend が担うロール(パイプライン順で連続していること)。"""
        return (LayerKind.CAPTURE,)

    @classmethod
    def consumes_payload(cls) -> PayloadKind:
        """入力の payload 形式。Capture は編成の先頭なので NONE。"""
        return PayloadKind.NONE

    @classmethod
    def produces_payload(cls) -> PayloadKind:
        """出力の payload 形式。Capture 単体は PCM ストリーム供給のみで
        発話 payload を産まない(発話単位は VAD 側の担当)ため NONE。"""
        return PayloadKind.NONE

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
