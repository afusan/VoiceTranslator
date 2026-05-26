"""Nllb200TranslatorBackend の単体テスト。transformers を完全モック化。"""

from __future__ import annotations

import sys
from unittest.mock import MagicMock

import pytest

from voice_translator.common.errors import FatalError, SkipError
from voice_translator.common.utterance import Utterance


@pytest.fixture()
def fake_transformers(monkeypatch):
    fake_module = MagicMock()

    fake_tokenizer = MagicMock(name="tokenizer")
    fake_tokenizer.src_lang = ""
    fake_tokenizer.return_value = {"input_ids": MagicMock()}
    fake_tokenizer.convert_tokens_to_ids = MagicMock(return_value=42)
    fake_tokenizer.batch_decode = MagicMock(return_value=["こんにちは"])

    fake_model = MagicMock(name="model")
    fake_model.generate = MagicMock(return_value=MagicMock())

    fake_module.AutoTokenizer = MagicMock()
    fake_module.AutoTokenizer.from_pretrained = MagicMock(return_value=fake_tokenizer)
    fake_module.AutoModelForSeq2SeqLM = MagicMock()
    fake_module.AutoModelForSeq2SeqLM.from_pretrained = MagicMock(return_value=fake_model)

    monkeypatch.setitem(sys.modules, "transformers", fake_module)
    return fake_module, fake_tokenizer, fake_model


class TestInitialization:
    def test_loads_tokenizer_and_model(self, fake_transformers) -> None:
        fake_module, _, _ = fake_transformers
        from voice_translator.translator.nllb200_backend import (
            Nllb200TranslatorBackend,
        )

        Nllb200TranslatorBackend()

        fake_module.AutoTokenizer.from_pretrained.assert_called_once()
        fake_module.AutoModelForSeq2SeqLM.from_pretrained.assert_called_once()

    def test_load_failure_raises_fatal(self, monkeypatch) -> None:
        fake_module = MagicMock()
        fake_module.AutoTokenizer = MagicMock()
        fake_module.AutoTokenizer.from_pretrained = MagicMock(side_effect=OSError("net"))
        fake_module.AutoModelForSeq2SeqLM = MagicMock()
        monkeypatch.setitem(sys.modules, "transformers", fake_module)
        from voice_translator.translator.nllb200_backend import (
            Nllb200TranslatorBackend,
        )

        with pytest.raises(FatalError, match="ロードに失敗"):
            Nllb200TranslatorBackend()


class TestTranslate:
    def test_empty_text_passthrough(self, fake_transformers) -> None:
        from voice_translator.translator.nllb200_backend import (
            Nllb200TranslatorBackend,
        )

        backend = Nllb200TranslatorBackend()
        utt = Utterance(src_text="   ", src_lang="en")
        result = backend.translate(utt, "ja")
        assert result.tgt_text == ""
        assert result.tgt_lang == "ja"

    def test_translate_sets_tgt_fields(self, fake_transformers) -> None:
        from voice_translator.translator.nllb200_backend import (
            Nllb200TranslatorBackend,
        )

        backend = Nllb200TranslatorBackend()
        utt = Utterance(src_text="Hello", src_lang="en")
        result = backend.translate(utt, "ja")
        assert result.tgt_text == "こんにちは"
        assert result.tgt_lang == "ja"

    def test_iso_to_nllb_mapping(self, fake_transformers) -> None:
        _, fake_tokenizer, _ = fake_transformers
        from voice_translator.translator.nllb200_backend import (
            Nllb200TranslatorBackend,
        )

        backend = Nllb200TranslatorBackend()
        backend.translate(Utterance(src_text="hi", src_lang="en"), "ja")
        # src_lang として eng_Latn が設定されているはず
        assert fake_tokenizer.src_lang == "eng_Latn"
        # tgt の forced_bos_token_id 取得時に jpn_Jpan が渡される
        fake_tokenizer.convert_tokens_to_ids.assert_called_with("jpn_Jpan")

    def test_unknown_src_lang_falls_back_to_english(self, fake_transformers) -> None:
        _, fake_tokenizer, _ = fake_transformers
        from voice_translator.translator.nllb200_backend import (
            Nllb200TranslatorBackend,
        )

        backend = Nllb200TranslatorBackend()
        backend.translate(Utterance(src_text="x", src_lang="auto"), "ja")
        assert fake_tokenizer.src_lang == "eng_Latn"  # fallback

    def test_empty_translation_raises_skip(self, fake_transformers) -> None:
        _, fake_tokenizer, _ = fake_transformers
        fake_tokenizer.batch_decode = MagicMock(return_value=[""])
        from voice_translator.translator.nllb200_backend import (
            Nllb200TranslatorBackend,
        )

        backend = Nllb200TranslatorBackend()
        with pytest.raises(SkipError):
            backend.translate(Utterance(src_text="hi", src_lang="en"), "ja")

    def test_inference_exception_wrapped_fatal(self, fake_transformers) -> None:
        _, _, fake_model = fake_transformers
        fake_model.generate = MagicMock(side_effect=RuntimeError("oom"))
        from voice_translator.translator.nllb200_backend import (
            Nllb200TranslatorBackend,
        )

        backend = Nllb200TranslatorBackend()
        with pytest.raises(FatalError, match="翻訳失敗"):
            backend.translate(Utterance(src_text="hi", src_lang="en"), "ja")
