"""runner_asr の単体テスト。

FasterWhisperAsrBackend をモックして CLI 引数の流れと出力 JSON 形式を検証する。
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from voice_translator.dev import runner_asr
from voice_translator.dev._common import write_wav_float32


class FakeAsrBackend:
    """FasterWhisperAsrBackend の代わりに注入する検証用 fake。"""

    calls: list[dict] = []

    def __init__(
        self,
        *,
        model_size: str = "small",
        device: str = "auto",
        compute_type: str = "auto",
        beam_size: int = 1,
    ) -> None:
        # __init__ 引数を観測できるよう保持
        self._init = {
            "model_size": model_size,
            "device": device,
            "compute_type": compute_type,
            "beam_size": beam_size,
        }
        FakeAsrBackend.calls.append({"init": dict(self._init)})

    @property
    def device(self) -> str:
        # "auto" は cpu に解決されたことにする(テスト想定環境)
        d = self._init["device"]
        return "cpu" if d == "auto" else d

    @property
    def compute_type(self) -> str:
        c = self._init["compute_type"]
        return "int8" if c == "auto" else c

    def transcribe(self, pcm, src_lang_hint: str = "auto") -> tuple[str, str]:
        FakeAsrBackend.calls.append({
            "transcribe": {
                "pcm_size": int(getattr(pcm, "size", 0)),
                "hint": src_lang_hint,
            }
        })
        return "hello world", "en" if src_lang_hint in ("auto", "") else src_lang_hint


@pytest.fixture(autouse=True)
def _reset_fake() -> None:
    FakeAsrBackend.calls.clear()


@pytest.fixture
def patched_backend(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(runner_asr, "FasterWhisperAsrBackend", FakeAsrBackend)
    return FakeAsrBackend


@pytest.fixture
def sine_wav(tmp_path: Path) -> Path:
    sr = 16000
    t = np.linspace(0, 0.2, sr // 5, endpoint=False)
    pcm = (0.3 * np.sin(2 * np.pi * 220 * t)).astype(np.float32)
    path = tmp_path / "in.wav"
    write_wav_float32(path, pcm, sr)
    return path


# ============================================================
# CLI -> backend 引数の伝播
# ============================================================
def test_cli_args_pass_through_to_backend(
    patched_backend, sine_wav: Path, tmp_path: Path
) -> None:
    out = tmp_path / "out.json"
    rc = runner_asr.run([
        "--input", str(sine_wav),
        "--output", str(out),
        "--model", "medium",
        "--device", "cuda",
        "--compute-type", "int8_float16",
        "--beam-size", "5",
        "--lang-hint", "en",
        "--seq-id", "42",
    ])
    assert rc == 0
    init_call = FakeAsrBackend.calls[0]["init"]
    assert init_call == {
        "model_size": "medium",
        "device": "cuda",
        "compute_type": "int8_float16",
        "beam_size": 5,
    }
    transcribe_call = FakeAsrBackend.calls[1]["transcribe"]
    assert transcribe_call["hint"] == "en"
    assert transcribe_call["pcm_size"] > 0


# ============================================================
# 出力 JSON 形式
# ============================================================
def test_output_json_matches_dump_schema(
    patched_backend, sine_wav: Path, tmp_path: Path
) -> None:
    out = tmp_path / "out.json"
    rc = runner_asr.run([
        "--input", str(sine_wav),
        "--output", str(out),
        "--seq-id", "7",
    ])
    assert rc == 0
    data = json.loads(out.read_text(encoding="utf-8"))
    # StageDumpWriter の seq_NNNN_asr.json と同じスキーマ
    assert data["seq_id"] == 7
    assert data["stage"] == "asr"
    assert data["text"] == "hello world"
    assert data["src_lang"] == "en"  # FakeAsrBackend が auto→en に解決
    # ランナー固有メタ
    assert data["runner"]["name"] == "runner_asr"
    assert data["runner"]["device_resolved"] == "cpu"
    assert data["runner"]["compute_type_resolved"] == "int8"
    assert "elapsed_ms" in data["runner"]


def test_default_seq_id_is_one(patched_backend, sine_wav: Path, tmp_path: Path) -> None:
    out = tmp_path / "out.json"
    runner_asr.run(["--input", str(sine_wav), "--output", str(out)])
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["seq_id"] == 1


# ============================================================
# 入力ファイル不在 → エラー終了
# ============================================================
def test_missing_input_returns_nonzero(
    patched_backend, tmp_path: Path
) -> None:
    rc = runner_asr.run(["--input", str(tmp_path / "no.wav")])
    assert rc == 2
    # backend は構築されないはず
    assert FakeAsrBackend.calls == []


# ============================================================
# stdout 出力(--output 省略)
# ============================================================
def test_stdout_when_no_output(
    patched_backend, sine_wav: Path, capsys: pytest.CaptureFixture
) -> None:
    rc = runner_asr.run(["--input", str(sine_wav)])
    assert rc == 0
    captured = capsys.readouterr()
    data = json.loads(captured.out)
    assert data["stage"] == "asr"
    assert data["text"] == "hello world"
