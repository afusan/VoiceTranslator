"""WebRtcVadBackend: webrtcvad ベースの発話区切り検出。

役割: 16kHz/mono/float32 のチャンクを webrtcvad のフレーム判定(speech/silence)に流し、
連続フレームのヒステリシスで speech 開始/終了を検出して `VadSegment` を返す。
Silero が onnxruntime 不在等で動かない環境のフォールバック用。CPU で極軽量・ルールベース。
"""

from __future__ import annotations

from time import monotonic

import numpy as np

from voice_translator.common.errors import FatalError
from voice_translator.common.types import (
    INTERNAL_SAMPLE_RATE,
    BackendCapabilities,
    ModelStatus,
    PcmChunk,
)

from .backend import VadBackend, VadSegment


# webrtcvad が許可する frame_duration_ms。10/20/30 のいずれか。
_ALLOWED_FRAME_MS = (10, 20, 30)


class WebRtcVadBackend(VadBackend):
    """webrtcvad のフレーム判定を集約して発話区切りを検出する backend。

    役割: webrtcvad は「フレーム = speech / silence」を返すだけのルールベース VAD。
    本クラスは連続 N フレーム speech → 発話開始 / 連続 M フレーム silence → 発話終了の
    ヒステリシスを被せ、Silero と同じ `VadSegment` プロトコルで下流に渡す。
    """

    def __init__(
        self,
        *,
        aggressiveness: int = 2,
        frame_ms: int = 30,
        min_speech_ms: int = 60,
        min_silence_ms: int = 500,
        speech_pad_ms: int = 100,
        max_speech_sec: float = 8.0,
    ) -> None:
        """
        Args:
            aggressiveness: webrtcvad の感度 0〜3。大きいほど speech 判定が厳しい(false speech が減る)。
            frame_ms: 1 フレームの長さ(ms)。10/20/30 のいずれか。
            min_speech_ms: 「N ms 連続で speech」を発話開始とみなす(短いノイズ除去)。
            min_silence_ms: 「N ms 連続で silence」を発話終了とみなす(短い無音は跨ぐ)。
            speech_pad_ms: 発話前後に付加する余白(ms)。確定 segment の前後に samples を足す。
            max_speech_sec: 1 発話の最大長(秒)。これを超えたら強制区切り。0/None で無効。
        """
        super().__init__()
        if frame_ms not in _ALLOWED_FRAME_MS:
            raise FatalError(
                f"webrtcvad は frame_ms={frame_ms} を受け付けません。{_ALLOWED_FRAME_MS} のいずれか。"
            )
        if not (0 <= aggressiveness <= 3):
            raise FatalError(
                f"aggressiveness は 0〜3 の範囲です(指定値: {aggressiveness})"
            )

        self._set_status(ModelStatus.LOADING)
        try:
            import webrtcvad  # type: ignore
        except Exception as e:  # noqa: BLE001
            self.record_error(e, context="webrtcvad import")
            raise FatalError(
                f"webrtcvad のロードに失敗(`uv sync --extra vad-extra` で追加してください): {e}",
                cause=e,
            ) from e

        try:
            self._vad = webrtcvad.Vad(aggressiveness)
        except Exception as e:  # noqa: BLE001
            self.record_error(e, context="webrtcvad init")
            raise FatalError(f"webrtcvad の初期化に失敗: {e}", cause=e) from e

        # フレームサイズ(サンプル数)。16kHz × frame_ms / 1000。
        self._frame_samples: int = INTERNAL_SAMPLE_RATE * frame_ms // 1000

        # ヒステリシスの閾値(フレーム単位)
        self._min_speech_frames: int = max(1, int(min_speech_ms / frame_ms))
        self._min_silence_frames: int = max(1, int(min_silence_ms / frame_ms))
        self._pad_frames: int = max(0, int(speech_pad_ms / frame_ms))

        # 強制区切りの上限サンプル数(0 以下なら無効化)
        self._max_speech_samples: int = (
            int(max_speech_sec * INTERNAL_SAMPLE_RATE)
            if max_speech_sec and max_speech_sec > 0
            else 0
        )

        self._frame_ms = frame_ms
        self._buffer = np.zeros(0, dtype=np.float32)

        # 状態機械:
        # - 発話前: 直近の speech フレーム連続数で発話開始判定。
        # - 発話中: 直近の silence フレーム連続数で発話終了判定。
        # - pad 用に直近 `pad_frames` ぶんのフレームを ring に持っておく(発話前の余白)。
        self._in_speech = False
        self._consec_speech_frames = 0
        self._consec_silence_frames = 0
        self._pre_speech_ring: list[np.ndarray] = []
        self._pending_silence_frames: list[np.ndarray] = []
        self._speech_samples: list[np.ndarray] = []
        self._speech_accumulated_samples = 0
        self._speech_started_at: float | None = None

        self._set_status(ModelStatus.LOADED)

    # ----------------------------------------------------------
    def reset(self) -> None:
        """内部状態を初期化する(進行中の発話バッファ + ring + フラグ)。"""
        self._buffer = np.zeros(0, dtype=np.float32)
        self._in_speech = False
        self._consec_speech_frames = 0
        self._consec_silence_frames = 0
        self._pre_speech_ring = []
        self._pending_silence_frames = []
        self._speech_samples = []
        self._speech_accumulated_samples = 0
        self._speech_started_at = None

    # ----------------------------------------------------------
    def process(self, chunk: PcmChunk) -> list[VadSegment]:
        """チャンクを投入し、確定した発話を返す。"""
        if chunk.size == 0:
            return []
        self._buffer = np.concatenate(
            [self._buffer, chunk.astype(np.float32, copy=False)]
        )

        completed: list[VadSegment] = []
        while self._buffer.size >= self._frame_samples:
            frame = self._buffer[: self._frame_samples]
            self._buffer = self._buffer[self._frame_samples :]
            is_speech = self._is_speech(frame)
            self._handle_frame(frame, is_speech, completed)

        return completed

    # ----------------------------------------------------------
    def capabilities(self) -> BackendCapabilities:
        return BackendCapabilities(notes="webrtcvad(C 実装)。16kHz 想定。ルールベース。")

    # ============================================================
    # 内部
    # ============================================================
    def _is_speech(self, frame: np.ndarray) -> bool:
        """1 フレームを webrtcvad に流して speech か判定。"""
        # webrtcvad は int16 PCM のバイト列を要求する。float32 [-1, 1] を変換。
        pcm_int16 = np.clip(frame, -1.0, 1.0)
        pcm_int16 = (pcm_int16 * 32767.0).astype(np.int16)
        try:
            return bool(
                self._vad.is_speech(pcm_int16.tobytes(), INTERNAL_SAMPLE_RATE)
            )
        except Exception as e:  # noqa: BLE001
            raise FatalError(f"webrtcvad 推論失敗: {e}", cause=e) from e

    def _handle_frame(
        self, frame: np.ndarray, is_speech: bool, completed: list[VadSegment]
    ) -> None:
        """1 フレームの判定を受けて状態を進める。"""
        if not self._in_speech:
            # 発話前
            # pre-speech ring: pad 分だけ保持
            if self._pad_frames > 0:
                self._pre_speech_ring.append(frame.copy())
                if len(self._pre_speech_ring) > self._pad_frames:
                    self._pre_speech_ring.pop(0)
            if is_speech:
                self._consec_speech_frames += 1
                if self._consec_speech_frames >= self._min_speech_frames:
                    self._enter_speech()
            else:
                self._consec_speech_frames = 0
            return

        # 発話中
        if is_speech:
            # 滞っていた silence frame を、発話の一部として吸収
            if self._pending_silence_frames:
                for sf in self._pending_silence_frames:
                    self._speech_samples.append(sf)
                    self._speech_accumulated_samples += sf.shape[0]
                self._pending_silence_frames = []
            self._consec_silence_frames = 0
            self._speech_samples.append(frame.copy())
            self._speech_accumulated_samples += frame.shape[0]
            self._maybe_force_emit(completed)
        else:
            # silence 候補。N 連続したら発話終了。
            self._pending_silence_frames.append(frame.copy())
            self._consec_silence_frames += 1
            if self._consec_silence_frames >= self._min_silence_frames:
                self._exit_speech(completed)

    def _enter_speech(self) -> None:
        """発話開始へ遷移。pre-speech ring を発話バッファの先頭に積む。"""
        self._in_speech = True
        # pre-speech ring(余白)を発話の先頭に
        prelude = list(self._pre_speech_ring)
        self._pre_speech_ring = []
        # _consec_speech_frames 分の speech frame 自体は ring に含まれている想定なので、
        # ring の内容をそのまま speech_samples の先頭に置く。
        self._speech_samples = list(prelude)
        self._speech_accumulated_samples = sum(s.shape[0] for s in self._speech_samples)
        self._speech_started_at = monotonic()
        # silence カウンタは発話中にしか使わないのでここでクリア
        self._consec_silence_frames = 0
        self._pending_silence_frames = []

    def _exit_speech(self, completed: list[VadSegment]) -> None:
        """発話終了へ遷移。`VadSegment` を emit する。"""
        if not self._speech_samples:
            self._in_speech = False
            self._consec_speech_frames = 0
            self._consec_silence_frames = 0
            self._pending_silence_frames = []
            return
        pcm = np.concatenate(self._speech_samples).astype(np.float32, copy=False)
        started_at = (
            self._speech_started_at
            if self._speech_started_at is not None
            else monotonic()
        )
        completed.append(VadSegment(pcm=pcm, started_at_monotonic=started_at))
        self._in_speech = False
        self._consec_speech_frames = 0
        self._consec_silence_frames = 0
        self._pending_silence_frames = []
        self._speech_samples = []
        self._speech_accumulated_samples = 0
        self._speech_started_at = None

    def _maybe_force_emit(self, completed: list[VadSegment]) -> None:
        """max_speech_sec 超過なら強制的に区切る。"""
        if (
            self._max_speech_samples <= 0
            or self._speech_accumulated_samples < self._max_speech_samples
        ):
            return
        pcm = np.concatenate(self._speech_samples).astype(np.float32, copy=False)
        started_at = (
            self._speech_started_at
            if self._speech_started_at is not None
            else monotonic()
        )
        completed.append(VadSegment(pcm=pcm, started_at_monotonic=started_at))
        # 発話継続中扱いで再開
        self._speech_samples = []
        self._speech_accumulated_samples = 0
        self._speech_started_at = monotonic()
