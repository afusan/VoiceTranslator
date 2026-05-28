"""voice_translator.dev._common の単体テスト。

WAV/JSON IO・テキスト入力の解決(text / .json / .txt / stdin)を検証。
"""

from __future__ import annotations

import io
import json
import sys
import wave
from pathlib import Path

import numpy as np
import pytest

from voice_translator.dev._common import (
    read_json,
    read_wav_as_float32_mono,
    resolve_text_input,
    write_json,
    write_wav_float32,
)


# ============================================================
# WAV IO 往復
# ============================================================
class TestWavRoundtrip:
    def test_write_then_read_int16_roundtrip(self, tmp_path: Path) -> None:
        sr = 16000
        t = np.linspace(0, 0.05, sr // 20, endpoint=False)
        pcm = (0.4 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)
        path = tmp_path / "sine.wav"
        write_wav_float32(path, pcm, sr)
        out_pcm, out_sr = read_wav_as_float32_mono(path)
        assert out_sr == 16000
        assert out_pcm.size == pcm.size
        # int16 量子化誤差を許容して概形が一致
        np.testing.assert_allclose(out_pcm, pcm, atol=1e-3)

    def test_read_stereo_int16_wav_averages_to_mono(self, tmp_path: Path) -> None:
        sr = 16000
        # ステレオ int16 を直接書く(L=+0.3, R=-0.1 → 平均 +0.1)
        n = 100
        left = (np.full(n, 0.3) * 32767).astype(np.int16)
        right = (np.full(n, -0.1) * 32767).astype(np.int16)
        interleaved = np.empty(2 * n, dtype=np.int16)
        interleaved[0::2] = left
        interleaved[1::2] = right
        path = tmp_path / "stereo.wav"
        with wave.open(str(path), "wb") as wf:
            wf.setnchannels(2)
            wf.setsampwidth(2)
            wf.setframerate(sr)
            wf.writeframes(interleaved.tobytes())
        out_pcm, _ = read_wav_as_float32_mono(path)
        assert out_pcm.size == n
        assert abs(out_pcm.mean() - 0.1) < 0.01


# ============================================================
# JSON IO
# ============================================================
class TestJsonRoundtrip:
    def test_write_then_read(self, tmp_path: Path) -> None:
        path = tmp_path / "x.json"
        payload = {"seq_id": 42, "text": "こんにちは", "src_lang": "en"}
        write_json(path, payload)
        loaded = read_json(path)
        assert loaded == payload
        # 日本語は \uXXXX エスケープせずに保存される(ensure_ascii=False)
        raw = path.read_text(encoding="utf-8")
        assert "こんにちは" in raw


# ============================================================
# resolve_text_input
# ============================================================
class TestResolveTextInput:
    def test_explicit_text_arg_wins(self) -> None:
        text, meta = resolve_text_input(text="hi", input_path=None)
        assert text == "hi"
        assert meta is None

    def test_json_input_picks_text_field(self, tmp_path: Path) -> None:
        path = tmp_path / "asr.json"
        write_json(path, {"text": "hello world", "src_lang": "en", "seq_id": 1})
        text, meta = resolve_text_input(text=None, input_path=path)
        assert text == "hello world"
        assert meta and meta["src_lang"] == "en"

    def test_json_input_falls_back_to_tgt_text(self, tmp_path: Path) -> None:
        path = tmp_path / "tr.json"
        write_json(path, {"tgt_text": "やあ", "tgt_lang": "ja"})
        text, _ = resolve_text_input(text=None, input_path=path)
        assert text == "やあ"

    def test_json_input_without_known_field_raises(self, tmp_path: Path) -> None:
        path = tmp_path / "bad.json"
        write_json(path, {"foo": "bar"})
        with pytest.raises(ValueError):
            resolve_text_input(text=None, input_path=path)

    def test_plain_text_input(self, tmp_path: Path) -> None:
        path = tmp_path / "raw.txt"
        path.write_text("plain content\n", encoding="utf-8")
        text, meta = resolve_text_input(text=None, input_path=path)
        assert text == "plain content"
        assert meta is None

    def test_stdin_fallback(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(sys, "stdin", io.StringIO("from stdin\n"))
        text, meta = resolve_text_input(text=None, input_path=None)
        assert text == "from stdin"
        assert meta is None

    def test_empty_stdin_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(sys, "stdin", io.StringIO(""))
        with pytest.raises(ValueError):
            resolve_text_input(text=None, input_path=None)
