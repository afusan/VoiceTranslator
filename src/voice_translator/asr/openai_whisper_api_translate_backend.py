"""OpenAiWhisperApiTranslateBackend: OpenAI Whisper API translations による ASR+翻訳複合(クラウド)。

役割: 発話 PCM を OpenAI の `POST /v1/audio/translations` に投げ、1 リクエストで
「書き起こし + 英語への翻訳」を行う複合バックエンド(ASR + Translator の 2 ロール)。
ローカルの `faster_whisper_translate` のクラウド版にあたる(同じ Whisper の translate タスク)。

制約(API の仕様):
- 翻訳先は **英語固定**(`supported_target_languages() == ["en"]`)。
- このエンドポイントは whisper-1 のみ対応・`language` パラメータは受けない。
- 源言語テキストは出力されない(`src_text` は空文字)。検出された源言語は
  verbose_json の `language` から取得を試みる(得られなければ "auto")。

API key・HTTP クライアント・認証フロー・エラー写像は `OpenAiWhisperApiAsrBackend` を継承する。
"""

from __future__ import annotations

from typing import Any

from voice_translator.common.errors import FatalError, RecoverableError, SkipError
from voice_translator.common.messages import PayloadKind
from voice_translator.common.types import BackendCapabilities, LayerKind

from .backend import AsrTranslatorBackend
from .openai_whisper_api_backend import (
    _API_FILE_SIZE_LIMIT_BYTES,
    _LANGUAGE_NAME_TO_CODE,
    OpenAiWhisperApiAsrBackend,
    _pcm_to_wav_bytes,
)


class OpenAiWhisperApiTranslateBackend(OpenAiWhisperApiAsrBackend, AsrTranslatorBackend):
    """OpenAI Whisper API(translations)で ASR+翻訳を一括実行する複合バックエンド。"""

    _TRANSLATIONS_URL: str = "https://api.openai.com/v1/audio/translations"

    # ---- 複合の契約 ----
    def transcribe_translate(
        self, pcm: Any, src_lang_hint: str = "auto", tgt_lang: str = "en"
    ) -> tuple[str, str, str, str]:
        """1 リクエストで書き起こし + 英語翻訳。(src_text="", src_lang, tgt_text, "en")。

        `tgt_lang` は無視する(API は英語固定。UI 側は `supported_target_languages()`
        により "en" 以外を選ばせない)。
        """
        if self._client is None:
            raise FatalError(
                "OpenAI Whisper API translate backend が未初期化(API Key 未設定)"
            )
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
        # translations は language パラメータ非対応(送らない)。
        data: dict[str, str] = {
            "model": self._model,
            "response_format": "verbose_json",
        }

        try:
            resp = self._client.post(
                self._TRANSLATIONS_URL,
                headers={"Authorization": f"Bearer {self._api_key}"},
                files=files,
                data=data,
            )
        except Exception as e:  # noqa: BLE001
            self.record_error(e, context="OpenAI Whisper API translations request")
            raise RecoverableError(
                f"OpenAI Whisper API translations リクエスト失敗: {e}", cause=e
            ) from e

        self._raise_for_api_status(resp)

        try:
            payload = resp.json()
        except Exception as e:  # noqa: BLE001
            raise FatalError(
                f"OpenAI API レスポンス JSON 解析失敗: {e}", cause=e
            ) from e

        tgt_text = str(payload.get("text", "")).strip()
        if src_lang_hint in ("auto", "", None):
            # verbose_json の language は検出された源言語の英語名が入る想定。
            # 取れない/未知の表記なら "auto" に縮退(表示用にしか使われない)。
            api_lang = str(payload.get("language", "")).lower()
            src_lang = _LANGUAGE_NAME_TO_CODE.get(
                api_lang, "auto" if not api_lang else api_lang
            )
        else:
            src_lang = src_lang_hint
        return "", src_lang, tgt_text, "en"

    @classmethod
    def supported_target_languages(cls) -> list[str]:
        """Whisper translations は英語のみ。"""
        return ["en"]

    # ---- 編成申告(MRO 先頭の単体 ASR 申告を上書き) ----
    @classmethod
    def covers_roles(cls) -> tuple[LayerKind, ...]:
        return (LayerKind.ASR, LayerKind.TRANSLATOR)

    @classmethod
    def produces_payload(cls) -> PayloadKind:
        return PayloadKind.TRANSLATED

    # ---- メタ情報 ----
    def capabilities(self) -> BackendCapabilities:
        return BackendCapabilities(
            supported_languages=(),
            requires_gpu=False,
            is_cloud=True,
            requires_credentials=True,
            service_name="OpenAI Whisper API (translate)",
            terms_url="https://openai.com/policies/terms-of-use",
            notes=(
                f"OpenAI {self._model} translations(ASR+翻訳の複合、英語固定)。"
                "25MB/req 制限。従量課金(音声分単位)。"
            ),
        )
