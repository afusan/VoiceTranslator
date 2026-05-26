"""SileroVadBackend: silero-vad ベースの発話区切り検出。

役割: 16kHz/mono/float32 のチャンクストリームを受け、silero-vad で
発話の開始/終了を検出し、確定した発話を Utterance として返す。
silero-vad は 512サンプル単位の入力を要するため、内部バッファで合わせる。
"""

from __future__ import annotations

from time import monotonic
from typing import Any

import numpy as np

from voice_translator.common.errors import FatalError
from voice_translator.common.types import (
    INTERNAL_SAMPLE_RATE,
    BackendCapabilities,
    PcmChunk,
)
from voice_translator.common.utterance import Utterance

from .backend import VadBackend


# silero-vad が要求するチャンクサイズ(16kHzのとき)
SILERO_CHUNK_SAMPLES = 512


class SileroVadBackend(VadBackend):
    """silero-vad を使った発話区切り検出。

    役割: process(chunk) を呼ぶたびに内部バッファに溜め、512サンプル単位で
    silero-vad に流す。speech start → speech end の間のサンプルを集めて
    Utterance を生成する。
    """

    def __init__(
        self,
        *,
        threshold: float = 0.5,
        min_silence_ms: int = 800,   # 既定: 500→800(文末まで待つ)
        speech_pad_ms: int = 250,    # 既定: 100→250(語尾の子音を逃さない)
    ) -> None:
        # 遅延 import: silero-vad は依存(onnxruntime/torch)が重いため、
        # クラス生成時にのみ取り込む。テストでは monkeypatch しやすい。
        try:
            from silero_vad import VADIterator, load_silero_vad  # type: ignore
        except Exception as e:  # noqa: BLE001
            raise FatalError(f"silero-vad のロードに失敗: {e}", cause=e) from e

        try:
            self._model = load_silero_vad()
            self._iter = VADIterator(
                self._model,
                threshold=threshold,
                sampling_rate=INTERNAL_SAMPLE_RATE,
                min_silence_duration_ms=min_silence_ms,
                speech_pad_ms=speech_pad_ms,
            )
        except Exception as e:  # noqa: BLE001
            raise FatalError(f"silero-vad の初期化に失敗: {e}", cause=e) from e

        self._buffer = np.zeros(0, dtype=np.float32)
        self._in_speech = False
        self._speech_samples: list[np.ndarray] = []
        self._speech_started_at: float | None = None

    # ----------------------------------------------------------
    def reset(self) -> None:
        """内部状態を初期化する(進行中の発話バッファとVAD状態)。"""
        try:
            self._iter.reset_states()
        except Exception:  # noqa: BLE001
            pass
        self._buffer = np.zeros(0, dtype=np.float32)
        self._in_speech = False
        self._speech_samples = []
        self._speech_started_at = None

    # ----------------------------------------------------------
    def process(self, chunk: PcmChunk) -> list[Utterance]:
        """チャンクを投入し、確定した発話を返す。"""
        if chunk.size == 0:
            return []
        self._buffer = np.concatenate([self._buffer, chunk.astype(np.float32, copy=False)])

        completed: list[Utterance] = []
        while self._buffer.size >= SILERO_CHUNK_SAMPLES:
            window = self._buffer[:SILERO_CHUNK_SAMPLES]
            self._buffer = self._buffer[SILERO_CHUNK_SAMPLES:]

            event = self._step(window)
            if event is not None:
                self._handle_event(event, window, completed)
            elif self._in_speech:
                # 発話継続中: サンプルを蓄積
                self._speech_samples.append(window.copy())

        return completed

    # ----------------------------------------------------------
    def capabilities(self) -> BackendCapabilities:
        return BackendCapabilities(notes="silero-vad v5+(onnx 同梱)。16kHz 想定。")

    # ---- 内部 ----
    def _step(self, window: np.ndarray) -> dict[str, Any] | None:
        """1ウィンドウぶんを VAD に流し、{'start':...} / {'end':...} / None を返す。"""
        try:
            return self._iter(window, return_seconds=False)
        except Exception as e:  # noqa: BLE001
            raise FatalError(f"silero-vad 推論失敗: {e}", cause=e) from e

    def _handle_event(
        self, event: dict[str, Any], window: np.ndarray, completed: list[Utterance]
    ) -> None:
        """speech start / end の合図を受けて状態を更新する。"""
        if "start" in event:
            # 新しい発話の開始
            self._in_speech = True
            self._speech_samples = [window.copy()]
            self._speech_started_at = monotonic()
        elif "end" in event and self._in_speech:
            # 発話終了 → Utterance を生成
            self._speech_samples.append(window.copy())
            pcm = np.concatenate(self._speech_samples).astype(np.float32, copy=False)
            utt = Utterance(pcm=pcm)
            # t_capture は発話開始時点(VAD検知時刻)を採用
            if self._speech_started_at is not None:
                utt.timeline._times["t_capture"] = self._speech_started_at  # 直接代入(再mark回避)
            else:
                utt.timeline.mark("t_capture")
            utt.timeline.mark("t_vad_end")
            completed.append(utt)

            self._in_speech = False
            self._speech_samples = []
            self._speech_started_at = None
