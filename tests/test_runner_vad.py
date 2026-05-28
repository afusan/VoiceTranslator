"""runner_vad の単体テスト。SileroVadBackend をモック注入。"""

from __future__ import annotations

import json
from pathlib import Path
from time import monotonic

import numpy as np
import pytest

from voice_translator.dev import runner_vad
from voice_translator.dev._common import write_wav_float32
from voice_translator.vad.backend import VadSegment


class FakeVadBackend:
    """N 番目の process 呼び出しごとに 1 セグメント返す Fake。"""

    instances: list["FakeVadBackend"] = []

    def __init__(
        self,
        *,
        threshold: float = 0.5,
        min_silence_ms: int = 500,
        speech_pad_ms: int = 100,
        max_speech_sec: float = 8.0,
    ) -> None:
        self.init = {
            "threshold": threshold,
            "min_silence_ms": min_silence_ms,
            "speech_pad_ms": speech_pad_ms,
            "max_speech_sec": max_speech_sec,
        }
        self._calls = 0
        self.reset_calls = 0
        FakeVadBackend.instances.append(self)

    def reset(self) -> None:
        self.reset_calls += 1

    def process(self, chunk):
        self._calls += 1
        # 2 回呼び出されるたびに 1 つセグメントを返す
        if self._calls % 2 != 0:
            return []
        return [
            VadSegment(
                pcm=np.asarray(chunk, dtype=np.float32),
                started_at_monotonic=monotonic(),
            )
        ]


@pytest.fixture(autouse=True)
def _reset_fake() -> None:
    FakeVadBackend.instances.clear()


@pytest.fixture
def patched_backend(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(runner_vad, "SileroVadBackend", FakeVadBackend)


@pytest.fixture
def wav_1sec(tmp_path: Path) -> Path:
    # 16kHz mono の 1 秒(VAD への投入回数を確保するため十分な長さ)
    sr = 16000
    pcm = np.zeros(sr, dtype=np.float32)
    path = tmp_path / "in.wav"
    write_wav_float32(path, pcm, sr)
    return path


def test_vad_params_pass_through(patched_backend, wav_1sec: Path, tmp_path: Path) -> None:
    out_dir = tmp_path / "out"
    rc = runner_vad.run([
        "--input", str(wav_1sec),
        "--out-dir", str(out_dir),
        "--threshold", "0.7",
        "--min-silence-ms", "300",
        "--speech-pad-ms", "50",
        "--max-speech-sec", "5.0",
    ])
    assert rc == 0
    assert len(FakeVadBackend.instances) == 1
    inst = FakeVadBackend.instances[0]
    assert inst.init == {
        "threshold": 0.7,
        "min_silence_ms": 300,
        "speech_pad_ms": 50,
        "max_speech_sec": 5.0,
    }
    assert inst.reset_calls == 1


def test_index_json_lists_segments(
    patched_backend, wav_1sec: Path, tmp_path: Path
) -> None:
    out_dir = tmp_path / "out"
    rc = runner_vad.run([
        "--input", str(wav_1sec),
        "--out-dir", str(out_dir),
        "--chunk-samples", "2048",  # 1秒/2048 ≒ 8 回 process → 4 seg 程度
    ])
    assert rc == 0
    index_path = out_dir / "index.json"
    assert index_path.is_file()
    index = json.loads(index_path.read_text(encoding="utf-8"))
    assert index["samplerate"] == 16000
    assert index["segment_count"] == len(index["segments"]) > 0
    # 出力 WAV が segments と 1:1 対応
    for seg in index["segments"]:
        assert (out_dir / seg["file"]).is_file()
        assert seg["file"] == f"seq_{seg['seq_id']:04d}_vad.wav"
        assert seg["samples"] > 0


def test_missing_input_returns_nonzero(patched_backend, tmp_path: Path) -> None:
    rc = runner_vad.run([
        "--input", str(tmp_path / "no.wav"),
        "--out-dir", str(tmp_path / "out"),
    ])
    assert rc == 2
    assert FakeVadBackend.instances == []
