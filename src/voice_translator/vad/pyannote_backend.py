"""PyannoteVadBackend: pyannote.audio の VAD pipeline ベースの発話区切り検出。

役割: pyannote/voice-activity-detection モデルを HuggingFace から取得し、
バッファに溜めた音声を pipeline に流して segments を切り出す。ニューラルベースで
精度は高いが重く、HuggingFace token と利用同意が必要(gated model)。
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


# pyannote にバッチで投げるバッファ長(秒)。長すぎると遅延、短すぎると pipeline 起動コスト過多。
_BATCH_WINDOW_SEC = 2.0


class PyannoteVadBackend(VadBackend):
    """pyannote.audio の VAD pipeline を駆動して `VadSegment` を返す backend。

    役割: ストリーミング型でない pyannote pipeline をバッチ寄りに使う。一定量バッファ
    に溜めてから pipeline.apply() を呼び、得られた active segments を `VadSegment` で
    下流に渡す。HuggingFace 認証必須(gated model)。
    """

    def __init__(
        self,
        *,
        hf_token: str | None = None,
        model_id: str = "pyannote/voice-activity-detection",
        device: str = "auto",
        min_speech_ms: int = 200,
        min_silence_ms: int = 500,  # noqa: ARG002 - pipeline 既定に委譲(将来の調整余地)
        max_speech_sec: float = 8.0,
        batch_window_sec: float = _BATCH_WINDOW_SEC,
    ) -> None:
        """
        Args:
            hf_token: HuggingFace Token(モデル DL に必要)。None なら MISSING_CREDENTIALS 状態。
            model_id: pyannote モデル ID。
            device: "cpu" / "cuda" / "auto"。auto は torch.cuda.is_available() で振り分け。
            min_speech_ms: pipeline 結果の active segment 最小長(これ未満は破棄)。
            min_silence_ms: 将来の調整余地(pipeline 既定に委ねる)。
            max_speech_sec: 1 発話の最大長。これを超えたら強制区切り。
            batch_window_sec: pipeline に渡すバッファ長(秒)。
        """
        super().__init__()
        self._model_id = model_id
        self._device_pref = device
        self._min_speech_samples: int = int(min_speech_ms * INTERNAL_SAMPLE_RATE / 1000)
        self._max_speech_samples: int = (
            int(max_speech_sec * INTERNAL_SAMPLE_RATE)
            if max_speech_sec and max_speech_sec > 0
            else 0
        )
        self._batch_samples: int = int(batch_window_sec * INTERNAL_SAMPLE_RATE)

        # token 未入力なら、import 等の重い処理に進む前に MISSING_CREDENTIALS を立てる。
        # AppController._check_missing_credentials_gate でこの状態を見て start をブロックする。
        if not hf_token:
            self._set_status(ModelStatus.MISSING_CREDENTIALS)
            self._pipeline = None
            self._buffer = np.zeros(0, dtype=np.float32)
            self._in_speech = False
            self._speech_samples: list[np.ndarray] = []
            self._speech_accumulated_samples = 0
            self._speech_started_at: float | None = None
            self._buffer_offset_sec: float = 0.0
            return

        self._set_status(ModelStatus.LOADING)
        try:
            from pyannote.audio import Pipeline  # type: ignore
        except Exception as e:  # noqa: BLE001
            self.record_error(e, context="pyannote.audio import")
            raise FatalError(
                f"pyannote.audio のロードに失敗(`uv sync --extra vad-extra` で追加してください): {e}",
                cause=e,
            ) from e

        try:
            pipeline = Pipeline.from_pretrained(model_id, use_auth_token=hf_token)
        except Exception as e:  # noqa: BLE001
            self.record_error(e, context="pyannote pipeline load")
            raise FatalError(
                f"pyannote pipeline のロードに失敗(HF token / model 同意を確認): {e}",
                cause=e,
            ) from e

        # device 振り分け
        resolved_device = self._resolve_device(device)
        try:
            import torch  # type: ignore

            pipeline.to(torch.device(resolved_device))
        except Exception:  # noqa: BLE001 - torch 未入っていれば cpu のまま
            pass

        self._pipeline = pipeline
        self._buffer = np.zeros(0, dtype=np.float32)
        self._in_speech = False
        self._speech_samples = []
        self._speech_accumulated_samples = 0
        self._speech_started_at = None
        # buffer の先頭が「発話の何秒目に位置するか」(pipeline からの相対秒数 → 絶対 monotonic への換算用)
        self._buffer_offset_sec = 0.0

        self._set_status(ModelStatus.LOADED)

    # ============================================================
    # 認証情報フロー
    # ============================================================
    @classmethod
    def credential_spec(cls) -> list[CredentialField]:
        return [
            CredentialField(
                key_name="hf_token",
                label="HuggingFace Token",
                secret=True,
                help_text=(
                    "pyannote.audio のモデルは gated。https://hf.co/settings/tokens で発行し、"
                    "モデルページで利用同意を済ませること。"
                ),
            ),
        ]

    @classmethod
    def verify_credentials(cls, values: dict[str, str]) -> VerifyResult:
        token = (values or {}).get("hf_token", "").strip()
        if not token:
            return VerifyResult(ok=False, message="HuggingFace Token が未入力です")
        # 軽量チェック: HF API の /api/whoami-v2 を呼んで token が生きているか見る。
        # モデルの DL は重いので、ここではしない。実モデルアクセス権の検証は別途
        # backend の初期化で扱う(初回 process で失敗 → record_error)。
        try:
            import urllib.error
            import urllib.request

            req = urllib.request.Request(
                "https://huggingface.co/api/whoami-v2",
                headers={"Authorization": f"Bearer {token}"},
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                if 200 <= resp.status < 300:
                    return VerifyResult(ok=True, message="HuggingFace 認証 OK")
                return VerifyResult(
                    ok=False, message=f"HF API 応答異常: HTTP {resp.status}"
                )
        except urllib.error.HTTPError as e:
            if e.code in (401, 403):
                return VerifyResult(ok=False, message="HuggingFace Token が無効です")
            return VerifyResult(ok=False, message=f"HF API エラー: HTTP {e.code}")
        except Exception as e:  # noqa: BLE001
            return VerifyResult(ok=False, message=f"HF API 接続失敗: {e}")

    # ============================================================
    # I/F
    # ============================================================
    def reset(self) -> None:
        self._buffer = np.zeros(0, dtype=np.float32)
        self._in_speech = False
        self._speech_samples = []
        self._speech_accumulated_samples = 0
        self._speech_started_at = None
        self._buffer_offset_sec = 0.0

    def process(self, chunk: PcmChunk) -> list[VadSegment]:
        if self._pipeline is None:
            return []
        if chunk.size == 0:
            return []
        self._buffer = np.concatenate(
            [self._buffer, chunk.astype(np.float32, copy=False)]
        )
        completed: list[VadSegment] = []
        while self._buffer.size >= self._batch_samples:
            window = self._buffer[: self._batch_samples]
            self._buffer = self._buffer[self._batch_samples :]
            self._run_pipeline(window, completed)
        return completed

    def capabilities(self) -> BackendCapabilities:
        return BackendCapabilities(
            requires_gpu=False,  # CPU でも動くが激重
            requires_credentials=True,
            service_name="pyannote.audio (HuggingFace)",
            terms_url="https://huggingface.co/pyannote/voice-activity-detection",
            notes="pyannote.audio VAD pipeline。HF token + 利用同意が必要。",
        )

    # ============================================================
    # 内部
    # ============================================================
    @staticmethod
    def _resolve_device(pref: str) -> str:
        if pref in ("cpu", "cuda", "mps"):
            return pref
        try:
            import torch  # type: ignore

            if torch.cuda.is_available():
                return "cuda"
            mps_ok = getattr(getattr(torch, "backends", None), "mps", None)
            if mps_ok is not None and mps_ok.is_available():
                return "mps"
        except Exception:  # noqa: BLE001
            pass
        return "cpu"

    def _run_pipeline(self, window: np.ndarray, completed: list[VadSegment]) -> None:
        """1 バッチを pipeline に投げ、active segments を VadSegment 化する。"""
        try:
            # pyannote は (channels, samples) の torch.Tensor を期待する
            import torch  # type: ignore

            waveform = torch.from_numpy(window.copy()).unsqueeze(0)
            annotation = self._pipeline(
                {"waveform": waveform, "sample_rate": INTERNAL_SAMPLE_RATE}
            )
        except Exception as e:  # noqa: BLE001
            self.record_error(e, context="pyannote pipeline apply")
            # バッファ消費を進めるが、segment は出さない
            return

        # annotation.get_timeline().support() で active 区間を列挙(pyannote.core.Annotation 互換)
        try:
            timeline = annotation.get_timeline().support()
        except Exception:  # noqa: BLE001
            return

        for segment in timeline:
            start_sec = float(segment.start)
            end_sec = float(segment.end)
            start_idx = max(0, int(start_sec * INTERNAL_SAMPLE_RATE))
            end_idx = min(window.shape[0], int(end_sec * INTERNAL_SAMPLE_RATE))
            if end_idx <= start_idx:
                continue
            pcm = window[start_idx:end_idx].astype(np.float32, copy=False)
            if pcm.shape[0] < self._min_speech_samples:
                continue
            # 強制区切り上限を超えていたら、後段で破裂しないよう切り詰める
            if (
                self._max_speech_samples > 0
                and pcm.shape[0] > self._max_speech_samples
            ):
                # チャンク分割: max_speech_samples ごとに segment を切る
                pos = 0
                while pos < pcm.shape[0]:
                    seg = pcm[pos : pos + self._max_speech_samples]
                    completed.append(
                        VadSegment(pcm=seg.copy(), started_at_monotonic=monotonic())
                    )
                    pos += self._max_speech_samples
            else:
                completed.append(
                    VadSegment(pcm=pcm.copy(), started_at_monotonic=monotonic())
                )

        self._buffer_offset_sec += window.shape[0] / INTERNAL_SAMPLE_RATE
