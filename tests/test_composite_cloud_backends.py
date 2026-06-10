"""クラウド系 ASR+翻訳複合 backend の単体テスト(httpx 完全モック)。

- OpenAiWhisperApiTranslateBackend: translations エンドポイント(英語固定)
- GptAudioTranslateBackend: GPT 音声入力(任意言語 + 原文取得、JSON 契約と縮退)
"""

from __future__ import annotations

import sys
from unittest.mock import MagicMock

import numpy as np
import pytest

from voice_translator.common.errors import FatalError, RecoverableError, SkipError
from voice_translator.common.messages import PayloadKind
from voice_translator.common.types import LayerKind, ModelStatus


@pytest.fixture()
def fake_httpx(monkeypatch):
    """httpx をモックに差し替える(post の戻りは各テストで設定)。"""
    fake_module = MagicMock(name="httpx_module")
    fake_client = MagicMock(name="httpx_client")
    fake_module.Client = MagicMock(return_value=fake_client)
    monkeypatch.setitem(sys.modules, "httpx", fake_module)
    return fake_module, fake_client


def _make_response(status_code: int, json_payload: dict | None = None, text: str = "") -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    resp.json = MagicMock(return_value=json_payload or {})
    resp.text = text
    return resp


def _pcm(sec: float = 0.5) -> np.ndarray:
    return np.zeros(int(16000 * sec), dtype=np.float32)


# ============================================================
# OpenAI Whisper API translations(英語固定)
# ============================================================
class TestWhisperApiTranslateDeclarations:
    def test_covers_and_payloads(self) -> None:
        from voice_translator.asr.openai_whisper_api_translate_backend import (
            OpenAiWhisperApiTranslateBackend as B,
        )
        assert B.covers_roles() == (LayerKind.ASR, LayerKind.TRANSLATOR)
        assert B.consumes_payload() == PayloadKind.RAW
        assert B.produces_payload() == PayloadKind.TRANSLATED
        assert B.supported_target_languages() == ["en"]
        assert B.supports_auto_detect() is True  # Whisper 系を継承


class TestWhisperApiTranslateCall:
    def test_missing_key_sets_missing_credentials(self) -> None:
        from voice_translator.asr.openai_whisper_api_translate_backend import (
            OpenAiWhisperApiTranslateBackend,
        )
        b = OpenAiWhisperApiTranslateBackend(api_key="")
        assert b.get_status() == ModelStatus.MISSING_CREDENTIALS
        with pytest.raises(FatalError, match="未初期化"):
            b.transcribe_translate(_pcm())

    def test_success_returns_contract(self, fake_httpx) -> None:
        from voice_translator.asr.openai_whisper_api_translate_backend import (
            OpenAiWhisperApiTranslateBackend,
        )
        _, client = fake_httpx
        client.post.return_value = _make_response(
            200, {"text": " Hello world. ", "language": "japanese"}
        )
        b = OpenAiWhisperApiTranslateBackend(api_key="sk-test")

        src_text, src_lang, tgt_text, tgt_lang = b.transcribe_translate(
            _pcm(), src_lang_hint="auto", tgt_lang="en"
        )
        assert src_text == ""           # translations は源文を返さない
        assert src_lang == "ja"          # 検出言語(英語名)を ISO に正規化
        assert tgt_text == "Hello world."
        assert tgt_lang == "en"

        # translations エンドポイントに language パラメータを送らない
        kwargs = client.post.call_args.kwargs
        assert "translations" in client.post.call_args.args[0]
        assert "language" not in kwargs["data"]

    def test_hint_overrides_detected_language(self, fake_httpx) -> None:
        from voice_translator.asr.openai_whisper_api_translate_backend import (
            OpenAiWhisperApiTranslateBackend,
        )
        _, client = fake_httpx
        client.post.return_value = _make_response(200, {"text": "hi", "language": "japanese"})
        b = OpenAiWhisperApiTranslateBackend(api_key="sk-test")
        _, src_lang, _, _ = b.transcribe_translate(_pcm(), src_lang_hint="ko")
        assert src_lang == "ko"

    def test_empty_pcm_raises_skip(self, fake_httpx) -> None:
        from voice_translator.asr.openai_whisper_api_translate_backend import (
            OpenAiWhisperApiTranslateBackend,
        )
        b = OpenAiWhisperApiTranslateBackend(api_key="sk-test")
        with pytest.raises(SkipError):
            b.transcribe_translate(np.zeros(0, dtype=np.float32))

    @pytest.mark.parametrize(
        ("status", "exc"),
        [(401, FatalError), (429, RecoverableError), (503, RecoverableError), (400, FatalError)],
    )
    def test_http_errors_mapped_to_severity(self, fake_httpx, status, exc) -> None:
        from voice_translator.asr.openai_whisper_api_translate_backend import (
            OpenAiWhisperApiTranslateBackend,
        )
        _, client = fake_httpx
        client.post.return_value = _make_response(status, text="boom")
        b = OpenAiWhisperApiTranslateBackend(api_key="sk-test")
        with pytest.raises(exc):
            b.transcribe_translate(_pcm())

    def test_network_error_is_recoverable(self, fake_httpx) -> None:
        from voice_translator.asr.openai_whisper_api_translate_backend import (
            OpenAiWhisperApiTranslateBackend,
        )
        _, client = fake_httpx
        client.post.side_effect = RuntimeError("conn reset")
        b = OpenAiWhisperApiTranslateBackend(api_key="sk-test")
        with pytest.raises(RecoverableError):
            b.transcribe_translate(_pcm())


# ============================================================
# GPT 音声入力(任意言語 + 原文取得)
# ============================================================
class TestGptAudioTranslateDeclarations:
    def test_covers_and_payloads(self) -> None:
        from voice_translator.asr.gpt_audio_translate_backend import (
            GptAudioTranslateBackend as B,
        )
        assert B.covers_roles() == (LayerKind.ASR, LayerKind.TRANSLATOR)
        assert B.consumes_payload() == PayloadKind.RAW
        assert B.produces_payload() == PayloadKind.TRANSLATED
        assert "ja" in B.supported_target_languages()  # 任意言語(英語固定ではない)
        assert B.supports_auto_detect() is True


def _gpt_response(content: str) -> dict:
    return {"choices": [{"message": {"content": content}}]}


class TestGptAudioTranslateCall:
    def test_missing_key_sets_missing_credentials(self) -> None:
        from voice_translator.asr.gpt_audio_translate_backend import (
            GptAudioTranslateBackend,
        )
        b = GptAudioTranslateBackend(api_key="")
        assert b.get_status() == ModelStatus.MISSING_CREDENTIALS

    def test_success_parses_json_contract(self, fake_httpx) -> None:
        from voice_translator.asr.gpt_audio_translate_backend import (
            GptAudioTranslateBackend,
        )
        _, client = fake_httpx
        client.post.return_value = _make_response(
            200,
            _gpt_response(
                '{"src_lang": "en", "src_text": "Hello.", "tgt_text": "こんにちは。"}'
            ),
        )
        b = GptAudioTranslateBackend(api_key="sk-test")

        src_text, src_lang, tgt_text, tgt_lang = b.transcribe_translate(
            _pcm(), src_lang_hint="auto", tgt_lang="ja"
        )
        assert src_text == "Hello."     # 複合でも原文が取れるパターン
        assert src_lang == "en"
        assert tgt_text == "こんにちは。"
        assert tgt_lang == "ja"

        # 音声は input_audio(wav/base64)として送られる
        payload = client.post.call_args.kwargs["json"]
        user_content = payload["messages"][1]["content"]
        assert user_content[0]["type"] == "input_audio"
        assert user_content[0]["input_audio"]["format"] == "wav"

    def test_code_fenced_json_is_parsed(self, fake_httpx) -> None:
        from voice_translator.asr.gpt_audio_translate_backend import (
            GptAudioTranslateBackend,
        )
        _, client = fake_httpx
        client.post.return_value = _make_response(
            200,
            _gpt_response(
                '```json\n{"src_lang": "ja", "src_text": "やあ", "tgt_text": "Hi"}\n```'
            ),
        )
        b = GptAudioTranslateBackend(api_key="sk-test")
        src_text, src_lang, tgt_text, _ = b.transcribe_translate(_pcm(), tgt_lang="en")
        assert (src_text, src_lang, tgt_text) == ("やあ", "ja", "Hi")

    def test_non_json_degrades_to_whole_text(self, fake_httpx) -> None:
        """JSON 契約が守られなくても発話を無駄にしない(本文全体 = 翻訳)。"""
        from voice_translator.asr.gpt_audio_translate_backend import (
            GptAudioTranslateBackend,
        )
        _, client = fake_httpx
        client.post.return_value = _make_response(200, _gpt_response("こんにちは。"))
        b = GptAudioTranslateBackend(api_key="sk-test")
        src_text, src_lang, tgt_text, _ = b.transcribe_translate(_pcm(), tgt_lang="ja")
        assert tgt_text == "こんにちは。"
        assert src_text == ""
        assert src_lang == "auto"

    def test_freeform_src_lang_degrades_to_auto(self, fake_httpx) -> None:
        from voice_translator.asr.gpt_audio_translate_backend import (
            GptAudioTranslateBackend,
        )
        _, client = fake_httpx
        client.post.return_value = _make_response(
            200,
            _gpt_response('{"src_lang": "Japanese language", "src_text": "x", "tgt_text": "y"}'),
        )
        b = GptAudioTranslateBackend(api_key="sk-test")
        _, src_lang, _, _ = b.transcribe_translate(_pcm(), tgt_lang="en")
        assert src_lang == "auto"

    @pytest.mark.parametrize(
        ("status", "exc"),
        [(403, FatalError), (429, RecoverableError), (500, RecoverableError)],
    )
    def test_http_errors_mapped_to_severity(self, fake_httpx, status, exc) -> None:
        from voice_translator.asr.gpt_audio_translate_backend import (
            GptAudioTranslateBackend,
        )
        _, client = fake_httpx
        client.post.return_value = _make_response(status)
        b = GptAudioTranslateBackend(api_key="sk-test")
        with pytest.raises(exc):
            b.transcribe_translate(_pcm())

    def test_empty_pcm_raises_skip(self, fake_httpx) -> None:
        from voice_translator.asr.gpt_audio_translate_backend import (
            GptAudioTranslateBackend,
        )
        b = GptAudioTranslateBackend(api_key="sk-test")
        with pytest.raises(SkipError):
            b.transcribe_translate(np.zeros(0, dtype=np.float32))
