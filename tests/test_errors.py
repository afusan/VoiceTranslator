"""AppError / Severity / ErrorHandler の単体テスト。

R-Error-Context: callback の新シグネチャ(message + exc/stage/seq_id kwargs)
とログ整形(seq=/stage=/[SEVERITY]/caused by)を検証する項目を追加。
"""

from __future__ import annotations

import logging

from voice_translator.common.error_handler import ErrorAction, ErrorHandler
from voice_translator.common.errors import (
    AppError,
    FatalError,
    RecoverableError,
    Severity,
    SkipError,
    WarnError,
)


class TestErrorClasses:
    def test_severity_is_set(self) -> None:
        assert FatalError("x").severity is Severity.FATAL
        assert RecoverableError("x").severity is Severity.RECOVERABLE
        assert SkipError("x").severity is Severity.SKIP
        assert WarnError("x").severity is Severity.WARN

    def test_cause_is_attached(self) -> None:
        inner = ValueError("inner")
        e = FatalError("outer", cause=inner)
        assert e.__cause__ is inner

    def test_base_app_error_directly(self) -> None:
        e = AppError("x", Severity.WARN)
        assert e.severity is Severity.WARN


class TestErrorHandlerActions:
    """severity → ErrorAction の対応関係。"""

    def test_fatal_returns_stop_and_calls_notifier(self) -> None:
        called: list[dict] = []

        def notifier(message, *, exc=None, stage=None, seq_id=None):
            called.append({"message": message, "exc": exc, "stage": stage, "seq_id": seq_id})

        handler = ErrorHandler(on_fatal=notifier)
        action = handler.handle(FatalError("boom"))
        assert action == ErrorAction.STOP
        assert len(called) == 1
        assert called[0]["message"] == "boom"

    def test_recoverable_returns_retry(self) -> None:
        handler = ErrorHandler()
        assert handler.handle(RecoverableError("timeout")) == ErrorAction.RETRY

    def test_skip_returns_skip(self) -> None:
        handler = ErrorHandler()
        assert handler.handle(SkipError("empty")) == ErrorAction.SKIP

    def test_warn_returns_continue_and_calls_notifier(self) -> None:
        called: list[dict] = []

        def notifier(message, *, exc=None, stage=None, seq_id=None):
            called.append({"message": message, "exc": exc})

        handler = ErrorHandler(on_warn=notifier)
        action = handler.handle(WarnError("latency"))
        assert action == ErrorAction.CONTINUE
        assert len(called) == 1
        assert called[0]["message"] == "latency"

    def test_unknown_exception_treated_as_fatal(self) -> None:
        called: list = []
        handler = ErrorHandler(on_fatal=lambda m, **_kw: called.append(m))
        action = handler.handle(RuntimeError("?"))
        assert action == ErrorAction.STOP
        assert called  # 通知が呼ばれた


class TestErrorHandlerContext:
    """stage / seq_id の context が callback とログに反映されること。"""

    def test_context_passed_to_callback(self) -> None:
        seen: list[dict] = []

        def notifier(message, *, exc=None, stage=None, seq_id=None):
            seen.append({"message": message, "exc": exc, "stage": stage, "seq_id": seq_id})

        handler = ErrorHandler(on_fatal=notifier)
        original = FatalError("boom")
        handler.handle(original, stage="ASR", seq_id=42)
        assert seen[0]["message"] == "boom"
        assert seen[0]["stage"] == "ASR"
        assert seen[0]["seq_id"] == 42
        assert seen[0]["exc"] is original

    def test_log_contains_seq_and_stage(self, caplog) -> None:
        caplog.set_level(logging.ERROR, logger="voice_translator")
        handler = ErrorHandler()
        handler.handle(FatalError("boom"), stage="Translator", seq_id=7)
        # ログメッセージに seq= と stage= が含まれる
        messages = [r.message for r in caplog.records]
        assert any("seq=7" in m and "stage=Translator" in m and "boom" in m for m in messages)

    def test_log_contains_severity_tag(self, caplog) -> None:
        caplog.set_level(logging.INFO, logger="voice_translator")
        handler = ErrorHandler()
        handler.handle(SkipError("empty pcm"), stage="ASR", seq_id=1)
        # SKIP タグが含まれる
        messages = [r.message for r in caplog.records]
        assert any("[SKIP]" in m and "empty pcm" in m for m in messages)

    def test_log_contains_cause(self, caplog) -> None:
        caplog.set_level(logging.ERROR, logger="voice_translator")
        inner = ValueError("inner reason")
        exc = FatalError("outer", cause=inner)
        handler = ErrorHandler()
        handler.handle(exc, stage="ASR", seq_id=1)
        # caused by 元例外型 + メッセージ が含まれる
        messages = [r.message for r in caplog.records]
        assert any("caused by ValueError" in m and "inner reason" in m for m in messages)

    def test_no_context_omitted_from_log(self, caplog) -> None:
        """stage/seq_id を渡さない場合は seq=/stage= の prefix が出ない。"""
        caplog.set_level(logging.ERROR, logger="voice_translator")
        handler = ErrorHandler()
        handler.handle(FatalError("standalone"))
        messages = [r.message for r in caplog.records]
        # メッセージはあるが seq= / stage= は付かない
        target = [m for m in messages if "standalone" in m]
        assert target
        for m in target:
            assert "seq=" not in m
            assert "stage=" not in m

    def test_unclassified_exception_log_shows_unclassified_tag(self, caplog) -> None:
        caplog.set_level(logging.ERROR, logger="voice_translator")
        handler = ErrorHandler()
        handler.handle(RuntimeError("raw"), stage="ASR", seq_id=9)
        messages = [r.message for r in caplog.records]
        assert any("FATAL/未分類" in m for m in messages)


class TestErrorHandlerLogLevels:
    """severity → logging level の対応。"""

    def test_skip_logged_at_info(self, caplog) -> None:
        caplog.set_level(logging.DEBUG, logger="voice_translator")
        handler = ErrorHandler()
        handler.handle(SkipError("e"))
        skip_records = [r for r in caplog.records if "[SKIP]" in r.message]
        assert skip_records
        assert all(r.levelno == logging.INFO for r in skip_records)

    def test_warn_logged_at_warning(self, caplog) -> None:
        caplog.set_level(logging.DEBUG, logger="voice_translator")
        handler = ErrorHandler()
        handler.handle(WarnError("e"))
        warn_records = [r for r in caplog.records if "[WARN]" in r.message]
        assert warn_records
        assert all(r.levelno == logging.WARNING for r in warn_records)

    def test_fatal_logged_at_error(self, caplog) -> None:
        caplog.set_level(logging.DEBUG, logger="voice_translator")
        handler = ErrorHandler()
        handler.handle(FatalError("e"))
        fatal_records = [r for r in caplog.records if "[FATAL]" in r.message]
        assert fatal_records
        assert all(r.levelno == logging.ERROR for r in fatal_records)

    def test_recoverable_logged_at_warning(self, caplog) -> None:
        caplog.set_level(logging.DEBUG, logger="voice_translator")
        handler = ErrorHandler()
        handler.handle(RecoverableError("e"))
        rec_records = [r for r in caplog.records if "[RECOVERABLE]" in r.message]
        assert rec_records
        assert all(r.levelno == logging.WARNING for r in rec_records)


class TestErrorHandlerCallbackResilience:
    """callback が例外を投げてもハンドラ自体は壊れない。"""

    def test_notifier_exception_does_not_propagate(self, caplog) -> None:
        caplog.set_level(logging.ERROR, logger="voice_translator")

        def boom(message, **_kwargs):
            raise RuntimeError("callback broke")

        handler = ErrorHandler(on_fatal=boom)
        # handle() 自体は例外を返さず、コールバック例外はログに残る
        action = handler.handle(FatalError("e"))
        assert action == ErrorAction.STOP
        assert any("エラー通知コールバックで例外" in r.message for r in caplog.records)
