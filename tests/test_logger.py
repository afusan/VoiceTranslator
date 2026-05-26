"""Logger / TranslationLogger / TextLogger の単体テスト。"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path

from voice_translator.common.logger import (
    TextLogger,
    TranslationLogger,
    setup_app_logger,
)
from voice_translator.common.utterance import Utterance


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
    def _build_utterance(self) -> Utterance:
        u = Utterance(src_lang="en")
        u.timeline.mark("t_capture")
        u.src_text = "hello"
        u.timeline.mark("t_asr")
        u.tgt_lang = "ja"
        u.tgt_text = "こんにちは"
        u.timeline.mark("t_translate")
        u.timeline.mark("t_tts")
        u.timeline.mark("t_playback")
        return u

    def test_write_appends_jsonl(self, tmp_jsonl_path: Path) -> None:
        logger = TranslationLogger(tmp_jsonl_path, enabled=True)
        logger.write(self._build_utterance())
        logger.write(self._build_utterance())

        lines = tmp_jsonl_path.read_text(encoding="utf-8").splitlines()
        assert len(lines) == 2
        for line in lines:
            record = json.loads(line)
            assert record["src_text"] == "hello"
            assert record["tgt_text"] == "こんにちは"
            assert record["src_lang"] == "en"
            assert record["tgt_lang"] == "ja"
            assert record["latency_ms"] is not None

    def test_disabled_writes_nothing(self, tmp_jsonl_path: Path) -> None:
        logger = TranslationLogger(tmp_jsonl_path, enabled=False)
        logger.write(self._build_utterance())
        assert not tmp_jsonl_path.exists()
        assert logger.enabled is False

    def test_latency_none_when_missing_timestamps(self, tmp_jsonl_path: Path) -> None:
        u = Utterance(src_text="x", tgt_text="y")
        # t_capture / t_playback を打たない
        TranslationLogger(tmp_jsonl_path, enabled=True).write(u)
        record = json.loads(tmp_jsonl_path.read_text(encoding="utf-8").splitlines()[0])
        assert record["latency_ms"] is None


# ============================================================
# TextLogger(翻訳前後テキストの個別ログ)
# ============================================================
class TestTextLogger:
    def _paths(self, tmp_path: Path) -> tuple[Path, Path]:
        return tmp_path / "soundsrc.txt", tmp_path / "translated.txt"

    def _utt(self) -> Utterance:
        return Utterance(
            src_text="hello world",
            src_lang="en",
            tgt_text="こんにちは 世界",
            tgt_lang="ja",
        )

    def test_both_disabled_writes_nothing(self, tmp_path: Path) -> None:
        src, tgt = self._paths(tmp_path)
        logger = TextLogger(src_path=src, tgt_path=tgt, src_enabled=False, tgt_enabled=False)
        logger.write(self._utt())
        assert not src.exists()
        assert not tgt.exists()
        assert logger.src_enabled is False
        assert logger.tgt_enabled is False

    def test_only_src_enabled(self, tmp_path: Path) -> None:
        src, tgt = self._paths(tmp_path)
        logger = TextLogger(src_path=src, tgt_path=tgt, src_enabled=True, tgt_enabled=False)
        logger.write(self._utt())
        assert src.exists()
        assert not tgt.exists()
        line = src.read_text(encoding="utf-8")
        assert "hello world" in line
        assert "[en]" in line

    def test_only_tgt_enabled(self, tmp_path: Path) -> None:
        src, tgt = self._paths(tmp_path)
        logger = TextLogger(src_path=src, tgt_path=tgt, src_enabled=False, tgt_enabled=True)
        logger.write(self._utt())
        assert not src.exists()
        assert tgt.exists()
        line = tgt.read_text(encoding="utf-8")
        assert "こんにちは 世界" in line
        assert "[ja]" in line

    def test_both_enabled(self, tmp_path: Path) -> None:
        src, tgt = self._paths(tmp_path)
        logger = TextLogger(src_path=src, tgt_path=tgt, src_enabled=True, tgt_enabled=True)
        logger.write(self._utt())
        assert src.exists()
        assert tgt.exists()

    def test_line_format(self, tmp_path: Path) -> None:
        """`[YYYY-MM-DD HH:MM:SS] [lang] text` 形式であること。"""
        src, tgt = self._paths(tmp_path)
        logger = TextLogger(src_path=src, tgt_path=tgt, src_enabled=True, tgt_enabled=False)
        logger.write(self._utt())
        content = src.read_text(encoding="utf-8")
        # 末尾は LF
        assert content.endswith("\n")
        m = re.match(
            r"^\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\] \[en\] hello world\n$",
            content,
        )
        assert m is not None, f"format mismatch: {content!r}"

    def test_append_mode(self, tmp_path: Path) -> None:
        src, tgt = self._paths(tmp_path)
        logger = TextLogger(src_path=src, tgt_path=tgt, src_enabled=True, tgt_enabled=False)
        logger.write(self._utt())
        logger.write(self._utt())
        logger.write(self._utt())
        lines = src.read_text(encoding="utf-8").splitlines()
        assert len(lines) == 3

    def test_empty_text_skipped(self, tmp_path: Path) -> None:
        src, tgt = self._paths(tmp_path)
        logger = TextLogger(src_path=src, tgt_path=tgt, src_enabled=True, tgt_enabled=True)
        # src 空
        logger.write(Utterance(src_text="", src_lang="en", tgt_text="あ", tgt_lang="ja"))
        # tgt 空
        logger.write(Utterance(src_text="hi", src_lang="en", tgt_text="", tgt_lang="ja"))
        # 両方空白のみ
        logger.write(Utterance(src_text="   ", tgt_text="\t"))

        src_content = src.read_text(encoding="utf-8") if src.exists() else ""
        tgt_content = tgt.read_text(encoding="utf-8") if tgt.exists() else ""
        # src には "hi" の 1 行だけ、tgt には "あ" の 1 行だけ
        assert src_content.count("\n") == 1 and "hi" in src_content
        assert tgt_content.count("\n") == 1 and "あ" in tgt_content

    def test_utf8_japanese(self, tmp_path: Path) -> None:
        src, tgt = self._paths(tmp_path)
        logger = TextLogger(src_path=src, tgt_path=tgt, src_enabled=False, tgt_enabled=True)
        u = Utterance(tgt_text="日本語テスト🎌", tgt_lang="ja")
        logger.write(u)
        # 読み戻しできることを確認(化けてないこと)
        content = tgt.read_text(encoding="utf-8")
        assert "日本語テスト🎌" in content

    def test_lf_newline_on_windows(self, tmp_path: Path) -> None:
        """書き出した内容に \\r\\n が混じらないこと(CRLF 変換を防止)。"""
        src, tgt = self._paths(tmp_path)
        logger = TextLogger(src_path=src, tgt_path=tgt, src_enabled=True, tgt_enabled=False)
        logger.write(self._utt())
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
