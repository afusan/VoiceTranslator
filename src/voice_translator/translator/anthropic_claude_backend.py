"""AnthropicClaudeTranslatorBackend: Anthropic Claude による LLM 翻訳。

役割: src_text を tgt_lang のテキストに翻訳する LLM 翻訳 backend。
モデルは Claude 4.5 Haiku を既定とし、設定で変更可。
クラウド + 単一 API key パターン。GPT-4o-mini backend と並走させ、LLM 翻訳の
比較対象として用意する。

設計判断:
- HTTP は httpx(extras `translator-anthropic`)
- プロンプトは GPT と同じ構造(`_build_messages` 同等)
- Anthropic Messages API は system が messages の外、user/assistant のみ messages
- レスポンスは `content[0].text` から取り出し、prefix 除去
- model: `claude-haiku-4-5-20251001`(2026 時点の最新 Haiku)
"""

from __future__ import annotations

from voice_translator.common.errors import (
    FatalError,
    RecoverableError,
    SkipError,
)
from voice_translator.common.languages import language_name
from voice_translator.common.types import (
    BackendCapabilities,
    CredentialField,
    ModelStatus,
    VerifyResult,
)

from .backend import TranslatorBackend


_API_URL: str = "https://api.anthropic.com/v1/messages"
_API_VERSION: str = "2023-06-01"
_DEFAULT_MODEL: str = "claude-haiku-4-5-20251001"


_SUPPORTED_TARGET_LANGUAGES: tuple[str, ...] = (
    "ar", "bg", "bn", "ca", "cs", "da", "de", "el", "en", "es",
    "et", "fa", "fi", "fr", "gu", "he", "hi", "hr", "hu", "id",
    "it", "ja", "ko", "lt", "lv", "ms", "nl", "no", "pl", "pt",
    "ro", "ru", "sk", "sl", "sr", "sv", "ta", "te", "th", "tl",
    "tr", "uk", "ur", "vi", "zh",
)


def _build_prompt(src_text: str, src_lang: str, tgt_lang: str) -> tuple[str, list[dict]]:
    src_label = language_name(src_lang) if src_lang and src_lang != "auto" else "the source language"
    tgt_label = language_name(tgt_lang)
    system = (
        f"You are a professional translator. Translate the user's message "
        f"from {src_label} to {tgt_label}. "
        "Output ONLY the translation, no explanation, no quotes, no prefixes."
    )
    messages = [{"role": "user", "content": src_text}]
    return system, messages


def _strip_translation_prefix(text: str) -> str:
    cleaned = text.strip()
    for prefix in ("Translation:", "訳:", "翻訳:", "Translated:", "Output:"):
        if cleaned.startswith(prefix):
            cleaned = cleaned[len(prefix):].strip()
    return cleaned


class AnthropicClaudeTranslatorBackend(TranslatorBackend):
    """Anthropic Claude を呼ぶ LLM 翻訳 backend。"""

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
                f"(`uv sync --extra translator-anthropic` で追加してください): {e}",
                cause=e,
            ) from e
        self._client = httpx.Client(timeout=self._TIMEOUT_SEC)
        self._set_status(ModelStatus.LOADED)

    @classmethod
    def credential_spec(cls) -> list[CredentialField]:
        return [
            CredentialField(
                key_name="api_key",
                label="Anthropic API Key",
                secret=True,
                help_text="https://console.anthropic.com/settings/keys で発行(sk-ant-... で始まる)",
            ),
        ]

    @classmethod
    def verify_credentials(cls, values: dict[str, str]) -> VerifyResult:
        api_key = (values or {}).get("api_key", "").strip()
        if not api_key:
            return VerifyResult(ok=False, message="Anthropic API Key が未入力です")
        try:
            import httpx  # type: ignore
        except Exception as e:  # noqa: BLE001
            return VerifyResult(ok=False, message=f"httpx 未インストール: {e}")
        # Anthropic にはモデル一覧の GET API が無いので、最小の messages 呼び出しで疎通確認
        try:
            resp = httpx.post(
                _API_URL,
                headers={
                    "x-api-key": api_key,
                    "anthropic-version": _API_VERSION,
                    "Content-Type": "application/json",
                },
                json={
                    "model": _DEFAULT_MODEL,
                    "max_tokens": 1,
                    "messages": [{"role": "user", "content": "hi"}],
                },
                timeout=10.0,
            )
        except Exception as e:  # noqa: BLE001
            return VerifyResult(ok=False, message=f"Anthropic API 接続失敗: {e}")
        if resp.status_code == 200:
            return VerifyResult(ok=True, message="Anthropic API 認証 OK")
        if resp.status_code in (401, 403):
            return VerifyResult(ok=False, message="API Key が無効です")
        if resp.status_code == 429:
            return VerifyResult(ok=False, message="クォータ超過(レート/残高を確認)")
        return VerifyResult(
            ok=False, message=f"Anthropic API 応答異常: HTTP {resp.status_code}"
        )

    def translate(self, src_text: str, src_lang: str, tgt_lang: str) -> str:
        if self._client is None:
            raise FatalError("Anthropic Claude translator が未初期化(API Key 未設定)")
        text = (src_text or "").strip()
        if not text:
            return ""

        system, messages = _build_prompt(text, src_lang, tgt_lang)
        payload = {
            "model": self._model,
            "system": system,
            "messages": messages,
            "max_tokens": 1024,
            "temperature": 0.2,
        }
        try:
            resp = self._client.post(
                _API_URL,
                headers={
                    "x-api-key": self._api_key,
                    "anthropic-version": _API_VERSION,
                    "Content-Type": "application/json",
                },
                json=payload,
            )
        except Exception as e:  # noqa: BLE001
            self.record_error(e, context="Anthropic request")
            raise RecoverableError(
                f"Anthropic リクエスト失敗: {e}", cause=e,
            ) from e

        if resp.status_code in (401, 403):
            raise FatalError(
                f"Anthropic 認証エラー (HTTP {resp.status_code})"
            )
        if resp.status_code in (429, 500, 502, 503, 504):
            raise RecoverableError(
                f"Anthropic API 一時障害 (HTTP {resp.status_code})"
            )
        if resp.status_code != 200:
            raise FatalError(
                f"Anthropic API 異常応答 (HTTP {resp.status_code}): {resp.text[:200]}"
            )

        try:
            body = resp.json()
            # Messages API のレスポンス: content は ContentBlock のリスト([{type:"text", text:"..."}])
            blocks = body.get("content") or []
            text_parts = [b.get("text", "") for b in blocks if b.get("type") == "text"]
            content = "".join(text_parts)
        except Exception as e:  # noqa: BLE001
            raise FatalError(
                f"Anthropic レスポンス解析失敗: {e}", cause=e,
            ) from e

        result = _strip_translation_prefix(content)
        if not result:
            raise SkipError("Claude 翻訳結果が空です")
        return result

    def capabilities(self) -> BackendCapabilities:
        return BackendCapabilities(
            supported_languages=tuple(_SUPPORTED_TARGET_LANGUAGES),
            requires_gpu=False,
            is_cloud=True,
            requires_credentials=True,
            service_name="Anthropic Claude API",
            terms_url="https://www.anthropic.com/legal/aup",
            notes=f"Anthropic {self._model}。LLM 翻訳(temperature=0.2)。",
        )

    @classmethod
    def supported_target_languages(cls) -> list[str]:
        return list(_SUPPORTED_TARGET_LANGUAGES)
