"""UtteranceLedger の単体テスト(スレッドセーフ性含む)。"""

from __future__ import annotations

import threading
import time

from voice_translator.common.ledger import UtteranceLedger


class TestUtteranceLedgerBasic:
    def test_init_creates_empty_record(self) -> None:
        led = UtteranceLedger()
        led.init(1)
        assert 1 in led
        rec = led.peek(1)
        assert rec == {"timeline": {}}

    def test_init_existing_is_noop(self) -> None:
        led = UtteranceLedger()
        led.init(1)
        led.record(1, src_text="hi")
        led.init(1)  # 既存に対しては触らない
        rec = led.peek(1)
        assert rec.get("src_text") == "hi"

    def test_mark_time_records_stage(self) -> None:
        led = UtteranceLedger()
        led.init(1)
        t = led.mark_time(1, "t_asr")
        rec = led.peek(1)
        assert rec["timeline"]["t_asr"] == t

    def test_mark_time_on_unknown_seq_auto_inits(self) -> None:
        led = UtteranceLedger()
        # init せずに mark_time
        led.mark_time(99, "t_capture")
        assert 99 in led
        rec = led.peek(99)
        assert "t_capture" in rec["timeline"]

    def test_record_merges_fields(self) -> None:
        led = UtteranceLedger()
        led.init(1)
        led.record(1, src_text="hello", src_lang="en")
        led.record(1, tgt_text="こんにちは", tgt_lang="ja")
        rec = led.peek(1)
        assert rec["src_text"] == "hello"
        assert rec["src_lang"] == "en"
        assert rec["tgt_text"] == "こんにちは"
        assert rec["tgt_lang"] == "ja"

    def test_record_on_unknown_seq_auto_inits(self) -> None:
        led = UtteranceLedger()
        led.record(7, src_text="x")
        assert 7 in led

    def test_record_does_not_clobber_timeline(self) -> None:
        led = UtteranceLedger()
        led.mark_time(1, "t_asr")
        led.record(1, src_text="hi")  # timeline を含めずに渡す
        rec = led.peek(1)
        assert "t_asr" in rec["timeline"]
        assert rec["src_text"] == "hi"

    def test_record_with_timeline_merges(self) -> None:
        led = UtteranceLedger()
        led.mark_time(1, "t_asr")
        led.record(1, timeline={"t_translate": 123.0})
        rec = led.peek(1)
        assert "t_asr" in rec["timeline"]
        assert rec["timeline"]["t_translate"] == 123.0


class TestUtteranceLedgerPop:
    def test_pop_returns_full_record_and_removes(self) -> None:
        led = UtteranceLedger()
        led.init(1)
        led.record(1, src_text="hi")
        led.mark_time(1, "t_asr")
        rec = led.pop(1)
        assert rec["src_text"] == "hi"
        assert "t_asr" in rec["timeline"]
        assert 1 not in led

    def test_pop_unknown_returns_empty_dict(self) -> None:
        led = UtteranceLedger()
        assert led.pop(999) == {}  # KeyError しないこと

    def test_clear_removes_all(self) -> None:
        led = UtteranceLedger()
        led.init(1)
        led.init(2)
        led.init(3)
        assert len(led) == 3
        led.clear()
        assert len(led) == 0


class TestUtteranceLedgerPeekIsCopy:
    def test_peek_copy_does_not_affect_internal(self) -> None:
        led = UtteranceLedger()
        led.init(1)
        led.mark_time(1, "t_asr")
        snap = led.peek(1)
        snap["src_text"] = "mutated"
        snap["timeline"]["evil"] = 999.0
        # 元データは無傷
        real = led.peek(1)
        assert "src_text" not in real
        assert "evil" not in real["timeline"]

    def test_peek_unknown_returns_empty(self) -> None:
        led = UtteranceLedger()
        assert led.peek(123) == {}


class TestUtteranceLedgerThreadSafety:
    def test_concurrent_mark_no_data_loss(self) -> None:
        """複数スレッドから同じ ledger に mark_time/record しても破損しない。"""
        led = UtteranceLedger()
        n_threads = 8
        n_per_thread = 200
        barrier = threading.Barrier(n_threads)

        def worker(tid: int) -> None:
            barrier.wait()
            for i in range(n_per_thread):
                sid = tid * n_per_thread + i + 1
                led.init(sid)
                led.mark_time(sid, "t_asr")
                led.record(sid, src_text=f"t{tid}-{i}")
                led.mark_time(sid, "t_translate")

        threads = [threading.Thread(target=worker, args=(t,)) for t in range(n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # 全 seq_id が記録されており、各レコードが完備していること
        assert len(led) == n_threads * n_per_thread
        for tid in range(n_threads):
            for i in range(n_per_thread):
                sid = tid * n_per_thread + i + 1
                rec = led.pop(sid)
                assert rec["src_text"] == f"t{tid}-{i}"
                assert "t_asr" in rec["timeline"]
                assert "t_translate" in rec["timeline"]
        assert len(led) == 0

    def test_concurrent_pop_no_double_take(self) -> None:
        """同じ seq_id を複数スレッドで pop しても、どれか1つだけが中身を取れる。"""
        led = UtteranceLedger()
        for sid in range(1, 101):
            led.init(sid)
            led.record(sid, src_text=f"x{sid}")

        results: list[dict] = []
        results_lock = threading.Lock()

        def popper() -> None:
            for sid in range(1, 101):
                rec = led.pop(sid)
                if rec:  # 空でなければ自分が取った
                    with results_lock:
                        results.append(rec)

        threads = [threading.Thread(target=popper) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # 各 seq_id はちょうど 1 回だけ取得されている
        assert len(results) == 100
        assert len(led) == 0


class TestUtteranceLedgerTiming:
    def test_mark_time_returns_monotonic_now(self) -> None:
        led = UtteranceLedger()
        t1 = led.mark_time(1, "a")
        time.sleep(0.05)  # Windows time.sleep 最小解像度(~15.6ms)を上回る値
        t2 = led.mark_time(1, "b")
        assert t2 > t1
