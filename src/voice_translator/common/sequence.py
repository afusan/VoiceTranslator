"""SequenceGenerator: 発話シーケンス番号の発行器。

役割: 各発話に一意な連番 (seq_id) を発行する。各レイヤのログ(app.log /
soundsrc.txt / translated.txt / jsonl)に seq_id を載せて、後から対応を取れるようにする。

スレッドセーフ: 内部カウンタを Lock で保護し、複数スレッドから next() しても重複しない。
"""

from __future__ import annotations

import threading


class SequenceGenerator:
    """単調増加する整数連番の発行器。

    使い方:
        seq = SequenceGenerator()
        sid = seq.next()  # 1, 2, 3, ...
    """

    def __init__(self, start: int = 0) -> None:
        """start を初期値(発行前の値)としてセット。

        最初の next() は start + 1 を返す。デフォルトでは 1 から始まる。
        """
        self._lock = threading.Lock()
        self._counter = int(start)

    def next(self) -> int:
        """次の連番を返す(原子的に +1)。"""
        with self._lock:
            self._counter += 1
            return self._counter

    def current(self) -> int:
        """最後に発行した値を返す(発行前は start)。"""
        with self._lock:
            return self._counter

    def reset(self, start: int = 0) -> None:
        """カウンタを初期化。再 start 時の drain 用。"""
        with self._lock:
            self._counter = int(start)
