"""PipelineCoordinator._call_with_retry の単体テスト(Phase E)。

リトライ機構を pipeline ループから切り離して検証する。実バックエンドや実スレッドを
使わず、最小構成の coordinator を組み立てて `_call_with_retry` を直接叩く。
"""

from __future__ import annotations

import logging
import threading
from unittest.mock import MagicMock

import pytest

from voice_translator.common.error_handler import ErrorAction, ErrorHandler
from voice_translator.common.errors import (
    FatalError,
    RecoverableError,
    SkipError,
    WarnError,
)
from voice_translator.common.pipeline import PipelineCoordinator


def _make_coord(
    *,
    max_retries: int = 3,
    retry_base_sec: float = 0.01,
    retry_max_sec: float = 0.05,
) -> PipelineCoordinator:
    """テスト用に最小構成の PipelineCoordinator を作る。

    各 backend は MagicMock(申告 I/F を持たないため、編成上はレイヤ既定の
    単体 backend とみなされる)。`_call_with_retry` の動作だけを確認するため、
    スレッドは起動しない。バックオフは現実的に短く(0.01s)。
    """
    logger = logging.getLogger("vt_test")
    handler = ErrorHandler(logger=logger)
    coord = PipelineCoordinator(
        capture=MagicMock(),
        vad=MagicMock(),
        asr=MagicMock(),
        translator=MagicMock(),
        tts=MagicMock(),
        output=MagicMock(),
        error_handler=handler,
        max_retries=max_retries,
        retry_base_sec=retry_base_sec,
        retry_max_sec=retry_max_sec,
    )
    return coord


class TestRetrySuccess:
    def test_immediate_success_returns_continue(self) -> None:
        coord = _make_coord()
        backend = MagicMock()
        result, action = coord._call_with_retry(
            lambda: "ok", stage="ASR", seq_id=1, backend=backend,
        )
        assert action == ErrorAction.CONTINUE
        assert result == "ok"
        # record_error は呼ばれない(失敗なし)
        backend.record_error.assert_not_called()

    def test_recovers_after_some_retries(self) -> None:
        """RecoverableError → 数回失敗後に成功なら、最終結果が返る。"""
        coord = _make_coord(max_retries=3)
        backend = MagicMock()
        attempts: list[int] = []

        def flaky():
            attempts.append(1)
            if len(attempts) <= 2:
                raise RecoverableError("transient")
            return "recovered"

        result, action = coord._call_with_retry(
            flaky, stage="ASR", seq_id=1, backend=backend,
        )
        assert action == ErrorAction.CONTINUE
        assert result == "recovered"
        # 3 回目で成功(2 回失敗 + 1 回成功)
        assert len(attempts) == 3
        # 失敗のたびに record_error が呼ばれている
        assert backend.record_error.call_count == 2


class TestRetryExhaustion:
    def test_recoverable_exhausted_escalates_to_stop(self) -> None:
        """RecoverableError が max_retries+1 回連続したら STOP に escalate。"""
        coord = _make_coord(max_retries=2)
        backend = MagicMock()

        def always_flaky():
            raise RecoverableError("never recovers")

        _, action = coord._call_with_retry(
            always_flaky, stage="ASR", seq_id=1, backend=backend,
        )
        assert action == ErrorAction.STOP
        # 初回 + 2 回リトライ = 3 回試行 → record_error も 3 回
        assert backend.record_error.call_count == 3


class TestRetryFatal:
    def test_fatal_stops_immediately_no_retry(self) -> None:
        """FatalError は初回で STOP、リトライしない。"""
        coord = _make_coord(max_retries=3)
        backend = MagicMock()
        attempts: list[int] = []

        def fatal_call():
            attempts.append(1)
            raise FatalError("dead")

        _, action = coord._call_with_retry(
            fatal_call, stage="ASR", seq_id=1, backend=backend,
        )
        assert action == ErrorAction.STOP
        assert len(attempts) == 1  # リトライなし
        assert backend.record_error.call_count == 1

    def test_unclassified_exception_treated_as_stop(self) -> None:
        """AppError 以外(素の RuntimeError 等)は FATAL 扱いで STOP。"""
        coord = _make_coord(max_retries=3)
        backend = MagicMock()
        _, action = coord._call_with_retry(
            lambda: (_ for _ in ()).throw(RuntimeError("boom")),
            stage="ASR", seq_id=1, backend=backend,
        )
        assert action == ErrorAction.STOP


class TestRetrySkip:
    def test_skip_no_retry(self) -> None:
        """SkipError はリトライせず SKIP を返す(当該発話のみ破棄)。"""
        coord = _make_coord(max_retries=3)
        backend = MagicMock()
        attempts: list[int] = []

        def skip_call():
            attempts.append(1)
            raise SkipError("empty input")

        _, action = coord._call_with_retry(
            skip_call, stage="ASR", seq_id=1, backend=backend,
        )
        assert action == ErrorAction.SKIP
        assert len(attempts) == 1


class TestRetryWarn:
    def test_warn_returns_continue_no_result(self) -> None:
        """WarnError は CONTINUE を返す(継続するが結果なし)。"""
        coord = _make_coord(max_retries=3)
        backend = MagicMock()
        _, action = coord._call_with_retry(
            lambda: (_ for _ in ()).throw(WarnError("warn")),
            stage="ASR", seq_id=1, backend=backend,
        )
        assert action == ErrorAction.CONTINUE


class TestRetryRecordError:
    def test_backend_without_record_error_is_safe(self) -> None:
        """backend が record_error を持たなくても落ちない。"""
        coord = _make_coord(max_retries=1)

        class _Bare:
            pass

        _, action = coord._call_with_retry(
            lambda: (_ for _ in ()).throw(FatalError("e")),
            stage="X", seq_id=1, backend=_Bare(),
        )
        assert action == ErrorAction.STOP

    def test_record_error_exception_is_swallowed(self) -> None:
        """backend.record_error が例外を投げても本体は止まらない。"""
        coord = _make_coord(max_retries=0)
        backend = MagicMock()
        backend.record_error = MagicMock(side_effect=RuntimeError("history bug"))
        _, action = coord._call_with_retry(
            lambda: (_ for _ in ()).throw(FatalError("e")),
            stage="X", seq_id=1, backend=backend,
        )
        assert action == ErrorAction.STOP  # 動作は通常通り


class TestRetryStopEventResponsiveness:
    def test_aborts_on_stop_event_during_backoff(self) -> None:
        """バックオフ中に stop_event が立ったら、待たずに STOP で抜ける。"""
        coord = _make_coord(max_retries=10, retry_base_sec=0.5, retry_max_sec=1.0)

        # 別スレッドで少し待ってから stop_event を立てる
        def trigger_stop():
            import time
            time.sleep(0.05)
            coord._stop_event.set()

        threading.Thread(target=trigger_stop, daemon=True).start()

        def always_recoverable():
            raise RecoverableError("transient")

        backend = MagicMock()
        # バックオフ中に stop_event が立つ。リトライ全周より十分短時間で抜けること。
        import time as _t
        t0 = _t.monotonic()
        _, action = coord._call_with_retry(
            always_recoverable, stage="X", seq_id=1, backend=backend,
        )
        elapsed = _t.monotonic() - t0
        assert action == ErrorAction.STOP
        # 1 秒以上待つ全周時間ではなく、stop 検出直後に抜ける
        assert elapsed < 0.5, f"stop 検出後に待ち過ぎ: {elapsed:.2f}s"
