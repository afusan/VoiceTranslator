"""runner_tts の単体テスト。SapiTtsBackend をモック注入。"""

from __future__ import annotations

import wave
from pathlib import Path

import numpy as np
import pytest

from voice_translator.dev import runner_tts
from voice_translator.dev._common import write_json


class FakeTtsBackend:
    instances: list["FakeTtsBackend"] = []

    def __init__(
        self,
        *,
        rate: int = 180,
        voice_lang_hint: str = "ja",
        flush_delay_sec: float = 0.1,
    ) -> None:
        self.init = {
            "rate": rate,
            "voice_lang_hint": voice_lang_hint,
            "flush_delay_sec": flush_delay_sec,
        }
        self.calls: list[tuple[str, str]] = []
        FakeTtsBackend.instances.append(self)

    def synthesize(self, text: str, tgt_lang: str):
        self.calls.append((text, tgt_lang))
        # 0.1 秒分のサイン波を返す
        sr = 22050
        t = np.linspace(0, 0.1, sr // 10, endpoint=False)
        pcm = (0.3 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)
        return pcm, sr


@pytest.fixture(autouse=True)
def _reset_fake() -> None:
    FakeTtsBackend.instances.clear()


@pytest.fixture
def patched_backend(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(runner_tts, "SapiTtsBackend", FakeTtsBackend)


def test_text_arg_synthesizes_wav(patched_backend, tmp_path: Path) -> None:
    out = tmp_path / "out.wav"
    rc = runner_tts.run([
        "--text", "こんにちは",
        "--output", str(out),
        "--tgt-lang", "ja",
        "--rate", "200",
    ])
    assert rc == 0
    assert out.is_file()
    with wave.open(str(out), "rb") as wf:
        assert wf.getnchannels() == 1
        assert wf.getsampwidth() == 2
        assert wf.getframerate() == 22050   # Fake が返した値が透過
        assert wf.getnframes() > 0
    inst = FakeTtsBackend.instances[0]
    assert inst.init == {
        "rate": 200,
        "voice_lang_hint": "ja",
        "flush_delay_sec": 0.1,
    }
    assert inst.calls == [("こんにちは", "ja")]


def test_json_input_inherits_tgt_lang(patched_backend, tmp_path: Path) -> None:
    in_json = tmp_path / "tr.json"
    write_json(in_json, {"tgt_text": "Hi", "tgt_lang": "en"})
    out = tmp_path / "out.wav"
    rc = runner_tts.run([
        "--input", str(in_json),
        "--output", str(out),
        "--tgt-lang", "ja",  # JSON 側の "en" が勝つ
    ])
    assert rc == 0
    inst = FakeTtsBackend.instances[0]
    assert inst.init["voice_lang_hint"] == "en"
    assert inst.calls == [("Hi", "en")]


def test_output_required(patched_backend, capsys: pytest.CaptureFixture) -> None:
    with pytest.raises(SystemExit):
        runner_tts.run(["--text", "x"])
