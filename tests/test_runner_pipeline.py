"""runner_pipeline の単体テスト。全バックエンドをモック注入して連結動作を検証する。"""

from __future__ import annotations

import json
from pathlib import Path
from time import monotonic

import numpy as np
import pytest

from voice_translator.dev import runner_pipeline
from voice_translator.dev._common import write_json, write_wav_float32
from voice_translator.vad.backend import VadSegment


# ============================================================
# Fake backends
# ============================================================
class FakeVad:
    def __init__(self, **kw) -> None:
        self.kw = kw
        self._calls = 0

    def reset(self) -> None:
        self._calls = 0

    def process(self, chunk):
        # 偶数回呼ばれたタイミングで 1 セグメント返す(チャンクをそのまま流用)
        self._calls += 1
        if self._calls % 2 != 0:
            return []
        return [VadSegment(pcm=np.asarray(chunk, dtype=np.float32), started_at_monotonic=monotonic())]


class FakeAsr:
    def __init__(self, **kw) -> None:
        self.kw = kw
        self.device = "cpu"
        self.compute_type = "int8"

    def transcribe(self, pcm, src_lang_hint: str = "auto") -> tuple[str, str]:
        return f"text-{int(pcm.size)}", "en"


class FakeTranslator:
    def __init__(self, **kw) -> None:
        self.kw = kw
        self.device = "cpu"

    def translate(self, src_text: str, src_lang: str, tgt_lang: str) -> str:
        return f"[{tgt_lang}]{src_text}"


class FakeTts:
    def __init__(self, **kw) -> None:
        self.kw = kw

    def synthesize(self, text: str, tgt_lang: str):
        # 0.05 秒の無音 22050Hz
        return np.zeros(22050 // 20, dtype=np.float32), 22050


@pytest.fixture
def patched_backends(monkeypatch: pytest.MonkeyPatch):
    # vad は runner_pipeline 内で直接参照
    monkeypatch.setattr(runner_pipeline, "SileroVadBackend", FakeVad)
    # 残りは lazy import なので原モジュールを差し替え
    import voice_translator.asr.faster_whisper_backend as fw_mod
    import voice_translator.translator.nllb200_backend as nllb_mod
    import voice_translator.tts.sapi_backend as sapi_mod
    monkeypatch.setattr(fw_mod, "FasterWhisperAsrBackend", FakeAsr)
    monkeypatch.setattr(nllb_mod, "Nllb200TranslatorBackend", FakeTranslator)
    monkeypatch.setattr(sapi_mod, "SapiTtsBackend", FakeTts)


@pytest.fixture
def wav_short(tmp_path: Path) -> Path:
    pcm = np.zeros(16000, dtype=np.float32)
    path = tmp_path / "in.wav"
    write_wav_float32(path, pcm, 16000)
    return path


# ============================================================
# vad → translate (3 段連結)
# ============================================================
def test_vad_to_translate_writes_per_segment_files(
    patched_backends, wav_short: Path, tmp_path: Path
) -> None:
    out_dir = tmp_path / "out"
    rc = runner_pipeline.run([
        "--from", "vad", "--to", "translate",
        "--input", str(wav_short),
        "--out-dir", str(out_dir),
        "--vad-chunk-samples", "4096",
        "--src-lang", "en", "--tgt-lang", "ja",
    ])
    assert rc == 0
    index = json.loads((out_dir / "index.json").read_text(encoding="utf-8"))
    assert index["from"] == "vad" and index["to"] == "translate"
    assert index["stages"] == ["vad", "asr", "translate"]
    assert len(index["units"]) >= 1
    for unit in index["units"]:
        seq = unit["seq_id"]
        # 各 unit に対し 3 つのファイルが生成されているはず
        # (vad.wav, asr.json, translate.json)
        assert (out_dir / f"seq_{seq:04d}_vad.wav").is_file()
        assert (out_dir / f"seq_{seq:04d}_asr.json").is_file()
        assert (out_dir / f"seq_{seq:04d}_translate.json").is_file()
        # tts は範囲外なので作られない
        assert not (out_dir / f"seq_{seq:04d}_tts.wav").exists()


# ============================================================
# asr → translate (1 段スキップ + 1 段)
# ============================================================
def test_asr_to_translate_skips_vad(
    patched_backends, wav_short: Path, tmp_path: Path
) -> None:
    out_dir = tmp_path / "out"
    rc = runner_pipeline.run([
        "--from", "asr", "--to", "translate",
        "--input", str(wav_short),
        "--out-dir", str(out_dir),
    ])
    assert rc == 0
    assert (out_dir / "seq_0001_asr.json").is_file()
    assert (out_dir / "seq_0001_translate.json").is_file()
    # vad はスキップ
    assert not (out_dir / "seq_0001_vad.wav").exists()
    # 中身チェック
    asr = json.loads((out_dir / "seq_0001_asr.json").read_text(encoding="utf-8"))
    tr = json.loads((out_dir / "seq_0001_translate.json").read_text(encoding="utf-8"))
    assert asr["stage"] == "asr"
    assert tr["src_text"] == asr["text"]
    assert tr["tgt_text"] == f"[ja]{asr['text']}"


# ============================================================
# translate → tts (テキスト入力)
# ============================================================
def test_translate_to_tts_from_json_input(
    patched_backends, tmp_path: Path
) -> None:
    asr_json = tmp_path / "asr_in.json"
    write_json(asr_json, {"text": "hello", "src_lang": "en"})
    out_dir = tmp_path / "out"
    rc = runner_pipeline.run([
        "--from", "translate", "--to", "tts",
        "--input", str(asr_json),
        "--out-dir", str(out_dir),
        "--tgt-lang", "ja",
    ])
    assert rc == 0
    assert (out_dir / "seq_0001_translate.json").is_file()
    assert (out_dir / "seq_0001_tts.wav").is_file()
    tr = json.loads((out_dir / "seq_0001_translate.json").read_text(encoding="utf-8"))
    assert tr["src_text"] == "hello"
    assert tr["tgt_text"] == "[ja]hello"


# ============================================================
# 不正な順序(--from > --to)
# ============================================================
def test_reverse_range_rejected(patched_backends, wav_short: Path, tmp_path: Path) -> None:
    rc = runner_pipeline.run([
        "--from", "tts", "--to", "vad",
        "--input", str(wav_short),
        "--out-dir", str(tmp_path / "out"),
    ])
    assert rc == 2
