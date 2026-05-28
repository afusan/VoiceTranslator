"""Nllb200TranslatorBackend の単体テスト。transformers を完全モック化。

R-2 でプリミティブ I/F に変更: translate(src_text, src_lang, tgt_lang) -> str。
"""

from __future__ import annotations

import sys
from unittest.mock import MagicMock

import pytest

from voice_translator.common.errors import FatalError, SkipError


@pytest.fixture()
def fake_transformers(monkeypatch):
    fake_module = MagicMock()

    fake_tokenizer = MagicMock(name="tokenizer")
    fake_tokenizer.src_lang = ""
    # tokenizer(text, return_tensors="pt") の戻り値は **入力 dict**(input_ids 等)。
    # 各テンソルは `.to(device)` で同じ MagicMock を返すように設定(GPU 移送のシミュレート)。
    fake_input_tensor = MagicMock(name="input_tensor")
    fake_input_tensor.to = MagicMock(return_value=fake_input_tensor)
    fake_tokenizer.return_value = {"input_ids": fake_input_tensor}
    fake_tokenizer.convert_tokens_to_ids = MagicMock(return_value=42)
    fake_tokenizer.batch_decode = MagicMock(return_value=["こんにちは"])

    fake_model = MagicMock(name="model")
    fake_model.generate = MagicMock(return_value=MagicMock())
    # `.to(device)` で同じ model を返す(PyTorch の挙動を簡易シミュレート)。
    # これがないと .to() の戻り値が別 MagicMock になって、テスト側の assert が空振りする。
    fake_model.to = MagicMock(return_value=fake_model)

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
        # 空白のみ入力 → 空文字を返す(SKIP は呼び出し側で判定)
        assert backend.translate("   ", "en", "ja") == ""

    def test_translate_returns_string(self, fake_transformers) -> None:
        from voice_translator.translator.nllb200_backend import (
            Nllb200TranslatorBackend,
        )

        backend = Nllb200TranslatorBackend()
        assert backend.translate("Hello", "en", "ja") == "こんにちは"

    def test_iso_to_nllb_mapping(self, fake_transformers) -> None:
        _, fake_tokenizer, _ = fake_transformers
        from voice_translator.translator.nllb200_backend import (
            Nllb200TranslatorBackend,
        )

        backend = Nllb200TranslatorBackend()
        backend.translate("hi", "en", "ja")
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
        backend.translate("x", "auto", "ja")
        assert fake_tokenizer.src_lang == "eng_Latn"  # fallback

    def test_empty_translation_raises_skip(self, fake_transformers) -> None:
        _, fake_tokenizer, _ = fake_transformers
        fake_tokenizer.batch_decode = MagicMock(return_value=[""])
        from voice_translator.translator.nllb200_backend import (
            Nllb200TranslatorBackend,
        )

        backend = Nllb200TranslatorBackend()
        with pytest.raises(SkipError):
            backend.translate("hi", "en", "ja")

    def test_generate_called_with_repetition_guard_params(
        self, fake_transformers
    ) -> None:
        """退化(degenerate output)抑止のパラメータが generate() に渡されること。

        translations.jsonl L184 で観測された "同じ n-gram を延々と繰り返す" 現象は
        greedy decoding + 抑止なし が原因。コンストラクタ既定値で各種抑止が入る。
        """
        _, _, fake_model = fake_transformers
        from voice_translator.translator.nllb200_backend import (
            Nllb200TranslatorBackend,
        )

        backend = Nllb200TranslatorBackend()
        backend.translate("hello", "en", "ja")

        fake_model.generate.assert_called_once()
        kwargs = fake_model.generate.call_args.kwargs
        # 既定で beam search、n-gram 重複制限、繰り返しペナルティ、early_stopping が入る
        assert kwargs.get("num_beams") == 4
        assert kwargs.get("no_repeat_ngram_size") == 3
        assert kwargs.get("repetition_penalty") == 1.1
        assert kwargs.get("early_stopping") is True

    def test_generate_params_can_be_overridden(self, fake_transformers) -> None:
        """コンストラクタ引数で生成パラメータを上書きできる。"""
        _, _, fake_model = fake_transformers
        from voice_translator.translator.nllb200_backend import (
            Nllb200TranslatorBackend,
        )

        backend = Nllb200TranslatorBackend(
            num_beams=1,
            no_repeat_ngram_size=0,
            repetition_penalty=1.0,
            early_stopping=False,
        )
        backend.translate("hello", "en", "ja")
        kwargs = fake_model.generate.call_args.kwargs
        assert kwargs.get("num_beams") == 1
        assert kwargs.get("no_repeat_ngram_size") == 0
        assert kwargs.get("repetition_penalty") == 1.0
        assert kwargs.get("early_stopping") is False

    def test_inference_exception_wrapped_fatal(self, fake_transformers) -> None:
        _, _, fake_model = fake_transformers
        fake_model.generate = MagicMock(side_effect=RuntimeError("oom"))
        from voice_translator.translator.nllb200_backend import (
            Nllb200TranslatorBackend,
        )

        backend = Nllb200TranslatorBackend()
        with pytest.raises(FatalError, match="翻訳失敗"):
            backend.translate("hi", "en", "ja")


class TestDeviceSelection:
    """device 引数の振る舞い: auto 解決と明示指定。"""

    def test_explicit_cpu_used(self, fake_transformers) -> None:
        _, _, fake_model = fake_transformers
        from voice_translator.translator.nllb200_backend import (
            Nllb200TranslatorBackend,
        )

        backend = Nllb200TranslatorBackend(device="cpu")
        assert backend.device == "cpu"
        # モデルが .to("cpu") されている
        fake_model.to.assert_called_with("cpu")

    def test_auto_resolves_to_cpu_when_no_accelerator(
        self, fake_transformers, monkeypatch
    ) -> None:
        """auto + アクセラレータ無しの環境では cpu に落ちる。"""
        # torch.cuda / mps を「無し」に固定
        from types import SimpleNamespace
        fake_torch = MagicMock(name="torch")
        fake_torch.cuda.is_available = MagicMock(return_value=False)
        fake_torch.backends = SimpleNamespace(mps=MagicMock())
        fake_torch.backends.mps.is_available = MagicMock(return_value=False)
        monkeypatch.setitem(sys.modules, "torch", fake_torch)

        from voice_translator.translator.nllb200_backend import (
            Nllb200TranslatorBackend,
        )
        backend = Nllb200TranslatorBackend(device="auto")
        assert backend.device == "cpu"

    def test_to_failure_falls_back_to_cpu(self, fake_transformers) -> None:
        """`.to(device)` で例外が出ても CPU に落として続行する。"""
        _, _, fake_model = fake_transformers
        # cuda 指定 → .to("cuda") が失敗 → CPU フォールバック
        fake_model.to.side_effect = [RuntimeError("CUDA OOM"), fake_model]
        from voice_translator.translator.nllb200_backend import (
            Nllb200TranslatorBackend,
        )

        backend = Nllb200TranslatorBackend(device="cuda")
        assert backend.device == "cpu"
        # 2 回目の .to が "cpu" で呼ばれる
        fake_model.to.assert_called_with("cpu")

    def test_input_tensors_moved_to_device(self, fake_transformers) -> None:
        """translate() 内で入力テンソルが device へ移送される。"""
        _, fake_tokenizer, _ = fake_transformers
        from voice_translator.translator.nllb200_backend import (
            Nllb200TranslatorBackend,
        )

        backend = Nllb200TranslatorBackend(device="cpu")
        backend.translate("hello", "en", "ja")
        # tokenizer 戻り値の各テンソルが .to("cpu") で移送されている
        input_tensor = fake_tokenizer.return_value["input_ids"]
        input_tensor.to.assert_called_with("cpu")
