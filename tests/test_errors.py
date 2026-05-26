"""AppError / Severity / ErrorHandler の単体テスト。"""

from __future__ import annotations

import logging

import pytest

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


class TestErrorHandler:
    def test_fatal_returns_stop_and_calls_notifier(self) -> None:
        called: list[str] = []
        handler = ErrorHandler(on_fatal=lambda m: called.append(m))
        action = handler.handle(FatalError("boom"))
        assert action == ErrorAction.STOP
        assert called == ["boom"]

    def test_recoverable_returns_retry(self) -> None:
        handler = ErrorHandler()
        assert handler.handle(RecoverableError("timeout")) == ErrorAction.RETRY

    def test_skip_returns_skip(self) -> None:
        handler = ErrorHandler()
        assert handler.handle(SkipError("empty")) == ErrorAction.SKIP

    def test_warn_returns_continue_and_calls_notifier(self) -> None:
        called: list[str] = []
        handler = ErrorHandler(on_warn=lambda m: called.append(m))
        action = handler.handle(WarnError("latency"))
        assert action == ErrorAction.CONTINUE
        assert called == ["latency"]

    def test_unknown_exception_treated_as_fatal(self) -> None:
        called: list[str] = []
        handler = ErrorHandler(on_fatal=lambda m: called.append(m))
        action = handler.handle(RuntimeError("?"))
        assert action == ErrorAction.STOP
        assert called  # 通知が呼ばれた

    def test_max_retries_default(self) -> None:
        handler = ErrorHandler(max_retries=3)
        assert handler.max_retries == 3
