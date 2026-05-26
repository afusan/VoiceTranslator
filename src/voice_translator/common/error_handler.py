"""エラー振り分けハンドラ。

役割: パイプラインや GUI から渡された例外を `Severity` に基づき、
致命=停止 / 回復=リトライ判定 / スキップ=破棄 / 警告=通知 の4挙動に振り分ける。
UI 層への通知は callback として渡される。
"""

from __future__ import annotations

import logging
from typing import Callable, Protocol

from .errors import AppError, Severity


class FatalNotifier(Protocol):
    """致命エラー通知のコールバック型(GUIのダイアログ等を想定)。"""

    def __call__(self, message: str) -> None: ...


class WarnNotifier(Protocol):
    """警告通知のコールバック型(GUIのバナー等を想定)。"""

    def __call__(self, message: str) -> None: ...


class ErrorAction(str):
    """ErrorHandler.handle() の戻り値となるアクション識別子。"""

    STOP = "STOP"        # FATAL: パイプライン停止
    RETRY = "RETRY"      # RECOVERABLE: 呼び出し元でリトライ判断
    SKIP = "SKIP"        # SKIP: 当該発話破棄
    CONTINUE = "CONTINUE"  # WARN: 継続


class ErrorHandler:
    """severity に基づいて挙動を決定するハンドラ。

    役割: `handle(exc)` を受けて、ログ出力 + UI通知 + 後続アクション(STOP/RETRY/SKIP/CONTINUE)を返す。
    """

    def __init__(
        self,
        *,
        logger: logging.Logger | None = None,
        on_fatal: FatalNotifier | None = None,
        on_warn: WarnNotifier | None = None,
        max_retries: int = 2,
    ) -> None:
        self._logger = logger or logging.getLogger("voice_translator")
        self._on_fatal = on_fatal
        self._on_warn = on_warn
        self._max_retries = max_retries

    @property
    def max_retries(self) -> int:
        """RECOVERABLE をリトライしてよい最大回数。"""
        return self._max_retries

    def handle(self, exc: BaseException) -> str:
        """例外を分類しアクションを返す。

        AppError 以外は FATAL として扱う(包み忘れの安全側挙動)。
        戻り値は `ErrorAction.*` のいずれか。
        """
        if not isinstance(exc, AppError):
            self._logger.exception("未分類の例外を FATAL として処理: %s", exc)
            if self._on_fatal:
                self._on_fatal(str(exc))
            return ErrorAction.STOP

        severity = exc.severity
        message = str(exc)

        if severity is Severity.FATAL:
            self._logger.error("[FATAL] %s", message, exc_info=exc)
            if self._on_fatal:
                self._on_fatal(message)
            return ErrorAction.STOP

        if severity is Severity.RECOVERABLE:
            self._logger.warning("[RECOVERABLE] %s", message)
            return ErrorAction.RETRY

        if severity is Severity.SKIP:
            self._logger.info("[SKIP] %s", message)
            return ErrorAction.SKIP

        if severity is Severity.WARN:
            self._logger.warning("[WARN] %s", message)
            if self._on_warn:
                self._on_warn(message)
            return ErrorAction.CONTINUE

        # 念のための fallback
        self._logger.error("未知の severity: %s (FATALとして扱う)", severity)
        if self._on_fatal:
            self._on_fatal(message)
        return ErrorAction.STOP
