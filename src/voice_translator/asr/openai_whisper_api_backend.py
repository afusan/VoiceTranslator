"""OpenAiWhisperApiAsrBackend: OpenAI の Whisper API による書き起こし(クラウド)。

役割: 発話単位の PCM(16kHz/mono/float32)を OpenAI Whisper API
(`POST /v1/audio/transcriptions`)に投げて書き起こす。
クラウド + 単一 API key の最単純パターン。Phase D の同意ダイアログ +
Phase E-2 の認証フローに自動接続される。

設計判断:
- HTTP クライアントは `httpx`(extras `asr-openai-api` で opt-in)
- PCM は WAV 形式に変換して multipart で送信(API は WAV/MP3 等を受ける)
- 25MB / req の上限を超える PCM は明示エラー(自動分割は本ブランチ対象外)
- レスポンスは `verbose_json` で `text` と `language` を取り出す
- API レスポンスの language は英語名(`"english"`)で返るので ISO 639-1 に正規化
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


# Whisper API の上限(2025-01 時点で 25 MB)。実体はサーバ側で検証されるが、
# こちらで先に弾いたほうがユーザにとって早く明確なエラーになる。
_API_FILE_SIZE_LIMIT_BYTES: int = 25 * 1024 * 1024

# Whisper API が返す language の英語名 → ISO 639-1 への正規化(主要分のみ)。
# 未掲載の言語が返ってきたら英語名をそのまま返す(下流の判定で auto 扱いされる)。
_LANGUAGE_NAME_TO_CODE: dict[str, str] = {
    "english": "en", "japanese": "ja", "chinese": "zh", "korean": "ko",
    "spanish": "es", "french": "fr", "german": "de", "italian": "it",
    "portuguese": "pt", "russian": "ru", "arabic": "ar", "hindi": "hi",
    "thai": "th", "vietnamese": "vi", "indonesian": "id", "turkish": "tr",
    "dutch": "nl", "polish": "pl", "ukrainian": "uk", "swedish": "sv",
    "czech": "cs", "danish": "da", "finnish": "fi", "norwegian": "no",
    "greek": "el", "hebrew": "he", "hungarian": "hu", "romanian": "ro",
    "bulgarian": "bg", "catalan": "ca",
}


def _pcm_to_wav_bytes(pcm: np.ndarray, sample_rate: int = INTERNAL_SAMPLE_RATE) -> bytes:
    """float32 PCM を 16bit PCM WAV バイト列に変換する(in-memory)。

    Whisper API は WAV/MP3/M4A 等を受け付ける。送信サイズを抑えるため 16bit に量子化。
    """
    if pcm.dtype != np.int16:
        clipped = np.clip(pcm, -1.0, 1.0)
        pcm_i16 = (clipped * 32767.0).astype(np.int16)
    else:
        pcm_i16 = pcm
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)  # 16 bit
        wf.setframerate(sample_rate)
        wf.writeframes(pcm_i16.tobytes())
    return buf.getvalue()


class OpenAiWhisperApiAsrBackend(AsrBackend):
    """OpenAI Whisper API を呼ぶクラウド ASR backend。"""

    _API_URL: str = "https://api.openai.com/v1/audio/transcriptions"
    _MODELS_URL: str = "https://api.openai.com/v1/models"
    _DEFAULT_MODEL: str = "whisper-1"
    _TIMEOUT_SEC: float = 60.0

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str = _DEFAULT_MODEL,
    ) -> None:
        super().__init__()  # status=INIT
        self._model = model
        self._api_key = (api_key or "").strip()

        # API key が無ければ MISSING_CREDENTIALS。AppController._check_missing_credentials_gate
        # が start をブロックする。
        if not self._api_key:
            self._set_status(ModelStatus.MISSING_CREDENTIALS)
            self._client = None
            return

        # 重い依存(httpx)はここで初めて import する。設定ダイアログを開いただけで
        # 引きずらないようにするため。
        try:
            import httpx  # type: ignore
        except Exception as e:  # noqa: BLE001
            raise FatalError(
                f"httpx のロードに失敗(`uv sync --extra asr-openai-api` で追加してください): {e}",
                cause=e,
            ) from e

        self._httpx = httpx
        # 接続を使い回せるよう Client を 1 つ持つ。発話ごとに作り直すと TLS ハンドシェイクが
        # 毎回走るので遅くなる(API のレイテンシ実用域を保つため)。
        self._client = httpx.Client(timeout=self._TIMEOUT_SEC)
        self._set_status(ModelStatus.LOADED)

    # ============================================================
    # 認証情報フロー
    # ============================================================
    @classmethod
    def credential_spec(cls) -> list[CredentialField]:
        return [
            CredentialField(
                key_name="api_key",
                label="OpenAI API Key",
                secret=True,
                help_text="https://platform.openai.com/api-keys で発行(sk-... で始まる)",
            ),
        ]

    @classmethod
    def verify_credentials(cls, values: dict[str, str]) -> VerifyResult:
        api_key = (values or {}).get("api_key", "").strip()
        if not api_key:
            return VerifyResult(ok=False, message="OpenAI API Key が未入力です")
        try:
            import httpx  # type: ignore
        except Exception as e:  # noqa: BLE001
            return VerifyResult(
                ok=False, message=f"httpx 未インストール: {e}"
            )
        try:
            resp = httpx.get(
                cls._MODELS_URL,
                headers={"Authorization": f"Bearer {api_key}"},
                timeout=10.0,
            )
        except Exception as e:  # noqa: BLE001
            return VerifyResult(ok=False, message=f"OpenAI API 接続失敗: {e}")
        if resp.status_code == 200:
            return VerifyResult(ok=True, message="OpenAI API 認証 OK")
        if resp.status_code in (401, 403):
            return VerifyResult(ok=False, message="API Key が無効です")
        if resp.status_code == 429:
            return VerifyResult(ok=False, message="クォータ超過(レート/残高を確認)")
        return VerifyResult(
            ok=False, message=f"OpenAI API 応答異常: HTTP {resp.status_code}"
        )

    # ============================================================
    # I/F
    # ============================================================
    def transcribe(self, pcm: Any, src_lang_hint: str = "auto") -> tuple[str, str]:
        if self._client is None:
            raise FatalError("OpenAI Whisper API backend が未初期化(API Key 未設定)")
        if pcm is None or (hasattr(pcm, "size") and pcm.size == 0):
            raise SkipError("ASR入力PCMが空です")

        wav_bytes = _pcm_to_wav_bytes(pcm)
        if len(wav_bytes) > _API_FILE_SIZE_LIMIT_BYTES:
            raise FatalError(
                f"発話が API 上限 {_API_FILE_SIZE_LIMIT_BYTES // (1024 * 1024)}MB を超えました"
                f"(送信サイズ {len(wav_bytes) // (1024 * 1024)}MB)。"
                "短く区切るか、サイズの大きい発話には別 backend を使用してください。"
            )

        files = {"file": ("speech.wav", wav_bytes, "audio/wav")}
        data: dict[str, str] = {
            "model": self._model,
            "response_format": "verbose_json",
        }
        # language は ISO 639-1。auto のときは送らない(API 側で自動検出)。
        if src_lang_hint not in ("auto", "", None):
            data["language"] = src_lang_hint

        try:
            resp = self._client.post(
                self._API_URL,
                headers={"Authorization": f"Bearer {self._api_key}"},
                files=files,
                data=data,
            )
        except Exception as e:  # noqa: BLE001
            self.record_error(e, context="OpenAI Whisper API request")
            # ネットワーク系は RecoverableError(retry 機構が拾う)
            raise RecoverableError(
                f"OpenAI Whisper API リクエスト失敗: {e}", cause=e
            ) from e

        self._raise_for_api_status(resp)

        try:
            payload = resp.json()
        except Exception as e:  # noqa: BLE001
            raise FatalError(
                f"OpenAI API レスポンス JSON 解析失敗: {e}", cause=e
            ) from e

        text = str(payload.get("text", "")).strip()
        api_lang = str(payload.get("language", "")).lower()
        if src_lang_hint in ("auto", "", None):
            # API が返す英語名("english")を ISO 639-1 ("en") に正規化。
            # 未掲載コードは "auto" にフォールバック(下流は src_lang を表示用にしか使わない)。
            lang_out = _LANGUAGE_NAME_TO_CODE.get(api_lang, "auto" if not api_lang else api_lang)
        else:
            lang_out = src_lang_hint
        return text, lang_out

    # ----------------------------------------------------------
    @staticmethod
    def _raise_for_api_status(resp: Any) -> None:
        """OpenAI API の HTTP ステータスを severity 付き例外に写像する(200 は素通し)。

        401/403 → FatalError(恒久障害) / 429/5xx → RecoverableError(リトライ対象)。
        translations 系のサブクラスも同じ写像を使う。
        """
        if resp.status_code in (401, 403):
            raise FatalError(
                f"OpenAI API 認証エラー (HTTP {resp.status_code}): "
                "API Key が無効か取り消されています"
            )
        if resp.status_code in (429, 500, 502, 503, 504):
            raise RecoverableError(
                f"OpenAI API 一時障害 (HTTP {resp.status_code})"
            )
        if resp.status_code != 200:
            raise FatalError(
                f"OpenAI API 異常応答 (HTTP {resp.status_code}): {resp.text[:200]}"
            )

    # ----------------------------------------------------------
    def capabilities(self) -> BackendCapabilities:
        return BackendCapabilities(
            supported_languages=(),
            requires_gpu=False,
            is_cloud=True,
            requires_credentials=True,
            service_name="OpenAI Whisper API",
            terms_url="https://openai.com/policies/terms-of-use",
            notes=f"OpenAI {self._model}。25MB/req 制限。verbose_json レスポンス。",
        )

    # ----------------------------------------------------------
    # 対応言語の宣言(UI の言語プルダウン連動用)
    # ----------------------------------------------------------
    @classmethod
    def supported_input_languages(cls) -> list[str]:
        from voice_translator.common.whisper_languages import WHISPER_INPUT_LANGUAGES
        return list(WHISPER_INPUT_LANGUAGES)

    @classmethod
    def supports_auto_detect(cls) -> bool:
        return True
