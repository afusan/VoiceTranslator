"""OpenAiGptTranslatorBackend: OpenAI GPT(chat/completions)による LLM 翻訳。

役割: src_text を tgt_lang のテキストに翻訳する LLM 翻訳 backend。
モデルは GPT-4o-mini を既定とし、設定で変更可。
クラウド + 単一 API key パターン(ASR の OpenAI Whisper API と同じ key を
別保存する設計、共有は将来検討)。

設計判断:
- HTTP は httpx(extras `translator-openai-api`)
- プロンプト: system に翻訳指示、user に src_text。出力に説明を混ぜない
  よう明示する
- レスポンスは `choices[0].message.content` から取り出し、先頭/末尾の余計な
  説明トークン(例: `"訳: ..."`)があれば軽く除去する
- 失敗種別: 401/403 → FatalError、429/5xx → RecoverableError
"""

from __future__ import annotations

from voice_translator.common.errors import (
    FatalError,
    RecoverableError,
    SkipError,
)
from voice_translator.common.languages import iso1_to_iso3, language_name
from voice_translator.common.types import (
    BackendCapabilities,
    CredentialField,
    ModelStatus,
    VerifyResult,
)

from .backend import TranslatorBackend


_API_URL: str = "https://api.openai.com/v1/chat/completions"
_MODELS_URL: str = "https://api.openai.com/v1/models"
_DEFAULT_MODEL: str = "gpt-4o-mini"


# LLM はほぼ何でも翻訳できるので、UI 表示の安定のため共通言語テーブルの主要
# メジャー言語を返す。本当の対応は実 API でしか分からないが、UI 選択肢としては
# このセットで十分。
_SUPPORTED_TARGET_LANGUAGES: tuple[str, ...] = (
    "ar", "bg", "bn", "ca", "cs", "da", "de", "el", "en", "es",
    "et", "fa", "fi", "fr", "gu", "he", "hi", "hr", "hu", "id",
    "it", "ja", "ko", "lt", "lv", "ms", "nl", "no", "pl", "pt",
    "ro", "ru", "sk", "sl", "sr", "sv", "ta", "te", "th", "tl",
    "tr", "uk", "ur", "vi", "zh",
)


def _build_messages(src_text: str, src_lang: str, tgt_lang: str) -> list[dict]:
    """翻訳プロンプトを組み立てる。LLM 共通(GPT/Claude)。"""
    src_label = language_name(src_lang) if src_lang and src_lang != "auto" else "the source language"
    tgt_label = language_name(tgt_lang)
    system = (
        f"You are a professional translator. Translate the user's message "
        f"from {src_label} to {tgt_label}. "
        "Output ONLY the translation, no explanation, no quotes, no prefixes."
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": src_text},
    ]


def _strip_translation_prefix(text: str) -> str:
    """LLM がたまに付ける「Translation: ...」「訳: ...」等の先頭ノイズを軽く除去。"""
    cleaned = text.strip()
    for prefix in ("Translation:", "訳:", "翻訳:", "Translated:", "Output:"):
        if cleaned.startswith(prefix):
            cleaned = cleaned[len(prefix):].strip()
    return cleaned


class OpenAiGptTranslatorBackend(TranslatorBackend):
    """OpenAI GPT を呼ぶ LLM 翻訳 backend。"""

    _TIMEOUT_SEC: float = 60.0

    def __init__(
        self, *, api_key: str | None = None, model: str = _DEFAULT_MODEL,
    ) -> None:
        super().__init__()
        self._api_key = (api_key or "").strip()
        self._model = model
        if not self._api_key:
            self._set_status(ModelStatus.MISSING_CREDENTIALS)
            self._client = None
            return

        try:
            import httpx  # type: ignore
        except Exception as e:  # noqa: BLE001
            raise FatalError(
                f"httpx のロードに失敗"
                f"(`uv sync --extra translator-openai-api` で追加してください): {e}",
                cause=e,
            ) from e
        self._client = httpx.Client(timeout=self._TIMEOUT_SEC)
        self._set_status(ModelStatus.LOADED)

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
            return VerifyResult(ok=False, message=f"httpx 未インストール: {e}")
        try:
            resp = httpx.get(
                _MODELS_URL,
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

    def translate(self, src_text: str, src_lang: str, tgt_lang: str) -> str:
        if self._client is None:
            raise FatalError("OpenAI GPT translator が未初期化(API Key 未設定)")
        text = (src_text or "").strip()
        if not text:
            return ""

        payload = {
            "model": self._model,
            "messages": _build_messages(text, src_lang, tgt_lang),
            "temperature": 0.2,  # 翻訳のばらつきを抑える
        }
        try:
            resp = self._client.post(
                _API_URL,
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
        except Exception as e:  # noqa: BLE001
            self.record_error(e, context="OpenAI GPT request")
            raise RecoverableError(
                f"OpenAI GPT リクエスト失敗: {e}", cause=e,
            ) from e

        if resp.status_code in (401, 403):
            raise FatalError(
                f"OpenAI 認証エラー (HTTP {resp.status_code})"
            )
        if resp.status_code in (429, 500, 502, 503, 504):
            raise RecoverableError(
                f"OpenAI API 一時障害 (HTTP {resp.status_code})"
            )
        if resp.status_code != 200:
            raise FatalError(
                f"OpenAI API 異常応答 (HTTP {resp.status_code}): {resp.text[:200]}"
            )

        try:
            body = resp.json()
            content = body["choices"][0]["message"]["content"]
        except Exception as e:  # noqa: BLE001
            raise FatalError(
                f"OpenAI GPT レスポンス解析失敗: {e}", cause=e,
            ) from e

        result = _strip_translation_prefix(content)
        if not result:
            raise SkipError("GPT 翻訳結果が空です")
        return result

    def capabilities(self) -> BackendCapabilities:
        return BackendCapabilities(
            supported_languages=tuple(sorted(iso1_to_iso3(c) for c in _SUPPORTED_TARGET_LANGUAGES)),
            requires_gpu=False,
            is_cloud=True,
            requires_credentials=True,
            service_name="OpenAI GPT API",
            terms_url="https://openai.com/policies/terms-of-use",
            notes=f"OpenAI {self._model}。LLM 翻訳(temperature=0.2)。",
        )

    @classmethod
    def supported_target_languages(cls) -> list[str]:
        # 内部表は 639-1 のまま据え置き、申告は正準(639-3)へ持ち上げる。
        return sorted(iso1_to_iso3(c) for c in _SUPPORTED_TARGET_LANGUAGES)
