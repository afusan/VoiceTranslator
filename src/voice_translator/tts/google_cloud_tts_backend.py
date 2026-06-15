"""GoogleCloudTtsBackend: Google Cloud Text-to-Speech(クラウド)。

役割: Google Cloud TTS SDK でテキストを LINEAR16 PCM (16kHz mono) に合成し、
(float32 PCM, samplerate) を返す。

認証: サービスアカウント JSON ファイル(`google_stt` と同形式、`field_type=file`)。
GCP プロジェクトで TTS API を有効化しておく必要がある。
"""

from __future__ import annotations

import numpy as np

from voice_translator.common.errors import FatalError, RecoverableError, SkipError
from voice_translator.common.languages import iso1_to_iso3, iso3_to_iso1
from voice_translator.common.types import (
    BackendCapabilities,
    CredentialField,
    ModelStatus,
    VerifyResult,
)

from .backend import TtsBackend

_SAMPLERATE = 16000

# 内部コード(ISO 639-1) → Google TTS BCP-47 言語コードの代表マッピング。
# voice_name を空にする場合のデフォルト言語選択に使う。表は 639-1 キーのまま据え置き、
# synthesize 側で正準(639-3)の tgt_lang を 639-1 へ落としてから引く。
_ISO_TO_BCP47 = {
    "en": "en-US", "ja": "ja-JP", "zh": "cmn-CN", "ko": "ko-KR",
    "fr": "fr-FR", "de": "de-DE", "es": "es-ES", "it": "it-IT",
    "pt": "pt-BR", "ru": "ru-RU", "nl": "nl-NL", "pl": "pl-PL",
    "tr": "tr-TR", "ar": "ar-XA", "hi": "hi-IN", "id": "id-ID",
    "th": "th-TH", "vi": "vi-VN", "sv": "sv-SE", "da": "da-DK",
    "no": "nb-NO", "fi": "fi-FI", "el": "el-GR", "he": "he-IL",
    "cs": "cs-CZ", "hu": "hu-HU", "ro": "ro-RO", "sk": "sk-SK",
    "uk": "uk-UA", "bg": "bg-BG",
}


class GoogleCloudTtsBackend(TtsBackend):
    """Google Cloud TTS バックエンド(クラウド / サービスアカウント JSON)。"""

    @classmethod
    def supported_output_languages(cls) -> list[str]:
        """Google Cloud TTS が voice を提供する主要言語を正準(ISO 639-3)で返す。

        内部表 `_ISO_TO_BCP47` は 639-1 キーのまま据え置き、申告境界で 639-3 へ持ち上げる。
        """
        return sorted(iso1_to_iso3(c) for c in _ISO_TO_BCP47)

    @classmethod
    def credential_spec(cls) -> list[CredentialField]:
        return [
            CredentialField(
                key_name="credentials_path",
                label="Service Account JSON ファイル",
                help_text="GCP コンソール → IAM → サービスアカウント → JSON ダウンロード。",
                secret=False,
                field_type="file",
                file_extensions=(("JSON", "*.json"), ("All Files", "*.*")),
            ),
        ]

    @classmethod
    def verify_credentials(cls, values: dict[str, str]) -> VerifyResult:
        """サービスアカウント JSON をロード → `list_voices` で疎通確認。"""
        creds_path = (values or {}).get("credentials_path", "").strip()
        if not creds_path:
            return VerifyResult(ok=False, message="サービスアカウント JSON が未設定です")
        try:
            from google.cloud import texttospeech  # type: ignore
            from google.oauth2 import service_account as oauth_sa  # type: ignore
        except Exception as e:  # noqa: BLE001
            return VerifyResult(
                ok=False,
                message=(
                    f"google-cloud-texttospeech が未インストール: {e}. "
                    "`uv sync --extra tts-google`"
                ),
            )
        try:
            credentials = oauth_sa.Credentials.from_service_account_file(creds_path)
        except (ValueError, FileNotFoundError) as e:
            return VerifyResult(ok=False, message=f"JSON ファイルが無効: {e}")
        except Exception as e:  # noqa: BLE001
            return VerifyResult(ok=False, message=f"認証情報のロード失敗: {e}")
        try:
            client = texttospeech.TextToSpeechClient(credentials=credentials)
            client.list_voices(timeout=10.0)
        except Exception as e:  # noqa: BLE001
            return VerifyResult(ok=False, message=f"疎通失敗: {e}")
        return VerifyResult(ok=True, message="認証 OK")

    def __init__(
        self,
        *,
        credentials_path: str | None = None,
        voice_name: str = "",
        default_language: str = "en",
    ) -> None:
        super().__init__()
        self._credentials_path = (credentials_path or "").strip() or None
        self._voice_name = voice_name.strip()
        # default_language は内部コード扱い。legacy 639-1 設定値も受け付けるため正準(639-3)へ。
        self._default_language = iso1_to_iso3(default_language)

        # 遅延 import
        try:
            from google.cloud import texttospeech  # type: ignore  # noqa: F401
            from google.oauth2 import service_account as oauth_sa  # type: ignore
        except Exception as e:  # noqa: BLE001
            self.record_error(e, context="google-cloud-texttospeech import")
            raise FatalError(
                f"google-cloud-texttospeech のロードに失敗: {e}. "
                "`uv sync --extra tts-google` でインストールしてください",
                cause=e,
            ) from e

        if not self._credentials_path:
            self._set_status(ModelStatus.MISSING_CREDENTIALS)
            self._client = None
            return

        try:
            credentials = oauth_sa.Credentials.from_service_account_file(
                self._credentials_path
            )
            self._client = texttospeech.TextToSpeechClient(credentials=credentials)
        except Exception as e:  # noqa: BLE001
            self.record_error(e, context="google tts client init")
            raise FatalError(
                f"Google Cloud TTS クライアント初期化失敗: {e}", cause=e,
            ) from e

        self._set_status(ModelStatus.LOADED)

    # ----------------------------------------------------------
    def synthesize(self, text: str, tgt_lang: str) -> tuple[np.ndarray, int]:
        text = (text or "").strip()
        if not text:
            raise SkipError("TTS入力テキストが空です")
        if self._client is None:
            raise FatalError("Google Cloud TTS: 認証情報が未設定です")

        from google.cloud import texttospeech  # type: ignore

        # tgt_lang / default_language は正準(639-3)。BCP-47 表は 639-1 キーなので落として引く。
        canonical = (tgt_lang or self._default_language)
        lang_iso = iso3_to_iso1(canonical).lower()
        bcp47 = _ISO_TO_BCP47.get(lang_iso, "en-US")

        synthesis_input = texttospeech.SynthesisInput(text=text)
        if self._voice_name:
            voice = texttospeech.VoiceSelectionParams(
                language_code=bcp47, name=self._voice_name,
            )
        else:
            # voice 未指定なら language_code のみ(Google が既定 voice を割り当てる)
            voice = texttospeech.VoiceSelectionParams(language_code=bcp47)
        audio_config = texttospeech.AudioConfig(
            audio_encoding=texttospeech.AudioEncoding.LINEAR16,
            sample_rate_hertz=_SAMPLERATE,
        )
        try:
            response = self._client.synthesize_speech(
                input=synthesis_input, voice=voice, audio_config=audio_config,
                timeout=30.0,
            )
        except Exception as e:  # noqa: BLE001
            # GCP の例外型は複雑なので、ここでは message にコードを残して
            # 上位の ErrorHandler に判断させる。401/403 相当は基本的に
            # 起動時に弾かれるので、ここでは Recoverable 寄り。
            msg = str(e)
            if "PERMISSION_DENIED" in msg or "UNAUTHENTICATED" in msg:
                raise FatalError(f"Google Cloud TTS: 認証エラー: {e}", cause=e) from e
            raise RecoverableError(f"Google Cloud TTS: 一時障害: {e}", cause=e) from e

        audio_bytes = response.audio_content
        if not audio_bytes:
            raise SkipError("Google Cloud TTS の出力が空です")

        pcm = np.frombuffer(audio_bytes, dtype=np.int16).astype(np.float32) / 32768.0
        return pcm, _SAMPLERATE

    # ----------------------------------------------------------
    def capabilities(self) -> BackendCapabilities:
        return BackendCapabilities(
            is_cloud=True,
            requires_credentials=True,
            service_name="Google Cloud Text-to-Speech",
            terms_url="https://cloud.google.com/text-to-speech",
            notes=(
                f"Google Cloud TTS (LINEAR16, sr={_SAMPLERATE}). "
                f"voice_name={self._voice_name or '(default)'}"
            ),
        )
