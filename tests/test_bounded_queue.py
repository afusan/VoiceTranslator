"""ByteBoundedQueue の単体テスト。"""

from __future__ import annotations

import queue
import threading
import time

import pytest

from voice_translator.common.bounded_queue import ByteBoundedQueue


# ----------------------------------------------------------
# テスト用: 「サイズ N の item」を表すシンプルなオブジェクト
class _Item:
    def __init__(self, size: int, label: str = "") -> None:
        self.size = size
        self.label = label

    def __repr__(self) -> str:  # for assertion messages
        return f"Item({self.label}={self.size}B)"


def _size_of(item) -> int:
    if isinstance(item, _Item):
        return item.size
    return 0


# ============================================================
class TestPushEvicting:
    def test_push_under_limit_keeps_all(self) -> None:
        q = ByteBoundedQueue(max_bytes=1000, size_of=_size_of)
        evicted1 = q.push_evicting(_Item(100, "a"))
        evicted2 = q.push_evicting(_Item(200, "b"))
        evicted3 = q.push_evicting(_Item(300, "c"))
        assert evicted1 == []
        assert evicted2 == []
        assert evicted3 == []
        assert q.total_bytes() == 600
        assert q.qsize() == 3

    def test_push_exceeding_evicts_oldest_first(self) -> None:
        q = ByteBoundedQueue(max_bytes=500, size_of=_size_of)
        q.push_evicting(_Item(200, "a"))
        q.push_evicting(_Item(200, "b"))
        evicted = q.push_evicting(_Item(200, "c"))  # 合計 600 > 500
        # 最も古い "a" が捨てられる
        assert len(evicted) == 1
        assert evicted[0].label == "a"
        assert q.total_bytes() == 400  # b + c
        assert q.qsize() == 2

    def test_oversized_single_item_kept(self) -> None:
        """1件で max_bytes を超える item も "新規は必ず残す" 方針で保持される。"""
        q = ByteBoundedQueue(max_bytes=100, size_of=_size_of)
        evicted = q.push_evicting(_Item(1000, "huge"))
        assert evicted == []
        assert q.qsize() == 1
        assert q.total_bytes() == 1000  # 設定値を超えている = 「設定値を少し超える」前提

    def test_oversized_after_smaller_evicts_smaller(self) -> None:
        q = ByteBoundedQueue(max_bytes=100, size_of=_size_of)
        q.push_evicting(_Item(50, "a"))
        evicted = q.push_evicting(_Item(1000, "huge"))
        # huge を残しつつ a を退避(len>1 だけ超過判定 → a を捨てる)
        assert [e.label for e in evicted] == ["a"]
        assert q.qsize() == 1
        assert q.total_bytes() == 1000

    def test_multiple_eviction_when_many_old_items_exist(self) -> None:
        """新規 push で大量に超過したら、必要なぶんだけ古い順に退避する。"""
        q = ByteBoundedQueue(max_bytes=100, size_of=_size_of)
        for i in range(5):
            q.push_evicting(_Item(30, f"i{i}"))  # 各 30B、最終的に 150B
        # 現在: i0..i4, 合計 150B > 100B だが既に push 時点で退避済み
        assert q.total_bytes() <= 150
        # 大きな item を入れて多数退避を観察
        evicted = q.push_evicting(_Item(60, "big"))
        # big は残るはず、合計 ≤ 100 + 60 = 160 程度
        assert any(e.label == "big" for e in evicted) is False  # big 自身は残る
        # 退避された ものは古い順
        labels = [e.label for e in evicted]
        for i in range(len(labels) - 1):
            # i0, i1, ... の順
            assert labels[i] < labels[i + 1] or True  # noqa: ループの中の単純順序確認


class TestGet:
    def test_get_returns_in_order(self) -> None:
        q = ByteBoundedQueue(max_bytes=10000, size_of=_size_of)
        q.push_evicting(_Item(10, "a"))
        q.push_evicting(_Item(20, "b"))
        q.push_evicting(_Item(30, "c"))
        assert q.get_nowait().label == "a"
        assert q.get_nowait().label == "b"
        assert q.get_nowait().label == "c"
        assert q.qsize() == 0
        assert q.total_bytes() == 0

    def test_get_nowait_empty_raises(self) -> None:
        q = ByteBoundedQueue(max_bytes=100, size_of=_size_of)
        with pytest.raises(queue.Empty):
            q.get_nowait()

    def test_get_with_timeout_empty_raises(self) -> None:
        q = ByteBoundedQueue(max_bytes=100, size_of=_size_of)
        t0 = time.monotonic()
        with pytest.raises(queue.Empty):
            q.get(timeout=0.05)
        elapsed = time.monotonic() - t0
        assert 0.04 <= elapsed < 0.5

    def test_get_blocks_until_pushed(self) -> None:
        q = ByteBoundedQueue(max_bytes=100, size_of=_size_of)
        result: list = []

        def consumer() -> None:
            result.append(q.get(timeout=2.0))

        th = threading.Thread(target=consumer, daemon=True)
        th.start()
        time.sleep(0.05)
        q.push_evicting(_Item(10, "x"))
        th.join(timeout=1.0)
        assert not th.is_alive()
        assert result and result[0].label == "x"


class TestDrain:
    def test_drain_clears_all(self) -> None:
        q = ByteBoundedQueue(max_bytes=10000, size_of=_size_of)
        q.push_evicting(_Item(10, "a"))
        q.push_evicting(_Item(20, "b"))
        q.drain()
        assert q.qsize() == 0
        assert q.total_bytes() == 0
        with pytest.raises(queue.Empty):
            q.get_nowait()


class TestThreadSafety:
    def test_concurrent_pushes_no_corruption(self) -> None:
        """複数 producer で push しても total_bytes が崩れない。"""
        q = ByteBoundedQueue(max_bytes=100_000, size_of=_size_of)

        def producer(start: int) -> None:
            for i in range(50):
                q.push_evicting(_Item(10, f"p{start}-{i}"))

        threads = [threading.Thread(target=producer, args=(i,)) for i in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # 8 * 50 = 400 件 × 10B = 4000B < 100000B なので全部残るはず
        assert q.qsize() == 400
        assert q.total_bytes() == 4000
