"""GoogleSttAsrBackend: Google Cloud Speech-to-Text による書き起こし(クラウド)。

役割: 発話単位の PCM(16kHz/mono/float32)を Google Cloud STT
(`recognize` 同期 API)に投げて書き起こす。
クラウド + サービスアカウント JSON ファイル認証パターン。

設計判断:
- 認証: サービスアカウント JSON ファイルパスを CredentialsStore に保存する
  (実体は GOOGLE_APPLICATION_CREDENTIALS 相当)
- 同期 `recognize()` を使う(streaming は本ブランチ対象外)
- 言語コードは ISO 639-1 ("en") を BCP-47 ("en-US") に変換して API に渡す
- `supports_auto_detect = False`(detect_language は別 API、本ブランチでは扱わない)
- 失敗種別:
  - 認証エラー(InvalidArgument 等)→ FatalError
  - レート/一時障害 → RecoverableError
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
from voice_translator.common.languages import iso1_to_iso3, iso3_to_iso1
from voice_translator.common.types import (
    INTERNAL_SAMPLE_RATE,
    BackendCapabilities,
    CredentialField,
    ModelStatus,
    VerifyResult,
)

from .backend import AsrBackend


# Google STT の代表対応言語(BCP-47 へのマッピング)。
# 公式対応はもっと多いが、本アプリで実用的なメジャー言語を採録。
# 上流追加時はここを追従する。
_ISO_TO_BCP47: dict[str, str] = {
    "en": "en-US", "ja": "ja-JP", "zh": "zh-CN", "ko": "ko-KR",
    "es": "es-ES", "fr": "fr-FR", "de": "de-DE", "it": "it-IT",
    "pt": "pt-PT", "ru": "ru-RU", "ar": "ar-SA", "hi": "hi-IN",
    "th": "th-TH", "vi": "vi-VN", "id": "id-ID", "tr": "tr-TR",
    "nl": "nl-NL", "pl": "pl-PL", "uk": "uk-UA", "sv": "sv-SE",
    "cs": "cs-CZ", "da": "da-DK", "fi": "fi-FI", "no": "no-NO",
    "el": "el-GR", "he": "iw-IL", "hu": "hu-HU", "ro": "ro-RO",
    "bg": "bg-BG", "ca": "ca-ES",
}

# 上の辞書から自動生成する「対応言語コード(ISO 639-1)」一覧。
# 申告は正準(639-3)に持ち上げる(下の supported_input_languages 参照)。
_SUPPORTED_INPUT_LANGUAGES: tuple[str, ...] = tuple(sorted(_ISO_TO_BCP47.keys()))


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


def _iso_to_bcp47(code: str) -> str:
    """ISO 639-1 → BCP-47。未掲載は en-US にフォールバック(API 側でエラーになるよりはマシ)。"""
    return _ISO_TO_BCP47.get(code, "en-US")


class GoogleSttAsrBackend(AsrBackend):
    """Google Cloud Speech-to-Text(同期 recognize)を使うクラウド backend。"""

    def __init__(
        self,
        *,
        credentials_path: str | None = None,
        default_language: str = "eng",
    ) -> None:
        super().__init__()  # status=INIT
        # default_language は src_lang_hint="auto" のとき API に渡すデフォルト言語
        # (Google STT は言語必須なので、auto を本 backend では「default 言語で投げる」と読み替える)。
        # 内部コードは正準(639-3)。legacy 639-1 を渡されても受けられるよう正準化する。
        self._default_language = iso1_to_iso3(default_language)
        self._credentials_path = (credentials_path or "").strip()

        if not self._credentials_path:
            self._set_status(ModelStatus.MISSING_CREDENTIALS)
            self._client = None
            return

        # 重い依存はここで初めて import
        try:
            from google.cloud import speech  # type: ignore
            from google.oauth2 import service_account  # type: ignore
        except Exception as e:  # noqa: BLE001
            raise FatalError(
                f"google-cloud-speech のロードに失敗"
                f"(`uv sync --extra asr-google-stt` で追加してください): {e}",
                cause=e,
            ) from e

        try:
            creds = service_account.Credentials.from_service_account_file(
                self._credentials_path
            )
            self._client = speech.SpeechClient(credentials=creds)
            self._speech_module = speech
        except FileNotFoundError as e:
            raise FatalError(
                f"サービスアカウント JSON が見つかりません: {self._credentials_path}",
                cause=e,
            ) from e
        except Exception as e:  # noqa: BLE001
            self.record_error(e, context="google STT client init")
            raise FatalError(
                f"Google STT クライアントの初期化に失敗: {e}", cause=e,
            ) from e

        self._set_status(ModelStatus.LOADED)

    # ============================================================
    # 認証情報フロー
    # ============================================================
    @classmethod
    def credential_spec(cls) -> list[CredentialField]:
        return [
            CredentialField(
                key_name="credentials_path",
                label="サービスアカウント JSON",
                secret=False,  # パス自体は秘匿情報ではない(ファイルの中身が秘匿)
                field_type="file",
                file_extensions=(("JSON", "*.json"), ("All", "*.*")),
                help_text=(
                    "Google Cloud Console で発行したサービスアカウント鍵 (JSON) "
                    "のファイルパス。Speech-to-Text API の有効化が必要。"
                ),
            ),
        ]

    @classmethod
    def verify_credentials(cls, values: dict[str, str]) -> VerifyResult:
        path = (values or {}).get("credentials_path", "").strip()
        if not path:
            return VerifyResult(ok=False, message="JSON ファイルが未指定です")
        try:
            from google.cloud import speech  # type: ignore
            from google.oauth2 import service_account  # type: ignore
        except Exception as e:  # noqa: BLE001
            return VerifyResult(
                ok=False, message=f"google-cloud-speech 未インストール: {e}"
            )
        try:
            creds = service_account.Credentials.from_service_account_file(path)
            # クライアントの初期化が通れば認証 OK と判定(API の小さな呼び出しは課金や
            # 権限境界が読みづらいため、ここではしない)。
            speech.SpeechClient(credentials=creds)
        except FileNotFoundError:
            return VerifyResult(ok=False, message=f"ファイルが見つかりません: {path}")
        except ValueError as e:
            # JSON 形式不正 / 必要キー欠落 など
            return VerifyResult(ok=False, message=f"JSON 形式が不正: {e}")
        except Exception as e:  # noqa: BLE001
            return VerifyResult(ok=False, message=f"Google STT 認証失敗: {e}")
        return VerifyResult(ok=True, message="Google STT 認証 OK")

    # ============================================================
    # I/F
    # ============================================================
    def transcribe(self, pcm: Any, src_lang_hint: str = "auto") -> tuple[str, str]:
        if self._client is None:
            raise FatalError("Google STT backend が未初期化(JSON 未設定)")
        if pcm is None or (hasattr(pcm, "size") and pcm.size == 0):
            raise SkipError("ASR入力PCMが空です")

        wav_bytes = _pcm_to_wav_bytes(pcm)
        # auto なら default_language を使う(Google STT は language_code 必須)。
        # iso は正準(639-3)で扱い、BCP-47 変換表(639-1 キー)に渡す直前で 639-1 に落とす。
        iso = (
            src_lang_hint
            if src_lang_hint not in ("auto", "", None)
            else self._default_language
        )
        bcp47 = _iso_to_bcp47(iso3_to_iso1(iso))

        speech = self._speech_module
        try:
            audio = speech.RecognitionAudio(content=wav_bytes)
            config = speech.RecognitionConfig(
                encoding=speech.RecognitionConfig.AudioEncoding.LINEAR16,
                sample_rate_hertz=INTERNAL_SAMPLE_RATE,
                language_code=bcp47,
            )
            response = self._client.recognize(config=config, audio=audio)
        except Exception as e:  # noqa: BLE001
            self.record_error(e, context="google STT recognize")
            # google.api_core.exceptions は import なしで isinstance チェックを避けるため、
            # 型名で判断する(InvalidArgument / Unauthenticated / PermissionDenied は Fatal、
            # それ以外のネット系は Recoverable に倒す)。
            exc_name = type(e).__name__
            if exc_name in ("InvalidArgument", "Unauthenticated", "PermissionDenied"):
                raise FatalError(
                    f"Google STT 認証/権限エラー ({exc_name}): {e}", cause=e,
                ) from e
            raise RecoverableError(
                f"Google STT 一時障害 ({exc_name}): {e}", cause=e,
            ) from e

        # results は複数候補を含むが、最初の alternative の transcript を採用
        text_parts: list[str] = []
        for result in getattr(response, "results", []) or []:
            alts = getattr(result, "alternatives", None) or []
            if alts:
                text_parts.append(str(alts[0].transcript).strip())
        text = " ".join(p for p in text_parts if p).strip()
        return text, iso

    # ----------------------------------------------------------
    def capabilities(self) -> BackendCapabilities:
        return BackendCapabilities(
            supported_languages=(),
            requires_gpu=False,
            is_cloud=True,
            requires_credentials=True,
            service_name="Google Cloud Speech-to-Text",
            terms_url="https://cloud.google.com/speech-to-text",
            notes="Google Cloud STT(同期 recognize)。サービスアカウント JSON で認証。",
        )

    # ----------------------------------------------------------
    # 対応言語の宣言(UI の言語プルダウン連動用)
    # ----------------------------------------------------------
    @classmethod
    def supported_input_languages(cls) -> list[str]:
        # 元リストは 639-1。正準(639-3)へ持ち上げて申告する。
        return sorted(iso1_to_iso3(c) for c in _SUPPORTED_INPUT_LANGUAGES)

    @classmethod
    def supports_auto_detect(cls) -> bool:
        # detect_language API は別呼び出し(レイテンシ増)。本ブランチでは未対応。
        return False
