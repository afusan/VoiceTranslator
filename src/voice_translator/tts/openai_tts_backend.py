"""OpenAiTtsBackend: OpenAI TTS API(クラウド)。

役割: OpenAI `/v1/audio/speech` エンドポイントにテキストを送り、PCM を取得して
(float32 PCM, samplerate) を返す。response_format=pcm 指定で 24kHz mono
signed 16-bit PCM が WAV ヘッダなしで直接返るため、デコード不要。

認証: API key(Bearer)。`openai_gpt` / `openai_whisper_api` とは別保存
(将来の共有化は別ブランチで検討)。
"""

from __future__ import annotations

import numpy as np

from voice_translator.common.errors import FatalError, RecoverableError, SkipError
from voice_translator.common.languages import iso1_to_iso3
from voice_translator.common.types import (
    BackendCapabilities,
    CredentialField,
    ModelStatus,
    VerifyResult,
)
from voice_translator.common.whisper_languages import WHISPER_INPUT_LANGUAGES

from .backend import TtsBackend

_API_URL = "https://api.openai.com/v1/audio/speech"
_MODELS_URL = "https://api.openai.com/v1/models"
_DEFAULT_VOICE = "alloy"
_DEFAULT_MODEL = "tts-1"
_SAMPLERATE = 24000  # response_format=pcm は 24kHz 固定

# layer_settings_schema の dropdown 候補
SUPPORTED_VOICES: tuple[str, ...] = (
    "alloy", "echo", "fable", "onyx", "nova", "shimmer",
)
SUPPORTED_MODELS: tuple[str, ...] = ("tts-1", "tts-1-hd")


class OpenAiTtsBackend(TtsBackend):
    """OpenAI TTS バックエンド(クラウド / API key / プリメイド 6 voice)。"""

    @classmethod
    def supported_output_languages(cls) -> list[str]:
        """OpenAI TTS は Whisper と同等の言語カバレッジ(50+ 言語)。

        共有リスト `WHISPER_INPUT_LANGUAGES` は 639-1 なので、申告は正準(639-3)へ
        持ち上げる。synthesize は入力テキストから言語を推定し tgt_lang を使わないため、
        ここでの申告変換のみで足りる。
        """
        return sorted(
            iso1_to_iso3(code)
            for code in WHISPER_INPUT_LANGUAGES
            if code != "auto"
        )

    @classmethod
    def credential_spec(cls) -> list[CredentialField]:
        return [
            CredentialField(
                key_name="api_key",
                label="OpenAI API Key",
                help_text="OpenAI ダッシュボード → API keys から発行。`sk-...` で始まる。",
                secret=True,
            ),
        ]

    @classmethod
    def verify_credentials(cls, values: dict[str, str]) -> VerifyResult:
        """`GET /v1/models` で疎通確認(モデル一覧を引くだけで疎通検証になる)。"""
        api_key = (values or {}).get("api_key", "").strip()
        if not api_key:
            return VerifyResult(ok=False, message="API Key が未設定です")
        try:
            import httpx  # type: ignore
        except Exception as e:  # noqa: BLE001
            return VerifyResult(
                ok=False,
                message=f"httpx が未インストール: {e}. `uv sync --extra tts-openai-api`",
            )
        try:
            r = httpx.get(
                _MODELS_URL,
                headers={"Authorization": f"Bearer {api_key}"},
                timeout=10.0,
            )
        except Exception as e:  # noqa: BLE001
            return VerifyResult(ok=False, message=f"通信失敗: {e}")
        if r.status_code in (401, 403):
            return VerifyResult(ok=False, message=f"API Key が無効 (HTTP {r.status_code})")
        if r.status_code >= 400:
            return VerifyResult(
                ok=False, message=f"HTTP {r.status_code}: {r.text[:200]}",
            )
        return VerifyResult(ok=True, message="認証 OK")

    def __init__(
        self,
        *,
        api_key: str | None = None,
        voice: str = _DEFAULT_VOICE,
        model: str = _DEFAULT_MODEL,
    ) -> None:
        super().__init__()  # BackendBase: status=INIT
        self._voice = voice
        self._model = model
        self._api_key = api_key.strip() if api_key else None

        # 遅延 import
        try:
            import httpx  # type: ignore  # noqa: F401
        except Exception as e:  # noqa: BLE001
            self.record_error(e, context="httpx import")
            raise FatalError(
                f"httpx のロードに失敗: {e}. "
                "`uv sync --extra tts-openai-api` でインストールしてください",
                cause=e,
            ) from e

        if not self._api_key:
            # gate 用に MISSING_CREDENTIALS を立てる(Start ブロック)
            self._set_status(ModelStatus.MISSING_CREDENTIALS)
            return
        self._set_status(ModelStatus.LOADED)

    # ----------------------------------------------------------
    def synthesize(self, text: str, tgt_lang: str) -> tuple[np.ndarray, int]:
        text = (text or "").strip()
        if not text:
            raise SkipError("TTS入力テキストが空です")
        if not self._api_key:
            raise FatalError("OpenAI TTS: API Key が未設定です")

        import httpx  # type: ignore

        try:
            r = httpx.post(
                _API_URL,
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": self._model,
                    "voice": self._voice,
                    "input": text,
                    "response_format": "pcm",
                },
                timeout=30.0,
            )
        except httpx.HTTPError as e:
            raise RecoverableError(
                f"OpenAI TTS 通信エラー: {e}", cause=e,
            ) from e

        if r.status_code in (401, 403):
            raise FatalError(
                f"OpenAI TTS: 認証エラー (HTTP {r.status_code}). "
                "API Key を確認してください",
            )
        if r.status_code == 429 or r.status_code >= 500:
            raise RecoverableError(
                f"OpenAI TTS: 一時障害 (HTTP {r.status_code})",
            )
        if r.status_code >= 400:
            raise FatalError(
                f"OpenAI TTS: HTTP {r.status_code}: {r.text[:200]}",
            )

        audio_bytes = r.content
        if not audio_bytes:
            raise SkipError("OpenAI TTS の出力が空です")

        pcm = np.frombuffer(audio_bytes, dtype=np.int16).astype(np.float32) / 32768.0
        return pcm, _SAMPLERATE

    # ----------------------------------------------------------
    def capabilities(self) -> BackendCapabilities:
        return BackendCapabilities(
            is_cloud=True,
            requires_credentials=True,
            service_name="OpenAI TTS",
            terms_url="https://openai.com/policies/terms-of-use",
            notes=f"OpenAI TTS, voice={self._voice}, model={self._model}, sr={_SAMPLERATE}",
        )
