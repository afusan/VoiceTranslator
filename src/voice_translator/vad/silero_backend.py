"""SileroVadBackend: silero-vad ベースの発話区切り検出。

役割: 16kHz/mono/float32 のチャンクストリームを受け、silero-vad で
発話の開始/終了を検出し、確定した発話を `VadSegment` として返す。
silero-vad は 512サンプル単位の入力を要するため、内部バッファで合わせる。

ノンストップ放送(ニュース読み上げ等)で 30〜100 秒の発話が 1 単位になり、
下流の翻訳/TTS/再生が破綻するのを防ぐため、`max_speech_sec` の上限を
設けて N 秒経ったら強制的に区切る機能を持つ(0/None で無効=従来通り)。
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

from .backend import VadBackend, VadSegment


# silero-vad が要求するチャンクサイズ(16kHzのとき)
SILERO_CHUNK_SAMPLES = 512


class SileroVadBackend(VadBackend):
    """silero-vad を使った発話区切り検出。

    役割: process(chunk) を呼ぶたびに内部バッファに溜め、512サンプル単位で
    silero-vad に流す。speech start → speech end の間のサンプルを集めて
    VadSegment(pcm, started_at_monotonic) を生成する。
    """

    def __init__(
        self,
        *,
        threshold: float = 0.5,
        min_silence_ms: int = 500,
        speech_pad_ms: int = 100,
        max_speech_sec: float = 8.0,
    ) -> None:
        """
        Args:
            threshold: speech probability の判定しきい値(0〜1)。
            min_silence_ms: 発話終了とみなす無音期間(ms)。短くするほど早く区切られる。
            speech_pad_ms: 発話前後に付加する余白(ms)。
            max_speech_sec: 1 発話の最大長(秒)。これを超えたら強制的に区切って次の発話に
                繰り越す(VADIterator の状態は維持するので、次の音はそのまま継続発話扱い)。
                0 または None で無効化(従来通り VAD の end イベントだけが頼り)。
        """
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

        # 強制区切りの上限サンプル数(0 以下なら無効化)
        self._max_speech_samples: int = (
            int(max_speech_sec * INTERNAL_SAMPLE_RATE)
            if max_speech_sec and max_speech_sec > 0
            else 0
        )

        self._buffer = np.zeros(0, dtype=np.float32)
        self._in_speech = False
        self._speech_samples: list[np.ndarray] = []
        self._speech_started_at: float | None = None
        self._speech_accumulated_samples: int = 0

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
        self._speech_accumulated_samples = 0

    # ----------------------------------------------------------
    def process(self, chunk: PcmChunk) -> list[VadSegment]:
        """チャンクを投入し、確定した発話を返す。"""
        if chunk.size == 0:
            return []
        self._buffer = np.concatenate([self._buffer, chunk.astype(np.float32, copy=False)])

        completed: list[VadSegment] = []
        while self._buffer.size >= SILERO_CHUNK_SAMPLES:
            window = self._buffer[:SILERO_CHUNK_SAMPLES]
            self._buffer = self._buffer[SILERO_CHUNK_SAMPLES:]

            event = self._step(window)
            if event is not None:
                self._handle_event(event, window, completed)
            elif self._in_speech:
                # 発話継続中: サンプルを蓄積
                self._speech_samples.append(window.copy())
                self._speech_accumulated_samples += window.shape[0]
                # 最大長を超えたら強制区切り(VADIterator の状態は維持して継続発話扱いに)
                if (
                    self._max_speech_samples > 0
                    and self._speech_accumulated_samples >= self._max_speech_samples
                ):
                    self._force_emit_segment(completed)

        return completed

    def _force_emit_segment(self, completed: list[VadSegment]) -> None:
        """蓄積中の発話を強制的に区切って segment として emit し、次の発話として再開する。

        VADIterator(モデル側)の状態は触らない。次のチャンクが来たら "発話継続中" のまま
        speech_samples に貯まり始める。これにより 1 単位の処理時間が爆発するのを防ぐ。
        """
        pcm = np.concatenate(self._speech_samples).astype(np.float32, copy=False)
        started_at = (
            self._speech_started_at if self._speech_started_at is not None else monotonic()
        )
        completed.append(VadSegment(pcm=pcm, started_at_monotonic=started_at))
        # 次の発話として再開: _in_speech は維持
        self._speech_samples = []
        self._speech_accumulated_samples = 0
        self._speech_started_at = monotonic()

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
        self, event: dict[str, Any], window: np.ndarray, completed: list[VadSegment]
    ) -> None:
        """speech start / end の合図を受けて状態を更新する。"""
        if "start" in event:
            # 新しい発話の開始
            self._in_speech = True
            self._speech_samples = [window.copy()]
            self._speech_accumulated_samples = window.shape[0]
            self._speech_started_at = monotonic()
        elif "end" in event and self._in_speech:
            # 発話終了 → VadSegment を生成
            self._speech_samples.append(window.copy())
            self._speech_accumulated_samples += window.shape[0]
            pcm = np.concatenate(self._speech_samples).astype(np.float32, copy=False)
            started_at = self._speech_started_at if self._speech_started_at is not None else monotonic()
            completed.append(VadSegment(pcm=pcm, started_at_monotonic=started_at))

            self._in_speech = False
            self._speech_samples = []
            self._speech_accumulated_samples = 0
            self._speech_started_at = None
