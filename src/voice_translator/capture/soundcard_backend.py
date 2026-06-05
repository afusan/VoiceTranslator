"""SoundcardCaptureBackend: soundcard ライブラリを使った音声取得。

役割: マイク + スピーカLB(ループバック) を入力ソースとして列挙し、
選択されたソースから 16kHz/mono/float32 で PCM チャンクを取得する。
内部の `record()` はブロッキング呼び出し(チャンクサイズ分待つ)。
"""

from __future__ import annotations

import numpy as np
import soundcard as sc

from voice_translator.common.errors import FatalError
from voice_translator.common.types import (
    INTERNAL_CHANNELS,
    INTERNAL_SAMPLE_RATE,
    BackendCapabilities,
    CaptureKind,
    CaptureSource,
    ModelStatus,
    PcmChunk,
)

from .backend import AudioCaptureBackend


class SoundcardCaptureBackend(AudioCaptureBackend):
    """soundcard ベースの AudioCaptureBackend(デバイス単位の取得)。

    役割: 通常マイクとスピーカLBを列挙し、選択ソースから 16kHz/mono/float32 の
    チャンクを供給する。`read_chunk` の timeout 引数はチャンクサイズに連動するため、
    呼び出しは概ね 100ms 程度で返る(chunk_size=1600 のとき)。

    取得単位は `CaptureKind.DEVICE`(物理デバイス)。プロセス単位は将来の
    `ProcTapCaptureBackend` 等で対応する。
    """

    @classmethod
    def capture_kind(cls) -> CaptureKind:
        """soundcard はデバイス単位の取得 backend。"""
        return CaptureKind.DEVICE

    def __init__(self, *, chunk_size: int = 1600) -> None:
        super().__init__()  # BackendBase: status=INIT
        # 1600 frames / 16kHz = 100ms。PipelineCoordinator.read_timeout と整合させる。
        self._chunk_size = chunk_size
        self._mic: sc._Microphone | None = None  # type: ignore[name-defined]
        self._context = None  # context manager
        self._recorder = None  # 実体(record を持つ)
        # soundcard はライブラリで DL なし。デバイスは start() で開く。
        self._set_status(ModelStatus.LOADED)

    # ----------------------------------------------------------
    def list_sources(self) -> list[CaptureSource]:
        """通常マイク + スピーカLB(あれば) を列挙する。kind は DEVICE。"""
        sources: list[CaptureSource] = []
        seen_ids: set[str] = set()
        for mic in sc.all_microphones(include_loopback=True):
            sid = str(mic.id)
            if sid in seen_ids:
                continue
            seen_ids.add(sid)
            is_lb = bool(getattr(mic, "isloopback", False))
            display = f"[LB] {mic.name}" if is_lb else mic.name
            sources.append(
                CaptureSource(
                    source_id=sid,
                    display_name=display,
                    is_loopback=is_lb,
                    kind=CaptureKind.DEVICE,
                )
            )
        return sources

    # ----------------------------------------------------------
    def start(self, source_id: str) -> None:
        """指定 source_id のマイク/LBを開いて record 可能状態にする。"""
        if self._recorder is not None:
            raise RuntimeError("既に start 済みです。先に stop してください。")

        mic = self._find_mic(source_id)
        if mic is None:
            raise FatalError(f"指定された入力デバイスが見つかりません: {source_id}")

        try:
            self._context = mic.recorder(
                samplerate=INTERNAL_SAMPLE_RATE,
                channels=INTERNAL_CHANNELS,
                blocksize=self._chunk_size,
            )
            self._recorder = self._context.__enter__()
            self._mic = mic
        except Exception as e:  # noqa: BLE001 - 起動失敗はFATAL扱い
            raise FatalError(f"音声取得の開始に失敗: {e}", cause=e) from e

    # ----------------------------------------------------------
    def read_chunk(self, timeout: float = 0.1) -> PcmChunk | None:
        """chunk_size 分の PCM を取得して返す。

        timeout 引数は API 互換のため受けるが、soundcard の record は
        ブロッキングなので実質 chunk_size に対する時間で完了する。
        """
        if self._recorder is None:
            raise RuntimeError("start() を呼んでから read_chunk() してください")
        try:
            data = self._recorder.record(numframes=self._chunk_size)
        except Exception as e:  # noqa: BLE001 - デバイス消失等は FATAL に分類
            raise FatalError(f"音声取得に失敗: {e}", cause=e) from e
        return _to_internal_format(data)

    # ----------------------------------------------------------
    def stop(self) -> None:
        """recorder を閉じる。複数回呼ばれても安全。"""
        if self._context is not None:
            try:
                self._context.__exit__(None, None, None)
            except Exception:  # noqa: BLE001 - クローズ時の例外は握りつぶす
                pass
        self._context = None
        self._recorder = None
        self._mic = None

    # ----------------------------------------------------------
    def capabilities(self) -> BackendCapabilities:
        return BackendCapabilities(
            supported_languages=(),  # 音声取得は言語非依存
            requires_gpu=False,
            is_cloud=False,
            requires_credentials=False,
            notes="soundcard ベース。Windows/Linuxは loopback対応。Macは BlackHole等が別途必要。",
        )

    # ----------------------------------------------------------
    @staticmethod
    def _find_mic(source_id: str):
        """指定 id のマイク/LBを返す。見つからなければ None。"""
        for mic in sc.all_microphones(include_loopback=True):
            if str(mic.id) == source_id:
                return mic
        return None


def _to_internal_format(data: np.ndarray) -> PcmChunk:
    """soundcard の戻り値 (numframes, channels) を 1次元 float32 に整える。"""
    if data.ndim == 2:
        if data.shape[1] == 1:
            data = data[:, 0]
        else:
            data = data.mean(axis=1)
    return data.astype(np.float32, copy=False)
