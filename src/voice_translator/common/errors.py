"""アプリ共通の例外階層と severity 分類。

役割: 各バックエンドが下位例外を `AppError` に包んで送出し、
中央の `ErrorHandler` が severity に基づいて挙動を振り分けるための共通基盤。
詳細は docs/design/Class.md を参照。

backend 実装者向けの規約(R2-5):
- **下位例外を黙って FatalError に包むだけ、は避ける**。HTTP コード / 例外型を見て、
  適切な severity (RECOVERABLE / FATAL / SKIP / WARN) に分けて包むこと。
- 例:
  - HTTP 5xx / `ConnectionError` / `TimeoutError` → `RecoverableError`(リトライで通る見込み)
  - HTTP 401/403 / 認証失敗 → `FatalError`(ユーザ操作なしには通らない)
  - HTTP 4xx の入力不正 → `SkipError`(当該発話だけ破棄して継続)
  - 廃止予定 / 警告だけ出して動作継続 → `WarnError`
- 既存のローカル backend(faster-whisper / silero / NLLB / SAPI / soundcard)はモデル/デバイス
  起因の障害が主で、現状ほぼ `FatalError` で妥当。クラウド backend を追加する際に上記方針を
  適用すること。

backend は `BackendBase.record_error(exc, context=...)` を呼んで履歴に積むのも併用する
(GUI のステータステキストボックスに集約表示するため。Phase C/E)。
"""

from __future__ import annotations

from enum import Enum


class Severity(str, Enum):
    """エラーの重大度。`ErrorHandler` の挙動振り分けに使う。"""

    FATAL = "FATAL"              # 復旧不可。停止して再起動を促す。
    RECOVERABLE = "RECOVERABLE"  # リトライで回復しうる。
    SKIP = "SKIP"                # 当該発話を破棄して継続。
    WARN = "WARN"                # 動作継続、通知だけ出す。


class AppError(Exception):
    """アプリ内例外の基底。

    役割: 内部例外を severity 付きで包んで送出する。
    """

    def __init__(self, message: str, severity: Severity, *, cause: BaseException | None = None) -> None:
        super().__init__(message)
        self.severity = severity
        self.__cause__ = cause


class FatalError(AppError):
    """致命的: モデルロード失敗、デバイス消失、設定破損など。"""

    def __init__(self, message: str, *, cause: BaseException | None = None) -> None:
        super().__init__(message, Severity.FATAL, cause=cause)


class RecoverableError(AppError):
    """回復可能: 一時的失敗。リトライで通る見込みがあるもの。"""

    def __init__(self, message: str, *, cause: BaseException | None = None) -> None:
        super().__init__(message, Severity.RECOVERABLE, cause=cause)


class SkipError(AppError):
    """スキップ可能: 当該発話だけ破棄して継続する。"""

    def __init__(self, message: str, *, cause: BaseException | None = None) -> None:
        super().__init__(message, Severity.SKIP, cause=cause)


class WarnError(AppError):
    """警告: 動作は継続。通知バナーやログ蓄積のみ。"""

    def __init__(self, message: str, *, cause: BaseException | None = None) -> None:
        super().__init__(message, Severity.WARN, cause=cause)
