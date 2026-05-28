"""ByteBoundedQueue: バイト単位で上限を持つ FIFO キュー。

役割: 「合計バイト数が上限を超えるまでは積み続け、超えたら先頭から押し出す」
方針のキュー。リアルタイム音声処理で、件数ではなく**サイズ**で容量を制御したい
ステージ間 PCM 受け渡しに使う(`PipelineCoordinator` の captured/synthesized
ステージのキュー)。

設計のポイント:
- `push_evicting(item)` は基本的にブロックしない。追加 → 超過分を先頭から退避。
- 運用上「設定値を少し超える」ことを前提にする(「いったん積んでから超過分を消す」方針)。
- `get(timeout)` / `get_nowait()` は `queue.Queue` と同じ例外/同期挙動。
- スレッドセーフ。複数の producer / consumer が安全に共存できる。
"""

from __future__ import annotations

import queue
import threading
import time
from collections import deque
from typing import Callable


class ByteBoundedQueue:
    """合計バイト数で上限管理する FIFO キュー。

    使用例:
        # PCM(numpy ndarray)を持つ PipelineMessage 用
        q = ByteBoundedQueue(max_bytes=500_000, size_of=lambda msg: msg.payload.pcm.nbytes)
        evicted = q.push_evicting(msg)  # 超過した古い item のリスト

    Notes:
        - `size_of` は item ごとのバイト数を返す関数(高速・例外を出さないこと)。
        - `max_bytes <= 0` は「常に超過 → 退避」になるが、最低 1 件は保持される
          (`push_evicting` は常に少なくとも今入れた要素は残す)。
    """

    def __init__(self, max_bytes: int, size_of: Callable[[object], int]) -> None:
        self._max_bytes = max_bytes
        self._size_of = size_of
        self._items: deque = deque()
        self._total_bytes = 0
        self._cond = threading.Condition()

    # ----------------------------------------------------------
    @property
    def max_bytes(self) -> int:
        return self._max_bytes

    def qsize(self) -> int:
        """現在の保持件数(参考値; ロック内で取らないので transient)。"""
        return len(self._items)

    def total_bytes(self) -> int:
        """現在の合計バイト数(参考値)。"""
        return self._total_bytes

    # ----------------------------------------------------------
    def push_evicting(self, item: object) -> list[object]:
        """末尾に追加し、合計バイト数が `max_bytes` を超えていれば古いものから退避する。

        新規 item は **必ず保持される**(2 件以上ある場合のみ古いものを捨てる)。
        戻り値は退避した item のリスト(古い順)。退避なしなら空リスト。
        """
        with self._cond:
            size = max(0, int(self._size_of(item)))
            self._items.append(item)
            self._total_bytes += size

            evicted: list[object] = []
            # 「設定値を少し超える」前提なので、超過しても今入れた要素は残す。
            # 2 件以上残っている範囲で、合計が max_bytes を超えるなら古いものを捨てる。
            while (
                self._total_bytes > self._max_bytes
                and len(self._items) > 1
            ):
                old = self._items.popleft()
                self._total_bytes -= max(0, int(self._size_of(old)))
                evicted.append(old)

            # 1件以上できたので待ち consumer を起こす
            self._cond.notify()
            return evicted

    # ----------------------------------------------------------
    def get(self, timeout: float | None = None) -> object:
        """先頭を取り出す。空なら `timeout` 秒だけ待ち、来なければ `queue.Empty`。

        timeout=None で無限待ち。`queue.Queue.get(timeout=...)` と同じ挙動。
        """
        deadline = None if timeout is None else time.monotonic() + timeout
        with self._cond:
            while not self._items:
                if deadline is None:
                    self._cond.wait()
                else:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise queue.Empty
                    self._cond.wait(remaining)
            item = self._items.popleft()
            self._total_bytes -= max(0, int(self._size_of(item)))
            return item

    def get_nowait(self) -> object:
        """空なら即 `queue.Empty`。それ以外は先頭を取り出す。"""
        with self._cond:
            if not self._items:
                raise queue.Empty
            item = self._items.popleft()
            self._total_bytes -= max(0, int(self._size_of(item)))
            return item

    # ----------------------------------------------------------
    def drain(self) -> None:
        """全要素を破棄する(start 直前に呼んで前回残骸をクリアする等の用途)。"""
        with self._cond:
            self._items.clear()
            self._total_bytes = 0
