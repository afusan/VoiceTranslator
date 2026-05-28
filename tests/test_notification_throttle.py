"""NotificationThrottle の単体テスト。"""

from __future__ import annotations

import threading
import time

from voice_translator.common.notification_throttle import NotificationThrottle


class TestBasic:
    def test_first_check_allows(self) -> None:
        th = NotificationThrottle(window_sec=5.0)
        allow, suppressed = th.check(("ASR", "FatalError"))
        assert allow is True
        assert suppressed == 0

    def test_second_within_window_suppressed(self) -> None:
        th = NotificationThrottle(window_sec=5.0)
        th.check(("ASR", "FatalError"))
        allow, suppressed = th.check(("ASR", "FatalError"))
        assert allow is False
        assert suppressed == 0  # suppressed は allow=True のときだけ意味あり

    def test_after_window_allows_with_count(self) -> None:
        th = NotificationThrottle(window_sec=0.1)  # 100ms 窓
        th.check(("ASR", "FatalError"))         # 1回目 allow
        th.check(("ASR", "FatalError"))         # 抑制
        th.check(("ASR", "FatalError"))         # 抑制
        th.check(("ASR", "FatalError"))         # 抑制
        assert th.pending_suppressed(("ASR", "FatalError")) == 3
        time.sleep(0.15)
        allow, suppressed = th.check(("ASR", "FatalError"))
        assert allow is True
        assert suppressed == 3
        # 回収後はリセット
        assert th.pending_suppressed(("ASR", "FatalError")) == 0

    def test_different_keys_independent(self) -> None:
        th = NotificationThrottle(window_sec=5.0)
        a1, _ = th.check(("ASR", "FatalError"))
        b1, _ = th.check(("Translator", "FatalError"))
        # 別キーはそれぞれ独立に通る
        assert a1 is True and b1 is True
        # 同キー再度は抑制
        a2, _ = th.check(("ASR", "FatalError"))
        b2, _ = th.check(("Translator", "FatalError"))
        assert a2 is False and b2 is False


class TestDisabled:
    def test_zero_window_disables(self) -> None:
        th = NotificationThrottle(window_sec=0)
        assert th.disabled is True
        # 何度呼んでも全部 allow
        for _ in range(10):
            allow, suppressed = th.check(("ASR", "FatalError"))
            assert allow is True
            assert suppressed == 0

    def test_negative_window_disables(self) -> None:
        th = NotificationThrottle(window_sec=-1.0)
        assert th.disabled is True
        allow, _ = th.check(("X", "Y"))
        assert allow is True


class TestReset:
    def test_reset_clears_state(self) -> None:
        th = NotificationThrottle(window_sec=5.0)
        th.check(("ASR", "FatalError"))
        th.check(("ASR", "FatalError"))
        assert th.pending_suppressed(("ASR", "FatalError")) == 1
        th.reset()
        assert th.pending_suppressed(("ASR", "FatalError")) == 0
        # reset 後は初回扱いで allow
        allow, suppressed = th.check(("ASR", "FatalError"))
        assert allow is True
        assert suppressed == 0


class TestThreadSafety:
    def test_concurrent_checks_no_corruption(self) -> None:
        """複数スレッドで同キーを叩いても、allow=True がちょうど1回だけ得られる(短窓)。"""
        th = NotificationThrottle(window_sec=10.0)  # 長い窓で「1度しか通らない」を保証
        n_threads = 16
        n_per = 100
        allow_count_per_thread: list[int] = [0] * n_threads
        barrier = threading.Barrier(n_threads)

        def worker(tid: int) -> None:
            barrier.wait()
            local = 0
            for _ in range(n_per):
                allow, _ = th.check(("ASR", "FatalError"))
                if allow:
                    local += 1
            allow_count_per_thread[tid] = local

        threads = [threading.Thread(target=worker, args=(t,)) for t in range(n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # 全スレッド合計で allow=True は **ちょうど 1 回**(窓が広いので最初の1人しか通らない)
        total_allows = sum(allow_count_per_thread)
        assert total_allows == 1, f"窓内で複数 allow が発生: {total_allows}"
        # 残りは全部抑制カウントに乗っている
        assert th.pending_suppressed(("ASR", "FatalError")) == n_threads * n_per - 1
