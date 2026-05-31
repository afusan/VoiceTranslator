"""ElevenLabsTtsBackend: ElevenLabs TTS API(クラウド)。

役割: ElevenLabs `/v1/text-to-speech/{voice_id}` エンドポイントにテキストを送り、
PCM を取得して (float32 PCM, samplerate) を返す。
`output_format=pcm_16000` 指定で 16kHz mono signed 16-bit PCM が WAV ヘッダ
なしで直接返るため、MP3 デコード不要。

認証: API key(`xi-api-key` ヘッダ)。プリメイド voice(Rachel / Adam 等)を
voice_id で指定する。クローニング voice は本ブランチでは未対応
(pendList [⏳保留 2026-05-31] / 別ブランチ `feature/tts-voice-cloning`)。
"""

from __future__ import annotations

import numpy as np

from voice_translator.common.errors import FatalError, RecoverableError, SkipError
from voice_translator.common.types import (
    BackendCapabilities,
    CredentialField,
    ModelStatus,
    VerifyResult,
)

from .backend import TtsBackend

_API_BASE = "https://api.elevenlabs.io/v1"
# プリメイド voice の代表(無料 tier でも使える):
# Rachel(英語女性、汎用)。voice_id は ElevenLabs ダッシュボードから確認可能。
_DEFAULT_VOICE_ID = "21m00Tcm4TlvDq8ikWAM"  # Rachel
_DEFAULT_MODEL = "eleven_multilingual_v2"
_SAMPLERATE = 16000  # output_format=pcm_16000

# layer_settings_schema の dropdown 候補
SUPPORTED_MODELS: tuple[str, ...] = (
    "eleven_multilingual_v2",
    "eleven_turbo_v2_5",
    "eleven_monolingual_v1",
)


class ElevenLabsTtsBackend(TtsBackend):
    """ElevenLabs TTS バックエンド(クラウド / API key / プリメイド voice)。"""

    @classmethod
    def supported_output_languages(cls) -> list[str]:
        """`eleven_multilingual_v2` が公式に対応する 29 言語。

        他モデル(`eleven_turbo_v2_5` = 32 言語、`eleven_monolingual_v1` = 英語のみ)
        の差は宣言できないので、多言語モデル基準で広めに宣言する
        (英語専用モデルを選んでも UI 側で対応 list は変えない単純化)。
        """
        return [
            "ar", "bg", "cs", "da", "de", "el", "en", "es", "fi", "fil",
            "fr", "hi", "hr", "hu", "id", "it", "ja", "ko", "ms", "nl",
            "pl", "pt", "ro", "ru", "sk", "sv", "ta", "tr", "uk", "vi", "zh",
        ]

    @classmethod
    def credential_spec(cls) -> list[CredentialField]:
        return [
            CredentialField(
                key_name="api_key",
                label="ElevenLabs API Key",
                help_text="ElevenLabs ダッシュボード → Profile → API Key から取得。",
                secret=True,
            ),
        ]

    @classmethod
    def verify_credentials(cls, values: dict[str, str]) -> VerifyResult:
        """`GET /v1/voices` で疎通確認(voice 一覧取得 = 軽量 + 認証必須)。"""
        api_key = (values or {}).get("api_key", "").strip()
        if not api_key:
            return VerifyResult(ok=False, message="API Key が未設定です")
        try:
            import httpx  # type: ignore
        except Exception as e:  # noqa: BLE001
            return VerifyResult(
                ok=False,
                message=f"httpx が未インストール: {e}. `uv sync --extra tts-elevenlabs`",
            )
        try:
            r = httpx.get(
                f"{_API_BASE}/voices",
                headers={"xi-api-key": api_key},
                timeout=10.0,
            )
        except Exception as e:  # noqa: BLE001
            return VerifyResult(ok=False, message=f"通信失敗: {e}")
        if r.status_code in (401, 403):
            return VerifyResult(
                ok=False, message=f"API Key が無効 (HTTP {r.status_code})",
            )
        if r.status_code >= 400:
            return VerifyResult(
                ok=False, message=f"HTTP {r.status_code}: {r.text[:200]}",
            )
        return VerifyResult(ok=True, message="認証 OK")

    def __init__(
        self,
        *,
        api_key: str | None = None,
        voice_id: str = _DEFAULT_VOICE_ID,
        model_id: str = _DEFAULT_MODEL,
    ) -> None:
        super().__init__()
        self._voice_id = voice_id
        self._model_id = model_id
        self._api_key = api_key.strip() if api_key else None

        # 遅延 import
        try:
            import httpx  # type: ignore  # noqa: F401
        except Exception as e:  # noqa: BLE001
            self.record_error(e, context="httpx import")
            raise FatalError(
                f"httpx のロードに失敗: {e}. "
                "`uv sync --extra tts-elevenlabs` でインストールしてください",
                cause=e,
            ) from e

        if not self._api_key:
            self._set_status(ModelStatus.MISSING_CREDENTIALS)
            return
        self._set_status(ModelStatus.LOADED)

    # ----------------------------------------------------------
    def synthesize(self, text: str, tgt_lang: str) -> tuple[np.ndarray, int]:
        text = (text or "").strip()
        if not text:
            raise SkipError("TTS入力テキストが空です")
        if not self._api_key:
            raise FatalError("ElevenLabs: API Key が未設定です")

        import httpx  # type: ignore

        url = (
            f"{_API_BASE}/text-to-speech/{self._voice_id}"
            f"?output_format=pcm_{_SAMPLERATE}"
        )
        try:
            r = httpx.post(
                url,
                headers={
                    "xi-api-key": self._api_key,
                    "Content-Type": "application/json",
                    "Accept": "audio/pcm",
                },
                json={"text": text, "model_id": self._model_id},
                timeout=30.0,
            )
        except httpx.HTTPError as e:
            raise RecoverableError(
                f"ElevenLabs 通信エラー: {e}", cause=e,
            ) from e

        if r.status_code in (401, 403):
            raise FatalError(
                f"ElevenLabs: 認証エラー (HTTP {r.status_code})",
            )
        if r.status_code == 422:
            # voice_id が無効 / quota など固有エラー
            raise FatalError(
                f"ElevenLabs: 入力エラー (HTTP 422): {r.text[:300]}",
            )
        if r.status_code == 429 or r.status_code >= 500:
            raise RecoverableError(
                f"ElevenLabs: 一時障害 (HTTP {r.status_code})",
            )
        if r.status_code >= 400:
            raise FatalError(
                f"ElevenLabs: HTTP {r.status_code}: {r.text[:200]}",
            )

        audio_bytes = r.content
        if not audio_bytes:
            raise SkipError("ElevenLabs の出力が空です")

        pcm = np.frombuffer(audio_bytes, dtype=np.int16).astype(np.float32) / 32768.0
        return pcm, _SAMPLERATE

    # ----------------------------------------------------------
    def capabilities(self) -> BackendCapabilities:
        return BackendCapabilities(
            is_cloud=True,
            requires_credentials=True,
            service_name="ElevenLabs",
            terms_url="https://elevenlabs.io/terms-of-use",
            notes=(
                f"ElevenLabs TTS, voice_id={self._voice_id}, "
                f"model={self._model_id}, sr={_SAMPLERATE}"
            ),
        )
