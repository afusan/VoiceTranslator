"""PvcobraVadBackend: Picovoice Cobra ベースの発話区切り検出。

役割: pvcobra(C 実装の軽量 VAD)に 512 サンプル/フレームを流し、voice probability を
閾値判定して発話開始/終了を検出する。ローカル動作だがアクセスキー(認証)が要る — クラウド
backend とは別パターンの認証フローの検証材料。
"""

from __future__ import annotations

from time import monotonic

import numpy as np

from voice_translator.common.errors import FatalError
from voice_translator.common.types import (
    INTERNAL_SAMPLE_RATE,
    BackendCapabilities,
    CredentialField,
    ModelStatus,
    PcmChunk,
    VerifyResult,
)

from .backend import VadBackend, VadSegment


# Cobra は 16kHz/mono/int16 の 512 サンプル(=32ms)フレームを要求する。
_COBRA_FRAME_SAMPLES = 512
_COBRA_FRAME_MS = _COBRA_FRAME_SAMPLES * 1000 // INTERNAL_SAMPLE_RATE  # = 32


class PvcobraVadBackend(VadBackend):
    """Picovoice Cobra を駆動して `VadSegment` を返す backend。

    役割: Cobra は「フレーム → voice probability(0〜1)」を返す。本クラスは閾値超え/
    下回りの連続フレーム数で発話開始/終了を判定し、Silero と同じ `VadSegment` プロトコルで
    下流に渡す。`access_key` 必須。
    """

    def __init__(
        self,
        *,
        access_key: str | None = None,
        threshold: float = 0.5,
        min_speech_ms: int = 64,
        min_silence_ms: int = 500,
        speech_pad_ms: int = 100,
        max_speech_sec: float = 8.0,
    ) -> None:
        """
        Args:
            access_key: Picovoice Access Key。None なら MISSING_CREDENTIALS。
            threshold: voice probability の閾値(0〜1)。これを超えたフレームを speech 候補とする。
            min_speech_ms: 連続 speech 候補をこれだけの長さ確保したら発話開始(短いノイズ除去)。
            min_silence_ms: 連続 silence をこれだけ確保したら発話終了(短い無音を跨ぐ)。
            speech_pad_ms: 発話前後に付加する余白(ms)。
            max_speech_sec: 1 発話の最大長。これを超えたら強制区切り。
        """
        super().__init__()
        self._threshold = threshold
        self._frame_samples = _COBRA_FRAME_SAMPLES
        self._min_speech_frames = max(1, int(min_speech_ms / _COBRA_FRAME_MS))
        self._min_silence_frames = max(1, int(min_silence_ms / _COBRA_FRAME_MS))
        self._pad_frames = max(0, int(speech_pad_ms / _COBRA_FRAME_MS))
        self._max_speech_samples: int = (
            int(max_speech_sec * INTERNAL_SAMPLE_RATE)
            if max_speech_sec and max_speech_sec > 0
            else 0
        )

        self._cobra = None
        self._buffer = np.zeros(0, dtype=np.float32)
        self._in_speech = False
        self._consec_speech_frames = 0
        self._consec_silence_frames = 0
        self._pre_speech_ring: list[np.ndarray] = []
        self._pending_silence_frames: list[np.ndarray] = []
        self._speech_samples: list[np.ndarray] = []
        self._speech_accumulated_samples = 0
        self._speech_started_at: float | None = None

        if not access_key:
            # 認証情報が無い段階では load せず MISSING_CREDENTIALS に留まる。
            # AppController の start gate が止める想定。
            self._set_status(ModelStatus.MISSING_CREDENTIALS)
            return

        self._set_status(ModelStatus.LOADING)
        try:
            import pvcobra  # type: ignore
        except Exception as e:  # noqa: BLE001
            self.record_error(e, context="pvcobra import")
            raise FatalError(
                f"pvcobra のロードに失敗(`uv sync --extra vad-extra` で追加してください): {e}",
                cause=e,
            ) from e

        try:
            self._cobra = pvcobra.create(access_key=access_key)
        except Exception as e:  # noqa: BLE001
            self.record_error(e, context="pvcobra create")
            raise FatalError(
                f"pvcobra の初期化に失敗(access_key を確認): {e}", cause=e
            ) from e

        self._set_status(ModelStatus.LOADED)

    # ============================================================
    # 認証情報フロー
    # ============================================================
    @classmethod
    def credential_spec(cls) -> list[CredentialField]:
        return [
            CredentialField(
                key_name="access_key",
                label="Picovoice Access Key",
                secret=True,
                help_text=(
                    "https://console.picovoice.ai/ で発行。個人非商用は無料 tier、"
                    "商用利用は別途ライセンス。"
                ),
            ),
        ]

    @classmethod
    def verify_credentials(cls, values: dict[str, str]) -> VerifyResult:
        key = (values or {}).get("access_key", "").strip()
        if not key:
            return VerifyResult(ok=False, message="Access Key が未入力です")
        try:
            import pvcobra  # type: ignore
        except Exception as e:  # noqa: BLE001
            return VerifyResult(
                ok=False,
                message=f"pvcobra 未インストール(`uv sync --extra vad-extra`): {e}",
            )
        try:
            inst = pvcobra.create(access_key=key)
        except Exception as e:  # noqa: BLE001
            return VerifyResult(ok=False, message=f"Access Key が無効: {e}")
        # 即座に開放(verify は疎通確認のみ)
        try:
            inst.delete()
        except Exception:  # noqa: BLE001
            pass
        return VerifyResult(ok=True, message="Picovoice 認証 OK")

    # ============================================================
    # I/F
    # ============================================================
    def reset(self) -> None:
        self._buffer = np.zeros(0, dtype=np.float32)
        self._in_speech = False
        self._consec_speech_frames = 0
        self._consec_silence_frames = 0
        self._pre_speech_ring = []
        self._pending_silence_frames = []
        self._speech_samples = []
        self._speech_accumulated_samples = 0
        self._speech_started_at = None

    def process(self, chunk: PcmChunk) -> list[VadSegment]:
        if self._cobra is None:
            return []
        if chunk.size == 0:
            return []
        self._buffer = np.concatenate(
            [self._buffer, chunk.astype(np.float32, copy=False)]
        )

        completed: list[VadSegment] = []
        while self._buffer.size >= self._frame_samples:
            frame = self._buffer[: self._frame_samples]
            self._buffer = self._buffer[self._frame_samples :]
            prob = self._infer(frame)
            self._handle_frame(frame, prob >= self._threshold, completed)
        return completed

    def capabilities(self) -> BackendCapabilities:
        return BackendCapabilities(
            is_cloud=False,
            requires_credentials=True,
            service_name="Picovoice Cobra",
            terms_url="https://picovoice.ai/docs/cobra/",
            notes="pvcobra (C 実装)。16kHz / 512 サンプルフレーム。アクセスキー必須。",
        )

    # ============================================================
    # 内部
    # ============================================================
    def _infer(self, frame: np.ndarray) -> float:
        """1 フレームを Cobra に流して voice probability を取得。"""
        pcm_int16 = np.clip(frame, -1.0, 1.0)
        pcm_int16 = (pcm_int16 * 32767.0).astype(np.int16)
        try:
            return float(self._cobra.process(pcm_int16.tolist()))
        except Exception as e:  # noqa: BLE001
            raise FatalError(f"pvcobra 推論失敗: {e}", cause=e) from e

    def _handle_frame(
        self, frame: np.ndarray, is_speech: bool, completed: list[VadSegment]
    ) -> None:
        """1 フレーム判定を受けて状態を進める(WebRtcVadBackend と同じヒステリシス)。"""
        if not self._in_speech:
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
            self._pending_silence_frames.append(frame.copy())
            self._consec_silence_frames += 1
            if self._consec_silence_frames >= self._min_silence_frames:
                self._exit_speech(completed)

    def _enter_speech(self) -> None:
        self._in_speech = True
        prelude = list(self._pre_speech_ring)
        self._pre_speech_ring = []
        self._speech_samples = list(prelude)
        self._speech_accumulated_samples = sum(s.shape[0] for s in self._speech_samples)
        self._speech_started_at = monotonic()
        self._consec_silence_frames = 0
        self._pending_silence_frames = []

    def _exit_speech(self, completed: list[VadSegment]) -> None:
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
        self._speech_samples = []
        self._speech_accumulated_samples = 0
        self._speech_started_at = monotonic()
