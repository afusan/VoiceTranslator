"""DeepgramAsrBackend: Deepgram Nova-3 による書き起こし(クラウド、短期接続)。

役割: 発話単位の PCM(16kHz/mono/float32)を Deepgram の prerecorded API に
投げて書き起こす。WebSocket ストリーミングではなく「1 発話の PCM を短期接続で
送って同期で結果を待つ」運用で AsrBackend の同期 transcribe I/F に被せる。

設計判断:
- 真のストリーミング(逐次中間結果を ledger に流す)は別ブランチ対象外
- SDK: `deepgram-sdk` v3 系の同期 prerecorded API を使う
- verify は SDK ではなく httpx で `/v1/projects` を直接叩く
  (SDK の初期化だけだと無効キーでも通ってしまうため、明示的に API を叩く)
- レスポンスから text + detected_language を取り出す
"""

from __future__ import annotations

import io
import wave
from typing import Any

import numpy as np

from voice_translator.common.errors import (
    FatalError,
    RecoverableError,
    SkipError,
)
from voice_translator.common.types import (
    INTERNAL_SAMPLE_RATE,
    BackendCapabilities,
    CredentialField,
    ModelStatus,
    VerifyResult,
)

from .backend import AsrBackend


# Deepgram Nova-3 対応言語(主要)。公式の対応はもっと多いが、本アプリで実用的な
# メジャー言語を採録。上流追加時はここを追従する。
_DEEPGRAM_INPUT_LANGUAGES: tuple[str, ...] = (
    "en", "ja", "zh", "ko", "es", "fr", "de", "it", "pt", "ru",
    "ar", "hi", "th", "vi", "id", "tr", "nl", "pl", "uk", "sv",
    "cs", "da", "fi", "no", "el", "hu", "ro", "bg",
)


_PROJECTS_URL: str = "https://api.deepgram.com/v1/projects"


def _pcm_to_wav_bytes(pcm: np.ndarray) -> bytes:
    if pcm.dtype != np.int16:
        clipped = np.clip(pcm, -1.0, 1.0)
        pcm_i16 = (clipped * 32767.0).astype(np.int16)
    else:
        pcm_i16 = pcm
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(INTERNAL_SAMPLE_RATE)
        wf.writeframes(pcm_i16.tobytes())
    return buf.getvalue()


class DeepgramAsrBackend(AsrBackend):
    """Deepgram prerecorded API を使うクラウド ASR backend(短期接続パターン)。"""

    _DEFAULT_MODEL: str = "nova-3"
    _TIMEOUT_SEC: float = 60.0

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str = _DEFAULT_MODEL,
    ) -> None:
        super().__init__()  # status=INIT
        self._api_key = (api_key or "").strip()
        self._model = model

        if not self._api_key:
            self._set_status(ModelStatus.MISSING_CREDENTIALS)
            self._dg_client = None
            self._dg_options_cls = None
            return

        try:
            from deepgram import DeepgramClient, PrerecordedOptions  # type: ignore
        except Exception as e:  # noqa: BLE001
            raise FatalError(
                f"deepgram-sdk のロードに失敗"
                f"(`uv sync --extra asr-deepgram` で追加してください): {e}",
                cause=e,
            ) from e

        try:
            self._dg_client = DeepgramClient(self._api_key)
        except Exception as e:  # noqa: BLE001
            self.record_error(e, context="DeepgramClient init")
            raise FatalError(
                f"Deepgram クライアントの初期化に失敗: {e}", cause=e,
            ) from e
        self._dg_options_cls = PrerecordedOptions
        self._set_status(ModelStatus.LOADED)

    # ============================================================
    # 認証情報フロー
    # ============================================================
    @classmethod
    def credential_spec(cls) -> list[CredentialField]:
        return [
            CredentialField(
                key_name="api_key",
                label="Deepgram API Key",
                secret=True,
                help_text="https://console.deepgram.com/ で発行(Token プレフィックス不要)",
            ),
        ]

    @classmethod
    def verify_credentials(cls, values: dict[str, str]) -> VerifyResult:
        api_key = (values or {}).get("api_key", "").strip()
        if not api_key:
            return VerifyResult(ok=False, message="Deepgram API Key が未入力です")
        try:
            import httpx  # type: ignore
        except Exception as e:  # noqa: BLE001
            return VerifyResult(
                ok=False, message=f"httpx 未インストール: {e}"
            )
        try:
            resp = httpx.get(
                _PROJECTS_URL,
                headers={"Authorization": f"Token {api_key}"},
                timeout=10.0,
            )
        except Exception as e:  # noqa: BLE001
            return VerifyResult(ok=False, message=f"Deepgram API 接続失敗: {e}")
        if resp.status_code == 200:
            return VerifyResult(ok=True, message="Deepgram API 認証 OK")
        if resp.status_code in (401, 403):
            return VerifyResult(ok=False, message="API Key が無効です")
        return VerifyResult(
            ok=False, message=f"Deepgram API 応答異常: HTTP {resp.status_code}"
        )

    # ============================================================
    # I/F
    # ============================================================
    def transcribe(self, pcm: Any, src_lang_hint: str = "auto") -> tuple[str, str]:
        if self._dg_client is None:
            raise FatalError("Deepgram backend が未初期化(API Key 未設定)")
        if pcm is None or (hasattr(pcm, "size") and pcm.size == 0):
            raise SkipError("ASR入力PCMが空です")

        wav_bytes = _pcm_to_wav_bytes(pcm)
        source = {"buffer": wav_bytes}

        # auto は detect_language=True で送り、明示言語指定は language= で送る。
        # Deepgram は両者の同時指定を受け付けないので排他的に組み立てる。
        opts_kwargs: dict[str, Any] = {
            "model": self._model,
            "smart_format": True,
        }
        if src_lang_hint in ("auto", "", None):
            opts_kwargs["detect_language"] = True
        else:
            opts_kwargs["language"] = src_lang_hint

        try:
            options = self._dg_options_cls(**opts_kwargs)
            response = self._dg_client.listen.rest.v("1").transcribe_file(
                source, options, timeout=self._TIMEOUT_SEC,
            )
        except Exception as e:  # noqa: BLE001
            self.record_error(e, context="Deepgram transcribe_file")
            # SDK 内エラーの型は版差があるので名前で大まかに分岐
            exc_name = type(e).__name__
            if "Auth" in exc_name or "Unauthorized" in exc_name:
                raise FatalError(
                    f"Deepgram 認証エラー ({exc_name}): {e}", cause=e,
                ) from e
            raise RecoverableError(
                f"Deepgram 一時障害 ({exc_name}): {e}", cause=e,
            ) from e

        # response.results.channels[0].alternatives[0].transcript を取り出す。
        # detected_language は channels[0].detected_language(auto 時)。
        try:
            results = response.results
            channel = results.channels[0]
            alt = channel.alternatives[0]
            text = str(alt.transcript or "").strip()
            detected = getattr(channel, "detected_language", None) or ""
        except Exception as e:  # noqa: BLE001
            raise FatalError(
                f"Deepgram レスポンスの解析失敗: {e}", cause=e,
            ) from e

        if src_lang_hint in ("auto", "", None):
            lang_out = detected or "auto"
        else:
            lang_out = src_lang_hint
        return text, lang_out

    # ----------------------------------------------------------
    def capabilities(self) -> BackendCapabilities:
        return BackendCapabilities(
            supported_languages=(),
            requires_gpu=False,
            is_cloud=True,
            requires_credentials=True,
            service_name="Deepgram",
            terms_url="https://deepgram.com/terms",
            notes=f"Deepgram {self._model}。prerecorded(短期接続)で同期 transcribe。",
        )

    # ----------------------------------------------------------
    @classmethod
    def supported_input_languages(cls) -> list[str]:
        return list(_DEEPGRAM_INPUT_LANGUAGES)

    @classmethod
    def supports_auto_detect(cls) -> bool:
        return True
