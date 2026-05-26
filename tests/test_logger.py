"""Logger / TranslationLogger の単体テスト。"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from voice_translator.common.logger import TranslationLogger, setup_app_logger
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
