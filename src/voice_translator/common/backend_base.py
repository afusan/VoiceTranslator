"""BackendBase: 全 backend 共通の基底ミックスイン(状態 / エラー履歴 / notify 機構)。

役割: 各レイヤの抽象基底(AsrBackend, VadBackend, ...)が継承し、
`ModelStatus` 自己管理・状態変化通知・エラー履歴の保持を提供する。

設計判断(R2-1 解消方針):
- 状態の真実は **backend 側にある**。`AppController` は購読者として動く。
- かつて `AppController._model_status` dict にあった「レイヤ別状態」を分散化し、
  backend が自分の状態を保有することで責務肥大化を抑える。

Subscription パターン(R2-6):
- `subscribe(callback)` は `Subscription` トークンを返す。
- リスナの解除は `Subscription.unsubscribe()` を明示呼び出し(LayerSettingsDialog の `_dismiss` 等で使う)。
- 内部は弱参照に頼らず token 辞書で管理(死んだ widget へのコールバックは listener 側で握りつぶす)。
"""

from __future__ import annotations

import threading
from collections import deque
from time import time
from typing import Callable

from .types import ErrorRecord, ModelInfo, ModelStatus

# 状態変化の購読コールバック型。引数は遷移後の新ステータス。
StatusListener = Callable[[ModelStatus], None]

# エラー履歴のリングバッファ長(暫定)。Phase C のステータステキストボックスで集約表示する想定。
_ERROR_LOG_MAXLEN = 5


class Subscription:
    """状態変化購読の解除トークン。

    役割: `BackendBase.subscribe()` の戻り値。ダイアログの dismiss 等で明示的に
    `unsubscribe()` してリーク・死んだ widget 参照を防ぐ(R2-6)。
    """

    def __init__(self, owner: "BackendBase", token: int) -> None:
        # 所有者への循環参照を避けるため、解除時に直接 dict 操作するための弱い結合は持たないが、
        # Subscription を捨てれば listener も孤立して回収されるので、明示 unsubscribe を推奨。
        self._owner = owner
        self._token = token
        self._active = True

    def unsubscribe(self) -> None:
        """購読を解除する。複数回呼ばれても安全(2 回目以降は no-op)。"""
        if not self._active:
            return
        self._owner._remove_listener(self._token)
        self._active = False
        # owner 参照を切って循環参照の温床を残さない
        self._owner = None  # type: ignore[assignment]

    @property
    def is_active(self) -> bool:
        return self._active


class BackendBase:
    """全 backend 共通の基底ミックスイン。

    役割: 各レイヤの ABC が多重継承する。状態の自己保持 / 通知 / エラー履歴 /
    推奨モデル一覧の既定実装を提供する。

    サブクラスは `__init__` 内で必ず `super().__init__()` を呼ぶこと
    (初期化を忘れると `get_status()` などが AttributeError になる)。
    """

    def __init__(self) -> None:
        self._status: ModelStatus = ModelStatus.INIT
        self._listeners: dict[int, StatusListener] = {}
        self._next_token: int = 0
        self._listeners_lock = threading.Lock()
        self._error_log: deque[ErrorRecord] = deque(maxlen=_ERROR_LOG_MAXLEN)

    # ============================================================
    # ステータス
    # ============================================================
    def get_status(self) -> ModelStatus:
        """現在の `ModelStatus` を返す。"""
        return self._status

    def _set_status(self, status: ModelStatus) -> None:
        """状態を更新し、購読者へ通知する。サブクラス内部のみで使う想定。

        同じ状態への遷移はスキップする(notify も発火しない)。
        listener の例外は他 listener / 本体への影響を遮断する(ここでは黙殺、ログは購読側責任)。
        """
        if status == self._status:
            return
        self._status = status
        # listener 呼び出しはロック外で(再入リスク回避 + listener が長時間ブロックしても安全)
        with self._listeners_lock:
            listeners = list(self._listeners.values())
        for cb in listeners:
            try:
                cb(status)
            except Exception:  # noqa: BLE001 - listener の落下は本体を止めない
                pass

    # ============================================================
    # 購読
    # ============================================================
    def subscribe(self, callback: StatusListener) -> Subscription:
        """状態変化を購読し、解除用 `Subscription` トークンを返す。

        callback は `(ModelStatus,) -> None`。状態変化のたびに最新ステータスで呼ばれる。
        購読時点の現状態は callback されない(必要なら呼び出し側で `get_status()` を読む)。
        """
        with self._listeners_lock:
            token = self._next_token
            self._next_token += 1
            self._listeners[token] = callback
        return Subscription(self, token)

    def _remove_listener(self, token: int) -> None:
        with self._listeners_lock:
            self._listeners.pop(token, None)

    # ============================================================
    # エラー履歴
    # ============================================================
    def record_error(self, exc: BaseException, *, context: str | None = None) -> None:
        """エラーを履歴(リングバッファ)に追加する。

        Phase C/E でステータステキストボックスに集約表示するための入口。
        backend 実装は復帰不能 / 復帰可能のいずれの局面でも積んでよい。
        """
        self._error_log.append(
            ErrorRecord(
                timestamp=time(),
                message=str(exc),
                exc_type=type(exc).__name__,
                context=context,
            )
        )

    def get_recent_errors(self) -> list[ErrorRecord]:
        """直近のエラー履歴(古い→新しい順、最大 `_ERROR_LOG_MAXLEN` 件)。"""
        return list(self._error_log)

    # ============================================================
    # モデル一覧
    # ============================================================
    def list_recommended_models(self) -> list[ModelInfo]:
        """推奨モデル一覧を返す。モデル概念のない backend は空リスト。

        サブクラスでオーバーライドして固定リストを返すのが基本(faster-whisper の各サイズ、
        NLLB-200 の各サイズ等)。GUI のドロップダウン項目 + リソース目安表示で参照される。
        """
        return []
