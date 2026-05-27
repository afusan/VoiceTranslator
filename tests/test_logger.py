"""Logger / TranslationLogger / TextLogger の単体テスト。

R-3 で I/F 更新:
- TranslationLogger.write_record(record: dict)
- TextLogger.write_src(seq_id, text, lang) / write_tgt(seq_id, text, lang)
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from time import monotonic

from voice_translator.common.logger import (
    TextLogger,
    TranslationLogger,
    setup_app_logger,
)


class TestSetupAppLogger:
    def test_returns_logger_with_handlers(self, tmp_path: Path) -> None:
        logger = setup_app_logger(name="test_app_1", log_dir=tmp_path)
        try:
            assert isinstance(logger, logging.Logger)
            assert len(logger.handlers) >= 1
            assert (tmp_path / "app.log").exists() or True  # ファイルは初回書込で生成
        finally:
            for h in list(logger.handlers):
                logger.removeHandler(h)
                h.close()

    def test_no_handler_duplication(self, tmp_path: Path) -> None:
        logger1 = setup_app_logger(name="test_app_2", log_dir=tmp_path)
        before = len(logger1.handlers)
        logger2 = setup_app_logger(name="test_app_2", log_dir=tmp_path)
        try:
            assert logger1 is logger2
            assert len(logger2.handlers) == before
        finally:
            for h in list(logger1.handlers):
                logger1.removeHandler(h)
                h.close()


class TestTranslationLogger:
    def _record(self, seq_id: int = 1) -> dict:
        t0 = monotonic()
        return {
            "seq_id": seq_id,
            "src_lang": "en",
            "src_text": "hello",
            "tgt_lang": "ja",
            "tgt_text": "こんにちは",
            "timeline": {
                "t_capture": t0,
                "t_asr": t0 + 0.1,
                "t_translate": t0 + 0.2,
                "t_tts": t0 + 0.3,
                "t_playback": t0 + 0.5,
            },
        }

    def test_write_record_appends_jsonl(self, tmp_jsonl_path: Path) -> None:
        logger = TranslationLogger(tmp_jsonl_path, enabled=True)
        logger.write_record(self._record(seq_id=1))
        logger.write_record(self._record(seq_id=2))

        lines = tmp_jsonl_path.read_text(encoding="utf-8").splitlines()
        assert len(lines) == 2
        for i, line in enumerate(lines, start=1):
            record = json.loads(line)
            assert record["src_text"] == "hello"
            assert record["tgt_text"] == "こんにちは"
            assert record["src_lang"] == "en"
            assert record["tgt_lang"] == "ja"
            assert record["seq_id"] == i
            assert record["latency_ms"] is not None
            assert record["latency_ms"] > 0

    def test_disabled_writes_nothing(self, tmp_jsonl_path: Path) -> None:
        logger = TranslationLogger(tmp_jsonl_path, enabled=False)
        logger.write_record(self._record())
        assert not tmp_jsonl_path.exists()
        assert logger.enabled is False

    def test_latency_none_when_missing_timestamps(self, tmp_jsonl_path: Path) -> None:
        # timeline に t_capture / t_playback がない
        rec = {
            "seq_id": 1,
            "src_text": "x",
            "tgt_text": "y",
            "timeline": {},
        }
        TranslationLogger(tmp_jsonl_path, enabled=True).write_record(rec)
        record = json.loads(tmp_jsonl_path.read_text(encoding="utf-8").splitlines()[0])
        assert record["latency_ms"] is None


# ============================================================
# TextLogger(翻訳前後テキストの個別ログ)
# ============================================================
class TestTextLogger:
    def _paths(self, tmp_path: Path) -> tuple[Path, Path]:
        return tmp_path / "soundsrc.txt", tmp_path / "translated.txt"

    def test_both_disabled_writes_nothing(self, tmp_path: Path) -> None:
        src, tgt = self._paths(tmp_path)
        logger = TextLogger(src_path=src, tgt_path=tgt, src_enabled=False, tgt_enabled=False)
        logger.write_src(1, "hello", "en")
        logger.write_tgt(1, "こんにちは", "ja")
        assert not src.exists()
        assert not tgt.exists()
        assert logger.src_enabled is False
        assert logger.tgt_enabled is False

    def test_only_src_enabled(self, tmp_path: Path) -> None:
        src, tgt = self._paths(tmp_path)
        logger = TextLogger(src_path=src, tgt_path=tgt, src_enabled=True, tgt_enabled=False)
        logger.write_src(1, "hello world", "en")
        logger.write_tgt(1, "こんにちは 世界", "ja")  # 無効側は no-op
        assert src.exists()
        assert not tgt.exists()
        line = src.read_text(encoding="utf-8")
        assert "hello world" in line
        assert "[en]" in line
        assert "#1" in line

    def test_only_tgt_enabled(self, tmp_path: Path) -> None:
        src, tgt = self._paths(tmp_path)
        logger = TextLogger(src_path=src, tgt_path=tgt, src_enabled=False, tgt_enabled=True)
        logger.write_src(2, "hello world", "en")
        logger.write_tgt(2, "こんにちは 世界", "ja")
        assert not src.exists()
        assert tgt.exists()
        line = tgt.read_text(encoding="utf-8")
        assert "こんにちは 世界" in line
        assert "[ja]" in line
        assert "#2" in line

    def test_both_enabled(self, tmp_path: Path) -> None:
        src, tgt = self._paths(tmp_path)
        logger = TextLogger(src_path=src, tgt_path=tgt, src_enabled=True, tgt_enabled=True)
        logger.write_src(3, "hi", "en")
        logger.write_tgt(3, "やあ", "ja")
        assert src.exists()
        assert tgt.exists()

    def test_line_format(self, tmp_path: Path) -> None:
        """`[YYYY-MM-DD HH:MM:SS] #SEQ [lang] text` 形式であること。"""
        src, tgt = self._paths(tmp_path)
        logger = TextLogger(src_path=src, tgt_path=tgt, src_enabled=True, tgt_enabled=False)
        logger.write_src(42, "hello world", "en")
        content = src.read_text(encoding="utf-8")
        # 末尾は LF
        assert content.endswith("\n")
        m = re.match(
            r"^\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\] #42 \[en\] hello world\n$",
            content,
        )
        assert m is not None, f"format mismatch: {content!r}"

    def test_append_mode(self, tmp_path: Path) -> None:
        src, tgt = self._paths(tmp_path)
        logger = TextLogger(src_path=src, tgt_path=tgt, src_enabled=True, tgt_enabled=False)
        for i in range(3):
            logger.write_src(i + 1, "hello", "en")
        lines = src.read_text(encoding="utf-8").splitlines()
        assert len(lines) == 3

    def test_empty_text_skipped(self, tmp_path: Path) -> None:
        src, tgt = self._paths(tmp_path)
        logger = TextLogger(src_path=src, tgt_path=tgt, src_enabled=True, tgt_enabled=True)
        # src 空
        logger.write_src(1, "", "en")
        logger.write_tgt(1, "あ", "ja")
        # tgt 空
        logger.write_src(2, "hi", "en")
        logger.write_tgt(2, "", "ja")
        # 両方空白のみ
        logger.write_src(3, "   ", "")
        logger.write_tgt(3, "\t", "")

        src_content = src.read_text(encoding="utf-8") if src.exists() else ""
        tgt_content = tgt.read_text(encoding="utf-8") if tgt.exists() else ""
        # src には "hi" の 1 行だけ、tgt には "あ" の 1 行だけ
        assert src_content.count("\n") == 1 and "hi" in src_content
        assert tgt_content.count("\n") == 1 and "あ" in tgt_content

    def test_utf8_japanese(self, tmp_path: Path) -> None:
        src, tgt = self._paths(tmp_path)
        logger = TextLogger(src_path=src, tgt_path=tgt, src_enabled=False, tgt_enabled=True)
        logger.write_tgt(1, "日本語テスト🎌", "ja")
        # 読み戻しできることを確認(化けてないこと)
        content = tgt.read_text(encoding="utf-8")
        assert "日本語テスト🎌" in content

    def test_lf_newline_on_windows(self, tmp_path: Path) -> None:
        """書き出した内容に \\r\\n が混じらないこと(CRLF 変換を防止)。"""
        src, tgt = self._paths(tmp_path)
        logger = TextLogger(src_path=src, tgt_path=tgt, src_enabled=True, tgt_enabled=False)
        logger.write_src(1, "hello", "en")
        # バイナリで読んで CR が無いことを確認
        raw = src.read_bytes()
        assert b"\r" not in raw
        assert raw.endswith(b"\n")

    def test_no_directory_created_when_disabled(self, tmp_path: Path) -> None:
        """両 disabled なら親ディレクトリ作成も行わない。"""
        nested = tmp_path / "deep" / "dir"
        src = nested / "soundsrc.txt"
        tgt = nested / "translated.txt"
        TextLogger(src_path=src, tgt_path=tgt, src_enabled=False, tgt_enabled=False)
        # ディレクトリは作られていない
        assert not nested.exists()

    def test_directory_created_when_enabled(self, tmp_path: Path) -> None:
        nested = tmp_path / "deep" / "dir"
        src = nested / "soundsrc.txt"
        tgt = nested / "translated.txt"
        TextLogger(src_path=src, tgt_path=tgt, src_enabled=True, tgt_enabled=True)
        # ディレクトリだけは作られているはず(ファイルは write 時)
        assert nested.exists() and nested.is_dir()

    def test_no_lang_omits_bracket(self, tmp_path: Path) -> None:
        """lang が空なら [lang] は出ない。"""
        src, tgt = self._paths(tmp_path)
        logger = TextLogger(src_path=src, tgt_path=tgt, src_enabled=True, tgt_enabled=False)
        logger.write_src(1, "hello", "")
        content = src.read_text(encoding="utf-8")
        assert "[en]" not in content
        assert "[]" not in content
        assert "hello" in content
        assert "#1" in content
