"""DeepLTranslatorBackend: DeepL API による翻訳(クラウド)。

役割: src_text を tgt_lang のテキストに翻訳する。クラウド + 単一 API key パターン。

設計判断:
- API key の末尾 `:fx` で Free / Pro エンドポイントを自動切替(DeepL の慣行)
- ISO 639-1 → DeepL 言語コード(大文字、一部独自)に backend 内で変換
- HTTP は httpx(extras `translator-deepl` で opt-in)
- 失敗種別:
  - 401/403/456(クォータ) → FatalError
  - 429/5xx → RecoverableError
"""

from __future__ import annotations

from voice_translator.common.errors import (
    FatalError,
    RecoverableError,
    SkipError,
)
from voice_translator.common.languages import iso1_to_iso3, iso3_to_iso1
from voice_translator.common.types import (
    BackendCapabilities,
    CredentialField,
    ModelStatus,
    VerifyResult,
)

from .backend import TranslatorBackend


_FREE_URL: str = "https://api-free.deepl.com/v2/translate"
_PRO_URL: str = "https://api.deepl.com/v2/translate"
_FREE_USAGE_URL: str = "https://api-free.deepl.com/v2/usage"
_PRO_USAGE_URL: str = "https://api.deepl.com/v2/usage"


# DeepL の対応言語(2025-01 時点)。ISO 639-1 → DeepL コード。
# EN-US/EN-GB の区別はせず単一 EN として扱う(ASR 連動の単純化のため)。
_ISO_TO_DEEPL: dict[str, str] = {
    "ar": "AR", "bg": "BG", "cs": "CS", "da": "DA", "de": "DE",
    "el": "EL", "en": "EN", "es": "ES", "et": "ET", "fi": "FI",
    "fr": "FR", "hu": "HU", "id": "ID", "it": "IT", "ja": "JA",
    "ko": "KO", "lt": "LT", "lv": "LV", "nb": "NB", "nl": "NL",
    "pl": "PL", "pt": "PT", "ro": "RO", "ru": "RU", "sk": "SK",
    "sl": "SL", "sv": "SV", "tr": "TR", "uk": "UK", "zh": "ZH",
    "no": "NB",  # Norwegian(エイリアス)
}


def _is_free_key(api_key: str) -> bool:
    return api_key.endswith(":fx")


def _endpoint_pair(api_key: str) -> tuple[str, str]:
    """(translate_url, usage_url) を返す(Free/Pro 自動判定)。"""
    if _is_free_key(api_key):
        return _FREE_URL, _FREE_USAGE_URL
    return _PRO_URL, _PRO_USAGE_URL


def _to_deepl_lang(iso: str, *, fallback: str = "EN") -> str:
    return _ISO_TO_DEEPL.get(iso.lower(), fallback)


class DeepLTranslatorBackend(TranslatorBackend):
    """DeepL API を呼ぶクラウド Translator backend。"""

    _TIMEOUT_SEC: float = 30.0

    def __init__(self, *, api_key: str | None = None) -> None:
        super().__init__()
        self._api_key = (api_key or "").strip()
        if not self._api_key:
            self._set_status(ModelStatus.MISSING_CREDENTIALS)
            self._client = None
            return

        try:
            import httpx  # type: ignore
        except Exception as e:  # noqa: BLE001
            raise FatalError(
                f"httpx のロードに失敗(`uv sync --extra translator-deepl` で追加してください): {e}",
                cause=e,
            ) from e
        self._client = httpx.Client(timeout=self._TIMEOUT_SEC)
        self._translate_url, self._usage_url = _endpoint_pair(self._api_key)
        self._set_status(ModelStatus.LOADED)

    # ============================================================
    @classmethod
    def credential_spec(cls) -> list[CredentialField]:
        return [
            CredentialField(
                key_name="api_key",
                label="DeepL API Key",
                secret=True,
                help_text=(
                    "https://www.deepl.com/account で発行。末尾 `:fx` で Free、無印で Pro。"
                ),
            ),
        ]

    @classmethod
    def verify_credentials(cls, values: dict[str, str]) -> VerifyResult:
        api_key = (values or {}).get("api_key", "").strip()
        if not api_key:
            return VerifyResult(ok=False, message="DeepL API Key が未入力です")
        try:
            import httpx  # type: ignore
        except Exception as e:  # noqa: BLE001
            return VerifyResult(ok=False, message=f"httpx 未インストール: {e}")
        _, usage_url = _endpoint_pair(api_key)
        try:
            resp = httpx.get(
                usage_url,
                headers={"Authorization": f"DeepL-Auth-Key {api_key}"},
                timeout=10.0,
            )
        except Exception as e:  # noqa: BLE001
            return VerifyResult(ok=False, message=f"DeepL API 接続失敗: {e}")
        if resp.status_code == 200:
            kind = "Free" if _is_free_key(api_key) else "Pro"
            return VerifyResult(ok=True, message=f"DeepL ({kind}) 認証 OK")
        if resp.status_code in (401, 403):
            return VerifyResult(ok=False, message="API Key が無効です")
        if resp.status_code == 456:
            return VerifyResult(ok=False, message="クォータ超過")
        return VerifyResult(
            ok=False, message=f"DeepL API 応答異常: HTTP {resp.status_code}"
        )

    # ============================================================
    def translate(self, src_text: str, src_lang: str, tgt_lang: str) -> str:
        if self._client is None:
            raise FatalError("DeepL backend が未初期化(API Key 未設定)")
        text = (src_text or "").strip()
        if not text:
            return ""

        # 受け取りは正準 639-3。639-1 キーのネイティブ変換表を引く直前に落とす。
        tgt_iso1 = iso3_to_iso1(tgt_lang)
        data: dict[str, str] = {
            "text": text,
            "target_lang": _to_deepl_lang(tgt_iso1, fallback="JA"),
        }
        # src_lang="auto" / 未知のときは DeepL に自動検出させる(source_lang 省略)
        if src_lang and src_lang.lower() not in ("auto", ""):
            src_iso1 = iso3_to_iso1(src_lang)
            mapped = _ISO_TO_DEEPL.get(src_iso1.lower())
            if mapped:
                data["source_lang"] = mapped

        try:
            resp = self._client.post(
                self._translate_url,
                headers={"Authorization": f"DeepL-Auth-Key {self._api_key}"},
                data=data,
            )
        except Exception as e:  # noqa: BLE001
            self.record_error(e, context="DeepL request")
            raise RecoverableError(
                f"DeepL リクエスト失敗: {e}", cause=e,
            ) from e

        if resp.status_code in (401, 403):
            raise FatalError(
                f"DeepL 認証エラー (HTTP {resp.status_code})"
            )
        if resp.status_code == 456:
            raise FatalError("DeepL クォータ超過 (HTTP 456)")
        if resp.status_code in (429, 500, 502, 503, 504):
            raise RecoverableError(
                f"DeepL 一時障害 (HTTP {resp.status_code})"
            )
        if resp.status_code != 200:
            raise FatalError(
                f"DeepL 異常応答 (HTTP {resp.status_code}): {resp.text[:200]}"
            )

        try:
            payload = resp.json()
            translations = payload.get("translations") or []
            if not translations:
                raise SkipError("DeepL レスポンスに翻訳が含まれません")
            result = str(translations[0].get("text", "")).strip()
        except SkipError:
            raise
        except Exception as e:  # noqa: BLE001
            raise FatalError(
                f"DeepL レスポンス解析失敗: {e}", cause=e,
            ) from e

        if not result:
            raise SkipError("DeepL 翻訳結果が空です")
        return result

    # ============================================================
    def capabilities(self) -> BackendCapabilities:
        return BackendCapabilities(
            supported_languages=tuple(sorted(iso1_to_iso3(c) for c in _ISO_TO_DEEPL)),
            requires_gpu=False,
            is_cloud=True,
            requires_credentials=True,
            service_name="DeepL API",
            terms_url="https://www.deepl.com/pro-license",
            notes="DeepL API (Free/Pro 自動判定)。日本語品質トップ。",
        )

    # ============================================================
    @classmethod
    def supported_target_languages(cls) -> list[str]:
        # 変換表は 639-1 キーなので正準(639-3)へ持ち上げて申告する。
        return sorted(iso1_to_iso3(c) for c in _ISO_TO_DEEPL)
