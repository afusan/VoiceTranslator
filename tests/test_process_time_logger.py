"""ProcessTimeLogger の単体テスト。"""

from __future__ import annotations

import csv
from pathlib import Path

import pytest

from voice_translator.common.process_time_logger import (
    ProcessTimeLogger,
    derive_stage_durations,
)


# ----------------------------------------------------------
def _full_timeline_record(
    seq_id: int = 1,
    *,
    src_lang: str = "en",
    tgt_lang: str = "ja",
    src_text: str = "hello",
    tgt_text: str = "こんにちは",
) -> dict:
    """全マーカーが揃った record(0.1 秒刻みで進むダミー)。"""
    t0 = 1000.0
    return {
        "seq_id": seq_id,
        "src_lang": src_lang,
        "tgt_lang": tgt_lang,
        "src_text": src_text,
        "tgt_text": tgt_text,
        "timeline": {
            "t_capture":          t0 + 0.0,
            "t_vad_end":          t0 + 0.5,    # 発話 500ms
            "t_asr_start":        t0 + 0.6,    # ASR待ち 100ms
            "t_asr":              t0 + 1.2,    # ASR処理 600ms
            "t_translate_start":  t0 + 1.25,   # 翻訳待ち 50ms
            "t_translate":        t0 + 1.55,   # 翻訳処理 300ms
            "t_tts_start":        t0 + 1.6,    # TTS待ち 50ms
            "t_tts":              t0 + 1.9,    # TTS処理 300ms
            "t_playback_start":   t0 + 1.95,   # 再生待ち 50ms
            "t_playback":         t0 + 2.05,   # 再生 100ms
        },
    }


# ============================================================
class TestDeriveDurations:
    def test_full_timeline_produces_all_columns(self) -> None:
        rec = _full_timeline_record(seq_id=42)
        row = derive_stage_durations(rec)
        assert row["seq_id"] == "42"
        assert row["src_lang"] == "en"
        assert row["tgt_lang"] == "ja"
        # 500ms (utterance), 100ms (asr_wait), 600ms (asr_proc)
        assert row["utterance_ms"] == "500.0"
        assert row["asr_wait_ms"] == "100.0"
        assert row["asr_proc_ms"] == "600.0"
        assert row["translate_wait_ms"] == "50.0"
        assert row["translate_proc_ms"] == "300.0"
        assert row["tts_wait_ms"] == "50.0"
        assert row["tts_proc_ms"] == "300.0"
        assert row["output_wait_ms"] == "50.0"
        assert row["output_proc_ms"] == "100.0"
        # 2050ms total
        assert row["total_ms"] == "2050.0"
        assert row["src_chars"] == str(len("hello"))
        assert row["tgt_chars"] == str(len("こんにちは"))

    def test_missing_markers_yield_empty(self) -> None:
        """失敗時など、途中までしか timeline が無くても列は空文字で埋まる。"""
        rec = {
            "seq_id": 7,
            "src_lang": "en",
            "tgt_lang": "ja",
            "src_text": "x",
            "tgt_text": "",
            "timeline": {
                "t_capture": 1.0,
                "t_vad_end": 1.3,
                # ASR 以降は欠損
            },
        }
        row = derive_stage_durations(rec)
        assert row["utterance_ms"] == "300.0"
        assert row["asr_wait_ms"] == ""
        assert row["asr_proc_ms"] == ""
        assert row["total_ms"] == ""

    def test_empty_record_returns_empty_durations(self) -> None:
        row = derive_stage_durations({})
        assert row["seq_id"] == ""
        assert row["utterance_ms"] == ""
        assert row["total_ms"] == ""
        # src/tgt 文字数は 0
        assert row["src_chars"] == "0"
        assert row["tgt_chars"] == "0"


# ============================================================
class TestLoggerFile:
    def test_disabled_does_not_create_file(self, tmp_path: Path) -> None:
        path = tmp_path / "processtime.csv"
        logger = ProcessTimeLogger(path, enabled=False)
        logger.write_record(_full_timeline_record())
        assert not path.exists()
        assert logger.enabled is False

    def test_enabled_creates_header_and_writes_row(self, tmp_path: Path) -> None:
        path = tmp_path / "processtime.csv"
        logger = ProcessTimeLogger(path, enabled=True)
        logger.write_record(_full_timeline_record(seq_id=1))
        logger.write_record(_full_timeline_record(seq_id=2))

        assert path.exists()
        with path.open("r", encoding="utf-8") as f:
            rows = list(csv.reader(f))
        # 1 行目はヘッダ、その後 2 行
        assert rows[0][0] == "timestamp"
        assert "seq_id" in rows[0]
        assert "total_ms" in rows[0]
        assert len(rows) == 3  # header + 2

        # 2 行目以降の seq_id 列を確認
        seq_id_col = rows[0].index("seq_id")
        assert rows[1][seq_id_col] == "1"
        assert rows[2][seq_id_col] == "2"

    def test_header_not_duplicated_on_append(self, tmp_path: Path) -> None:
        """再 start でロガーを作り直してもヘッダは増えない(既存ファイル尊重)。"""
        path = tmp_path / "processtime.csv"
        ProcessTimeLogger(path, enabled=True).write_record(_full_timeline_record(seq_id=1))
        # 別インスタンス(=再起動相当)で追記
        ProcessTimeLogger(path, enabled=True).write_record(_full_timeline_record(seq_id=2))

        with path.open("r", encoding="utf-8") as f:
            rows = list(csv.reader(f))
        # ヘッダ + 2 行
        assert len(rows) == 3
        assert rows[0][0] == "timestamp"
        assert rows[1][0] != "timestamp"
        assert rows[2][0] != "timestamp"

    def test_columns_are_consistent_with_header(self, tmp_path: Path) -> None:
        path = tmp_path / "processtime.csv"
        logger = ProcessTimeLogger(path, enabled=True)
        logger.write_record(_full_timeline_record())
        with path.open("r", encoding="utf-8") as f:
            rows = list(csv.reader(f))
        assert len(rows[0]) == len(rows[1]), (
            "ヘッダとデータ行のカラム数が揃っていない"
        )
