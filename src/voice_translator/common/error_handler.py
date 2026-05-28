"""エラー振り分けハンドラ。

役割: パイプラインや GUI から渡された例外を `Severity` に基づき、
致命=停止 / 回復=リトライ判定 / スキップ=破棄 / 警告=通知 の4挙動に振り分ける。
UI 層への通知は callback として渡される。

R-Error-Context(2026-05-27): handle() に stage / seq_id の context を受け取り、
ログ・コールバックに「どこで・どの発話で」起きた例外かを含められるよう拡張。
"""

from __future__ import annotations

import logging
from typing import Callable, Protocol

from .errors import AppError, Severity
from .notification_throttle import NotificationThrottle


class FatalNotifier(Protocol):
    """致命エラー通知のコールバック型(GUIのダイアログ等を想定)。

    keyword 引数で context を受け取る。実装側は不要なら `**_kwargs` で吸収可。
    `suppressed` は前回通知から本通知までの間に集約・抑制された同種エラーの件数。
    """

    def __call__(
        self,
        message: str,
        *,
        exc: BaseException | None = None,
        stage: str | None = None,
        seq_id: int | None = None,
        suppressed: int = 0,
    ) -> None: ...


class WarnNotifier(Protocol):
    """警告通知のコールバック型(GUIのバナー等を想定)。"""

    def __call__(
        self,
        message: str,
        *,
        exc: BaseException | None = None,
        stage: str | None = None,
        seq_id: int | None = None,
        suppressed: int = 0,
    ) -> None: ...


class ErrorAction(str):
    """ErrorHandler.handle() の戻り値となるアクション識別子。"""

    STOP = "STOP"        # FATAL: パイプライン停止
    RETRY = "RETRY"      # RECOVERABLE: 呼び出し元でリトライ判断
    SKIP = "SKIP"        # SKIP: 当該発話破棄
    CONTINUE = "CONTINUE"  # WARN: 継続


# severity → ログレベル の対応表。SKIP は大量に出る想定で INFO に落とす。
_SEVERITY_TO_LEVEL: dict[Severity, int] = {
    Severity.FATAL: logging.ERROR,
    Severity.RECOVERABLE: logging.WARNING,
    Severity.SKIP: logging.INFO,
    Severity.WARN: logging.WARNING,
}


class ErrorHandler:
    """severity に基づいて挙動を決定するハンドラ。

    役割: `handle(exc, stage=..., seq_id=...)` を受けて、ログ出力 + UI通知 +
    後続アクション(STOP/RETRY/SKIP/CONTINUE)を返す。
    """

    def __init__(
        self,
        *,
        logger: logging.Logger | None = None,
        on_fatal: FatalNotifier | None = None,
        on_warn: WarnNotifier | None = None,
        throttle: NotificationThrottle | None = None,
    ) -> None:
        """`throttle` は省略可。指定すれば callback だけを集約・抑制する(ログは常に出る)。"""
        self._logger = logger or logging.getLogger("voice_translator")
        self._on_fatal = on_fatal
        self._on_warn = on_warn
        self._throttle = throttle

    def handle(
        self,
        exc: BaseException,
        *,
        stage: str | None = None,
        seq_id: int | None = None,
    ) -> str:
        """例外を分類しアクションを返す。

        - `stage`: 「ASR」「Translator」など、例外が起きた段。
        - `seq_id`: 発生時の発話シーケンス番号。Input/Capture 段で発行前ならNone。
        - AppError 以外は FATAL として扱う(包み忘れの安全側挙動)。
        - 戻り値は `ErrorAction.*` のいずれか。
        """
        # severity を決定
        if isinstance(exc, AppError):
            severity = exc.severity
        else:
            severity = Severity.FATAL  # 未分類は安全側で FATAL 扱い

        message = str(exc)
        formatted = self._format_context(
            stage=stage,
            seq_id=seq_id,
            severity=severity,
            message=message,
            exc=exc,
            unclassified=not isinstance(exc, AppError),
        )

        # ログ出力(severity ごとのレベルで)
        level = _SEVERITY_TO_LEVEL.get(severity, logging.ERROR)
        if severity is Severity.FATAL:
            # FATAL はスタックトレース付きで残す
            self._logger.log(level, formatted, exc_info=exc)
        else:
            self._logger.log(level, formatted)

        # コールバック発火と action の決定
        if severity is Severity.FATAL:
            self._notify(self._on_fatal, message, exc=exc, stage=stage, seq_id=seq_id)
            return ErrorAction.STOP
        if severity is Severity.RECOVERABLE:
            return ErrorAction.RETRY
        if severity is Severity.SKIP:
            return ErrorAction.SKIP
        if severity is Severity.WARN:
            self._notify(self._on_warn, message, exc=exc, stage=stage, seq_id=seq_id)
            return ErrorAction.CONTINUE

        # 念のための fallback
        self._logger.error("未知の severity: %s (FATALとして扱う)", severity)
        self._notify(self._on_fatal, message, exc=exc, stage=stage, seq_id=seq_id)
        return ErrorAction.STOP

    # ----------------------------------------------------------
    @staticmethod
    def _format_context(
        *,
        stage: str | None,
        seq_id: int | None,
        severity: Severity,
        message: str,
        exc: BaseException,
        unclassified: bool,
    ) -> str:
        """ログ用に「seq=N stage=X [SEVERITY] message (caused by ...)」形式に整形。"""
        parts: list[str] = []
        if seq_id is not None:
            parts.append(f"seq={seq_id}")
        if stage is not None:
            parts.append(f"stage={stage}")
        tag = severity.value if not unclassified else "FATAL/未分類"
        parts.append(f"[{tag}]")
        parts.append(message)

        # 例外チェーンの原因を補足(__cause__ があれば付与)
        cause = getattr(exc, "__cause__", None)
        if cause is not None and cause is not exc:
            parts.append(f"(caused by {type(cause).__name__}: {cause})")
        return " ".join(parts)

    def _notify(
        self,
        cb: Callable[..., None] | None,
        message: str,
        *,
        exc: BaseException,
        stage: str | None,
        seq_id: int | None,
    ) -> None:
        """コールバックを安全に呼ぶ。コールバック側の例外で停止させない。

        `throttle` が設定されている場合、(stage, 例外クラス名) をキーに集約・抑制する。
        抑制された呼び出しは静かに drop され、次の許可タイミングで suppressed カウントが渡される。
        """
        if cb is None:
            return

        suppressed = 0
        if self._throttle is not None:
            key = (stage or "_", type(exc).__name__)
            allow, suppressed = self._throttle.check(key)
            if not allow:
                return  # ログは _notify の外で済んでいる

        try:
            cb(message, exc=exc, stage=stage, seq_id=seq_id, suppressed=suppressed)
        except Exception:  # noqa: BLE001
            self._logger.exception("エラー通知コールバックで例外")
