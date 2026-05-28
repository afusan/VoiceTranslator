"""runner_translator の単体テスト。

Nllb200TranslatorBackend をモックして CLI 引数の流れと出力 JSON 形式を検証する。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from voice_translator.dev import runner_translator
from voice_translator.dev._common import write_json


class FakeTranslatorBackend:
    """Nllb200TranslatorBackend の代わりに注入する検証用 fake。"""

    calls: list[dict] = []

    def __init__(
        self,
        *,
        model_name: str = "facebook/nllb-200-distilled-600M",
        max_length: int = 512,
        num_beams: int = 4,
        no_repeat_ngram_size: int = 3,
        repetition_penalty: float = 1.1,
        early_stopping: bool = True,
        device: str = "auto",
    ) -> None:
        self._init = {
            "model_name": model_name,
            "max_length": max_length,
            "num_beams": num_beams,
            "no_repeat_ngram_size": no_repeat_ngram_size,
            "repetition_penalty": repetition_penalty,
            "early_stopping": early_stopping,
            "device": device,
        }
        FakeTranslatorBackend.calls.append({"init": dict(self._init)})

    @property
    def device(self) -> str:
        return "cpu" if self._init["device"] == "auto" else self._init["device"]

    def translate(self, src_text: str, src_lang: str, tgt_lang: str) -> str:
        FakeTranslatorBackend.calls.append({
            "translate": {"src_text": src_text, "src_lang": src_lang, "tgt_lang": tgt_lang}
        })
        return f"[{tgt_lang}]{src_text}"


@pytest.fixture(autouse=True)
def _reset_fake() -> None:
    FakeTranslatorBackend.calls.clear()


@pytest.fixture
def patched_backend(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        runner_translator, "Nllb200TranslatorBackend", FakeTranslatorBackend
    )
    return FakeTranslatorBackend


# ============================================================
# --text 直接指定
# ============================================================
def test_text_arg_passes_through(
    patched_backend, capsys: pytest.CaptureFixture
) -> None:
    rc = runner_translator.run(["--text", "hello", "--src-lang", "en", "--tgt-lang", "ja"])
    assert rc == 0
    data = json.loads(capsys.readouterr().out)
    assert data["stage"] == "translate"
    assert data["src_text"] == "hello"
    assert data["tgt_text"] == "[ja]hello"
    assert data["src_lang"] == "en"
    assert data["tgt_lang"] == "ja"


# ============================================================
# 生成パラメータが backend に届く
# ============================================================
def test_generation_params_reach_backend(patched_backend, tmp_path: Path) -> None:
    out = tmp_path / "out.json"
    rc = runner_translator.run([
        "--text", "anything",
        "--output", str(out),
        "--num-beams", "8",
        "--no-repeat-ngram-size", "4",
        "--repetition-penalty", "1.3",
        "--max-length", "256",
        "--no-early-stopping",
    ])
    assert rc == 0
    init = FakeTranslatorBackend.calls[0]["init"]
    assert init["num_beams"] == 8
    assert init["no_repeat_ngram_size"] == 4
    assert init["repetition_penalty"] == pytest.approx(1.3)
    assert init["max_length"] == 256
    assert init["early_stopping"] is False
    # 出力 JSON 側の runner メタにも残る
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["runner"]["num_beams"] == 8
    assert data["runner"]["early_stopping"] is False


# ============================================================
# .json 入力 → src_lang を上書き継承
# ============================================================
def test_json_input_inherits_src_lang(
    patched_backend, tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    in_json = tmp_path / "asr.json"
    write_json(in_json, {"text": "good morning", "src_lang": "en"})
    rc = runner_translator.run([
        "--input", str(in_json), "--tgt-lang", "ja",
        # --src-lang は意図的に違う値を渡しても JSON の方が優先される
        "--src-lang", "fr",
    ])
    assert rc == 0
    data = json.loads(capsys.readouterr().out)
    assert data["src_lang"] == "en"   # JSON 由来
    assert data["tgt_lang"] == "ja"
    assert data["src_text"] == "good morning"
    # backend にも en で届いたこと
    tr_call = FakeTranslatorBackend.calls[1]["translate"]
    assert tr_call["src_lang"] == "en"


# ============================================================
# --text と --input は排他
# ============================================================
def test_text_and_input_are_mutually_exclusive(
    patched_backend, tmp_path: Path
) -> None:
    in_json = tmp_path / "x.json"
    write_json(in_json, {"text": "x"})
    with pytest.raises(SystemExit):
        runner_translator.run(["--text", "y", "--input", str(in_json)])


# ============================================================
# .txt 入力(素テキスト)
# ============================================================
def test_plain_text_input(
    patched_backend, tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    txt = tmp_path / "in.txt"
    txt.write_text("Lorem ipsum", encoding="utf-8")
    rc = runner_translator.run([
        "--input", str(txt), "--src-lang", "en", "--tgt-lang", "ja",
    ])
    assert rc == 0
    data = json.loads(capsys.readouterr().out)
    assert data["src_text"] == "Lorem ipsum"
    assert data["src_lang"] == "en"  # JSON ではないので CLI 値が使われる
