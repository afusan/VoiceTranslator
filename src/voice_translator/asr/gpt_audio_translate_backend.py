"""GptAudioTranslateBackend: OpenAI GPT 音声入力モデルによる ASR+翻訳複合(クラウド)。

役割: 発話 PCM を chat/completions の音声入力(`input_audio`)で GPT 音声モデルに渡し、
1 リクエストで「書き起こし + 任意言語への翻訳」を行う複合バックエンド
(ASR + Translator の 2 ロール)。

英語固定の Whisper translate 系と違い:
- **翻訳先を自由に選べる**(LLM 翻訳と同じ言語セット)。
- **源言語テキスト(src_text)も得られる**(モデルに transcript + translation の
  JSON を返させる)ため、UI 履歴に原文も出る。

設計判断:
- HTTP は httpx(OpenAI 系 backend と共通)。モデル既定は音声入力対応の mini 系
  (gpt-4o-mini-audio-preview)— 動作確認用途で従量コストを抑えるため。設定で変更可。
- 出力契約: STRICT JSON `{"src_lang", "src_text", "tgt_text"}` を指示し、解析失敗時は
  本文全体を翻訳テキストとして縮退(発話が無駄にならないことを優先)。
- 失敗種別: 401/403 → FatalError、429/5xx → RecoverableError(OpenAI 系の共通写像)。
"""

from __future__ import annotations

import base64
import json
from typing import Any

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
# LLM 翻訳の対応言語セットと同一(同じモデルファミリのため)
from voice_translator.translator.openai_gpt_backend import _SUPPORTED_TARGET_LANGUAGES

from .backend import AsrTranslatorBackend
from .openai_whisper_api_backend import _pcm_to_wav_bytes


_API_URL: str = "https://api.openai.com/v1/chat/completions"
_MODELS_URL: str = "https://api.openai.com/v1/models"
_DEFAULT_MODEL: str = "gpt-4o-mini-audio-preview"


def _build_system_prompt(src_lang_hint: str, tgt_lang: str) -> str:
    """音声 → transcript + 翻訳 の STRICT JSON を返させる system プロンプト。"""
    tgt_label = language_name(tgt_lang)
    hint = ""
    if src_lang_hint not in ("auto", "", None):
        hint = f" The audio is spoken in {language_name(src_lang_hint)}."
    return (
        "You listen to an audio clip, transcribe it, and translate it."
        + hint
        + " Reply with STRICT JSON only, no code fences, no commentary: "
        '{"src_lang": "<ISO 639-1 code of the spoken language>", '
        '"src_text": "<verbatim transcript>", '
        f'"tgt_text": "<translation into {tgt_label}>"}}'
    )


def _parse_result_json(content: str) -> dict | None:
    """モデル出力から JSON を取り出す。コードフェンス付きにも耐える。失敗は None。"""
    text = (content or "").strip()
    if text.startswith("```"):
        # ```json ... ``` 形式を剥がす
        text = text.strip("`")
        first_newline = text.find("\n")
        if first_newline >= 0 and not text[:first_newline].lstrip().startswith("{"):
            text = text[first_newline + 1 :]
    try:
        parsed = json.loads(text)
    except (ValueError, TypeError):
        return None
    return parsed if isinstance(parsed, dict) else None


class GptAudioTranslateBackend(AsrTranslatorBackend):
    """OpenAI GPT 音声モデルで ASR+翻訳を一括実行する複合バックエンド。"""

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
                f"(`uv sync --extra asr-openai-api` で追加してください): {e}",
                cause=e,
            ) from e
        self._client = httpx.Client(timeout=self._TIMEOUT_SEC)
        self._set_status(ModelStatus.LOADED)

    # ============================================================
    # 認証情報フロー(OpenAI 系共通: 単一 API key)
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

    # ============================================================
    # 複合の契約
    # ============================================================
    def transcribe_translate(
        self, pcm: Any, src_lang_hint: str = "auto", tgt_lang: str = "en"
    ) -> tuple[str, str, str, str]:
        """1 リクエストで書き起こし + 翻訳。(src_text, src_lang, tgt_text, tgt_lang)。"""
        if self._client is None:
            raise FatalError("GPT audio translate backend が未初期化(API Key 未設定)")
        if pcm is None or (hasattr(pcm, "size") and pcm.size == 0):
            raise SkipError("ASR入力PCMが空です")

        wav_b64 = base64.b64encode(_pcm_to_wav_bytes(pcm)).decode("ascii")
        payload = {
            "model": self._model,
            "modalities": ["text"],
            "messages": [
                {
                    "role": "system",
                    "content": _build_system_prompt(src_lang_hint, tgt_lang),
                },
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "input_audio",
                            "input_audio": {"data": wav_b64, "format": "wav"},
                        },
                    ],
                },
            ],
            "temperature": 0.2,  # 翻訳のばらつきを抑える(LLM 翻訳 backend と同値)
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
            self.record_error(e, context="GPT audio translate request")
            raise RecoverableError(
                f"GPT audio リクエスト失敗: {e}", cause=e,
            ) from e

        if resp.status_code in (401, 403):
            raise FatalError(f"OpenAI 認証エラー (HTTP {resp.status_code})")
        if resp.status_code in (429, 500, 502, 503, 504):
            raise RecoverableError(f"OpenAI API 一時障害 (HTTP {resp.status_code})")
        if resp.status_code != 200:
            raise FatalError(
                f"OpenAI API 異常応答 (HTTP {resp.status_code}): {resp.text[:200]}"
            )

        try:
            body = resp.json()
            content = body["choices"][0]["message"]["content"]
        except Exception as e:  # noqa: BLE001
            raise FatalError(
                f"GPT audio レスポンス解析失敗: {e}", cause=e,
            ) from e

        parsed = _parse_result_json(content)
        if parsed is None:
            # JSON 契約が守られなかった場合の縮退: 本文全体を翻訳テキストとして扱う
            # (発話を無駄にしない。src_text は不明のため空)
            tgt_text = str(content or "").strip()
            src_text = ""
            src_lang = src_lang_hint if src_lang_hint not in ("auto", "", None) else "auto"
        else:
            tgt_text = str(parsed.get("tgt_text", "")).strip()
            src_text = str(parsed.get("src_text", "")).strip()
            if src_lang_hint not in ("auto", "", None):
                src_lang = src_lang_hint
            else:
                raw = str(parsed.get("src_lang", "")).strip().lower()
                # ISO 639-1 らしい 2〜3 文字のみ採用(自由記述は auto に縮退)
                src_lang = raw if 2 <= len(raw) <= 3 and raw.isalpha() else "auto"
        return src_text, src_lang, tgt_text, str(tgt_lang)

    # ============================================================
    # 対応言語・編成申告
    # ============================================================
    @classmethod
    def supported_input_languages(cls) -> list[str]:
        return list(_SUPPORTED_TARGET_LANGUAGES)

    @classmethod
    def supported_target_languages(cls) -> list[str]:
        return list(_SUPPORTED_TARGET_LANGUAGES)

    @classmethod
    def supports_auto_detect(cls) -> bool:
        return True

    # covers_roles / consumes_payload / produces_payload は
    # AsrTranslatorBackend の既定((ASR, TRANSLATOR) / RAW / TRANSLATED)をそのまま使う。

    # ---- メタ情報 ----
    def capabilities(self) -> BackendCapabilities:
        return BackendCapabilities(
            supported_languages=tuple(_SUPPORTED_TARGET_LANGUAGES),
            requires_gpu=False,
            is_cloud=True,
            requires_credentials=True,
            service_name="OpenAI GPT Audio (translate)",
            terms_url="https://openai.com/policies/terms-of-use",
            notes=(
                f"OpenAI {self._model}。音声入力 chat/completions で書き起こし+翻訳を"
                "一括実行(任意の翻訳先、原文テキストも取得)。音声トークン従量課金。"
            ),
        )
