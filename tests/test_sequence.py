"""SequenceGenerator の単体テスト(スレッドセーフ性含む)。"""

from __future__ import annotations

import threading

from voice_translator.common.sequence import SequenceGenerator


class TestSequenceGeneratorBasic:
    def test_starts_at_one_by_default(self) -> None:
        gen = SequenceGenerator()
        assert gen.next() == 1

    def test_monotonic_increase(self) -> None:
        gen = SequenceGenerator()
        seq = [gen.next() for _ in range(5)]
        assert seq == [1, 2, 3, 4, 5]

    def test_custom_start(self) -> None:
        gen = SequenceGenerator(start=100)
        assert gen.next() == 101
        assert gen.next() == 102

    def test_current_returns_last_issued(self) -> None:
        gen = SequenceGenerator()
        assert gen.current() == 0  # 発行前
        gen.next()
        gen.next()
        assert gen.current() == 2

    def test_reset(self) -> None:
        gen = SequenceGenerator()
        gen.next()
        gen.next()
        gen.reset()
        assert gen.next() == 1

    def test_reset_to_value(self) -> None:
        gen = SequenceGenerator()
        gen.next()
        gen.reset(start=500)
        assert gen.next() == 501


class TestSequenceGeneratorThreadSafety:
    def test_concurrent_next_no_duplicates(self) -> None:
        """並行 next() でも重複なし・抜けなし。"""
        gen = SequenceGenerator()
        n_threads = 8
        n_per_thread = 1000
        results: list[int] = []
        results_lock = threading.Lock()
        barrier = threading.Barrier(n_threads)

        def worker() -> None:
            barrier.wait()
            local = [gen.next() for _ in range(n_per_thread)]
            with results_lock:
                results.extend(local)

        threads = [threading.Thread(target=worker) for _ in range(n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(results) == n_threads * n_per_thread
        # 重複なし
        assert len(set(results)) == len(results)
        # 1 から n_threads*n_per_thread までが完備
        assert min(results) == 1
        assert max(results) == n_threads * n_per_thread
