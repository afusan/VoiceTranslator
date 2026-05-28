"""StageDumpWriter / NullStageDumpWriter の単体テスト。

WAV/JSON 書き出し・run_id ディレクトリ作成・古い run の自動削除・
ワーカスレッドのライフサイクルを検証する。実モデル/実デバイスは使わない(small)。
"""

from __future__ import annotations

import json
import wave
from pathlib import Path

import numpy as np
import pytest

from voice_translator.common.stage_dump import (
    NullStageDumpWriter,
    StageDumpWriter,
)


# ============================================================
# NullStageDumpWriter — 全メソッドが no-op で例外を出さないこと
# ============================================================
class TestNullStageDumpWriter:
    def test_all_methods_are_noop(self) -> None:
        w = NullStageDumpWriter()
        # 順序や引数の整合性に関係なく、何度呼んでも例外を出さない
        w.start_run({"any": "meta"})
        w.on_vad(1, np.zeros(160, dtype=np.float32), 16000)
        w.on_asr(1, "hello", "en")
        w.on_translate(1, "hello", "en", "こんにちは", "ja")
        w.on_tts(1, np.zeros(160, dtype=np.float32), 16000)
        w.stop_run()
        # start を呼んでなくても stop OK
        w2 = NullStageDumpWriter()
        w2.stop_run()


# ============================================================
# StageDumpWriter — ライフサイクル
# ============================================================
class TestStageDumpLifecycle:
    def test_start_run_creates_run_dir_and_run_json(self, tmp_path: Path) -> None:
        w = StageDumpWriter(
            dump_dir=tmp_path, run_id_factory=lambda: "20260528-160000-0001"
        )
        w.start_run({"backends": {"asr": "faster_whisper"}})
        try:
            run_dir = tmp_path / "20260528-160000-0001"
            assert run_dir.is_dir()
            run_meta = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
            assert run_meta["run_id"] == "20260528-160000-0001"
            assert run_meta["backends"] == {"asr": "faster_whisper"}
            assert "started_at" in run_meta
            assert w.run_dir == run_dir
        finally:
            w.stop_run()

    def test_double_start_is_ignored_with_warning(self, tmp_path: Path, caplog) -> None:
        w = StageDumpWriter(dump_dir=tmp_path, run_id_factory=lambda: "RUN-A")
        w.start_run()
        try:
            w.start_run()  # 2 回目は無視されるはず
            # run_dir は最初の run のまま
            assert w.run_dir is not None and w.run_dir.name == "RUN-A"
        finally:
            w.stop_run()

    def test_stop_run_idempotent(self, tmp_path: Path) -> None:
        w = StageDumpWriter(dump_dir=tmp_path)
        w.start_run()
        w.stop_run()
        # 2回目は no-op
        w.stop_run()
        assert w.run_dir is None


# ============================================================
# StageDumpWriter — 書き出し
# ============================================================
class TestStageDumpWrites:
    def _make_writer(self, tmp_path: Path, stages=None) -> StageDumpWriter:
        return StageDumpWriter(
            dump_dir=tmp_path,
            stages=stages or ("vad", "asr", "translate", "tts"),
            run_id_factory=lambda: "R",
        )

    def test_on_asr_writes_json(self, tmp_path: Path) -> None:
        w = self._make_writer(tmp_path)
        w.start_run()
        try:
            w.on_asr(42, "hello world", "en")
            w.stop_run()  # flush 兼用
            path = tmp_path / "R" / "seq_0042_asr.json"
            assert path.is_file()
            data = json.loads(path.read_text(encoding="utf-8"))
            assert data["seq_id"] == 42
            assert data["stage"] == "asr"
            assert data["src_lang"] == "en"
            assert data["text"] == "hello world"
        finally:
            # 既に stop 済みでも安全
            w.stop_run()

    def test_on_translate_writes_json_with_both_texts(self, tmp_path: Path) -> None:
        w = self._make_writer(tmp_path)
        w.start_run()
        try:
            w.on_translate(7, "hello", "en", "こんにちは", "ja")
            w.stop_run()
            data = json.loads(
                (tmp_path / "R" / "seq_0007_translate.json").read_text(encoding="utf-8")
            )
            assert data["src_text"] == "hello"
            assert data["tgt_text"] == "こんにちは"
            assert data["src_lang"] == "en"
            assert data["tgt_lang"] == "ja"
        finally:
            w.stop_run()

    def test_on_vad_writes_wav_int16_mono(self, tmp_path: Path) -> None:
        # 0.1 秒分のサイン波(float32)を投入し、WAV が int16/mono/16kHz で書かれることを確認
        sr = 16000
        t = np.linspace(0, 0.1, sr // 10, endpoint=False)
        pcm = (0.3 * np.sin(2 * np.pi * 220 * t)).astype(np.float32)
        w = self._make_writer(tmp_path)
        w.start_run()
        try:
            w.on_vad(3, pcm, sr)
            w.stop_run()
            with wave.open(str(tmp_path / "R" / "seq_0003_vad.wav"), "rb") as wf:
                assert wf.getnchannels() == 1
                assert wf.getsampwidth() == 2  # int16
                assert wf.getframerate() == 16000
                frames = wf.readframes(wf.getnframes())
            i16 = np.frombuffer(frames, dtype=np.int16)
            assert i16.size == pcm.size
            # ピーク振幅が概ね 0.3 * 32767 のオーダーであること
            assert i16.max() > 5000
        finally:
            w.stop_run()

    def test_on_tts_passes_through_samplerate(self, tmp_path: Path) -> None:
        # TTS は 22050Hz 等、内部標準と異なるサンプルレートが来る可能性がある
        pcm = (np.ones(2205, dtype=np.float32) * 0.5)
        w = self._make_writer(tmp_path)
        w.start_run()
        try:
            w.on_tts(11, pcm, 22050)
            w.stop_run()
            with wave.open(str(tmp_path / "R" / "seq_0011_tts.wav"), "rb") as wf:
                assert wf.getframerate() == 22050
        finally:
            w.stop_run()

    def test_stages_filter(self, tmp_path: Path) -> None:
        # asr と translate だけを書く設定
        w = self._make_writer(tmp_path, stages=("asr", "translate"))
        w.start_run()
        try:
            w.on_vad(1, np.zeros(160, dtype=np.float32), 16000)
            w.on_asr(1, "hi", "en")
            w.on_translate(1, "hi", "en", "やあ", "ja")
            w.on_tts(1, np.zeros(160, dtype=np.float32), 16000)
            w.stop_run()
            run = tmp_path / "R"
            assert (run / "seq_0001_asr.json").exists()
            assert (run / "seq_0001_translate.json").exists()
            assert not (run / "seq_0001_vad.wav").exists()
            assert not (run / "seq_0001_tts.wav").exists()
        finally:
            w.stop_run()


# ============================================================
# StageDumpWriter — 古い run の自動削除
# ============================================================
class TestStageDumpPruning:
    def test_prune_keeps_latest_max_runs_minus_one(self, tmp_path: Path) -> None:
        # 既存の run ディレクトリを 5 個作る(中身: run.json を空 dict で)
        for name in ("R01", "R02", "R03", "R04", "R05"):
            d = tmp_path / name
            d.mkdir()
            (d / "run.json").write_text("{}", encoding="utf-8")

        # max_runs=3 → 新規 1 つ作るので、既存は 2 つだけ残るはず(R04, R05)
        w = StageDumpWriter(
            dump_dir=tmp_path, max_runs=3, run_id_factory=lambda: "R06"
        )
        w.start_run()
        try:
            remaining = sorted(p.name for p in tmp_path.iterdir() if p.is_dir())
            assert remaining == ["R04", "R05", "R06"]
        finally:
            w.stop_run()

    def test_prune_disabled_when_max_runs_zero(self, tmp_path: Path) -> None:
        for name in ("R01", "R02", "R03"):
            d = tmp_path / name
            d.mkdir()
            (d / "run.json").write_text("{}", encoding="utf-8")
        w = StageDumpWriter(
            dump_dir=tmp_path, max_runs=0, run_id_factory=lambda: "R04"
        )
        w.start_run()
        try:
            remaining = sorted(p.name for p in tmp_path.iterdir() if p.is_dir())
            assert remaining == ["R01", "R02", "R03", "R04"]
        finally:
            w.stop_run()

    def test_prune_ignores_non_run_dirs(self, tmp_path: Path) -> None:
        # run.json も seq_* も無いディレクトリは run とみなさない(削除対象外)
        (tmp_path / "scratch").mkdir()
        (tmp_path / "scratch" / "other.txt").write_text("x", encoding="utf-8")
        w = StageDumpWriter(
            dump_dir=tmp_path, max_runs=1, run_id_factory=lambda: "R01"
        )
        w.start_run()
        try:
            remaining = sorted(p.name for p in tmp_path.iterdir() if p.is_dir())
            assert "scratch" in remaining
            assert "R01" in remaining
        finally:
            w.stop_run()


# ============================================================
# StageDumpWriter — start 前の呼び出しは drop(警告のみ)
# ============================================================
class TestStageDumpBeforeStart:
    def test_on_asr_before_start_run_is_dropped(self, tmp_path: Path) -> None:
        w = StageDumpWriter(dump_dir=tmp_path)
        # start_run していない状態
        w.on_asr(1, "hi", "en")  # 例外を出さない
        # ファイルも作られない
        assert not any(tmp_path.iterdir())


# ============================================================
# StageDumpWriter — stages の不正値を素通しせずに弾く
# ============================================================
class TestStageDumpStagesValidation:
    def test_invalid_stage_name_is_filtered_out(self, tmp_path: Path) -> None:
        w = StageDumpWriter(
            dump_dir=tmp_path, stages=("asr", "bogus", "translate"),
            run_id_factory=lambda: "R",
        )
        assert w.stages == frozenset({"asr", "translate"})

    def test_empty_stages_means_nothing_written(self, tmp_path: Path) -> None:
        w = StageDumpWriter(
            dump_dir=tmp_path, stages=(), run_id_factory=lambda: "R"
        )
        w.start_run()
        try:
            w.on_asr(1, "hi", "en")
            w.stop_run()
            assert not (tmp_path / "R" / "seq_0001_asr.json").exists()
        finally:
            w.stop_run()
