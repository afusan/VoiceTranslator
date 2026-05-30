"""PyannoteVadBackend: pyannote.audio の VAD pipeline ベースの発話区切り検出。

役割: pyannote/voice-activity-detection モデルを HuggingFace から取得し、
バッファに溜めた音声を pipeline に流して segments を切り出す。ニューラルベースで
精度は高いが重く、HuggingFace token と利用同意が必要(gated model)。

実装方針(2026-05-30 再構築):
- pyannote.audio **4.x** の公式 API(`Pipeline.from_pretrained(..., token=...)`)を
  そのまま使う。旧 3.x 系の `use_auth_token` / torch 2.6 weights_only / speechbrain
  LazyModule への 対症コード(monkey-patch / shim 等)は入れない方針。
  4.x が現代の huggingface_hub / torch 2.8 / checkpoint 形式に追従済みなので、
  公式 API だけで動く設計が成立する。
- 3.x へのフォールバックは入れない(配布方針上 `vad-extra` 利用時は 4.x を前提とする)。
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
        model_id: str = "pyannote/segmentation-3.0",
        device: str = "auto",
        min_speech_ms: int = 200,
        min_silence_ms: int = 500,
        max_speech_sec: float = 8.0,
        batch_window_sec: float = _BATCH_WINDOW_SEC,
    ) -> None:
        """
        Args:
            hf_token: HuggingFace Token(モデル DL に必要)。None なら MISSING_CREDENTIALS 状態。
            model_id: pyannote の **segmentation** モデル ID(VAD pipeline の基底に使う)。
                既定 `pyannote/segmentation-3.0`。
                `pyannote/voice-activity-detection` pipeline は HF 上の config が古い
                `@revision` 構文を含んでおり pyannote 4.x で動かないため不採用。
            device: "cpu" / "cuda" / "auto"。auto は torch.cuda.is_available() で振り分け。
            min_speech_ms: VAD pipeline の `min_duration_on`(これ未満の speech は捨てる)。
            min_silence_ms: VAD pipeline の `min_duration_off`(これ未満の無音は跨ぐ)。
            max_speech_sec: 1 発話の最大長。これを超えたら強制区切り。
            batch_window_sec: pipeline に渡すバッファ長(秒)。
        """
        super().__init__()
        self._model_id = model_id
        self._device_pref = device
        self._min_speech_ms = min_speech_ms
        self._min_silence_ms = min_silence_ms
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
            from pyannote.audio import Model  # type: ignore
            from pyannote.audio.pipelines import VoiceActivityDetection  # type: ignore
        except Exception as e:  # noqa: BLE001
            self.record_error(e, context="pyannote.audio import")
            raise FatalError(
                f"pyannote.audio のロードに失敗(`uv sync --extra vad-extra` で追加してください): {e}",
                cause=e,
            ) from e

        # 設計判断:
        # `Pipeline.from_pretrained("pyannote/voice-activity-detection")` を直接使う案は
        # 不採用。HF 上の pipeline config が古い `pyannote/segmentation@2022.07` という
        # `@revision` 構文を含み、pyannote 4.x が拒否する(`Revisions must be passed with
        # `revision` keyword argument.`)。
        # 代わりに pyannote 4.x の正規手順である「segmentation モデルを Model.from_pretrained
        # で取得 → VoiceActivityDetection(segmentation=model) で pipeline を組み立て」を採る。
        # これは pyannote 4.x の README / migration guide で推奨されているパターンで、
        # band-aid ではなく公式の組み立て方。
        try:
            segmentation_model = Model.from_pretrained(model_id, token=hf_token)
        except Exception as e:  # noqa: BLE001
            self.record_error(e, context="pyannote model load")
            raise FatalError(
                f"pyannote segmentation モデルのロードに失敗(HF token / "
                f"`{model_id}` の利用同意を確認): {e}",
                cause=e,
            ) from e
        if segmentation_model is None:
            # 4.x で `Model.from_pretrained` が None を返すケースは想定しないが防御線として残す
            raise FatalError(
                f"pyannote segmentation モデル `{model_id}` の取得結果が None でした。"
            )

        try:
            pipeline = VoiceActivityDetection(segmentation=segmentation_model)
            pipeline.instantiate(
                {
                    "min_duration_on": max(0.0, min_speech_ms / 1000.0),
                    "min_duration_off": max(0.0, min_silence_ms / 1000.0),
                }
            )
        except Exception as e:  # noqa: BLE001
            self.record_error(e, context="pyannote pipeline instantiate")
            raise FatalError(
                f"pyannote VAD pipeline の構築に失敗: {e}",
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
                    "https://hf.co/settings/tokens で発行 → "
                    "https://hf.co/pyannote/segmentation-3.0 で利用同意を済ませること。"
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
            terms_url="https://huggingface.co/pyannote/segmentation-3.0",
            notes="pyannote.audio 4.x VAD pipeline。segmentation-3.0 を基底に構築。HF token + 利用同意が必要。",
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
