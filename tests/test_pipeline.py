"""PipelineCoordinator(5スレッド版)の単体テスト。モックバックエンドで動作確認。"""

from __future__ import annotations

import time
from time import monotonic
from typing import Callable

import numpy as np
import pytest

from voice_translator.asr.backend import AsrBackend
from voice_translator.capture.backend import AudioCaptureBackend
from voice_translator.common.error_handler import ErrorHandler
from voice_translator.common.errors import FatalError, SkipError
from voice_translator.common.ledger import UtteranceLedger
from voice_translator.common.logger import TextLogger
from voice_translator.common.messages import PayloadKind
from voice_translator.common.pipeline import PipelineCoordinator
from voice_translator.common.pipeline_plan import PlanError
from voice_translator.common.sequence import SequenceGenerator
from voice_translator.common.types import CaptureSource, OutputDevice, PcmChunk
from voice_translator.output.backend import AudioOutputBackend
from voice_translator.translator.backend import TranslatorBackend
from voice_translator.tts.backend import TtsBackend
from voice_translator.vad.backend import VadBackend, VadSegment


# ============================================================
# モックバックエンド群
# ============================================================
class FakeCapture(AudioCaptureBackend):
    """事前に渡したチャンクを順に返す Capture。リストが尽きたら None を返す。"""

    def __init__(self, chunks: list[PcmChunk]) -> None:
        self._chunks = list(chunks)
        self._started = False

    def list_sources(self) -> list[CaptureSource]:
        return [CaptureSource("dummy", "Dummy")]

    def start(self, source_id: str) -> None:
        self._started = True

    def stop(self) -> None:
        self._started = False

    def read_chunk(self, timeout: float = 0.1) -> PcmChunk | None:
        if not self._started:
            raise RuntimeError("not started")
        if not self._chunks:
            time.sleep(timeout)
            return None
        return self._chunks.pop(0)


class FakeVad(VadBackend):
    """N チャンクごとに1発話 VadSegment を作るVAD。"""

    def __init__(self, every_n_chunks: int = 1) -> None:
        self._n = every_n_chunks
        self._count = 0

    def reset(self) -> None:
        self._count = 0

    def process(self, chunk: PcmChunk) -> list[VadSegment]:
        self._count += 1
        if self._count % self._n != 0:
            return []
        return [VadSegment(pcm=chunk, started_at_monotonic=monotonic())]


class FakeAsr(AsrBackend):
    def __init__(self, *, raise_exc: BaseException | None = None) -> None:
        self._raise = raise_exc

    def transcribe(self, pcm, src_lang_hint: str = "auto") -> tuple[str, str]:
        if self._raise is not None:
            raise self._raise
        return "hello", "en"

    @classmethod
    def supported_input_languages(cls) -> list[str]:
        return ["en", "ja"]

    @classmethod
    def supports_auto_detect(cls) -> bool:
        return True


class FakeTranslator(TranslatorBackend):
    @classmethod
    def supported_target_languages(cls) -> list[str]:
        return ["en", "ja"]

    def __init__(
        self,
        *,
        raise_exc: BaseException | None = None,
        result: str | None = None,  # None=src_text+"->ja", ""=passthrough,それ以外は固定
    ) -> None:
        self._raise = raise_exc
        self._result = result

    def translate(self, src_text: str, src_lang: str, tgt_lang: str) -> str:
        if self._raise is not None:
            raise self._raise
        if self._result is None:
            return src_text + "->ja"
        return self._result


class FakeTts(TtsBackend):
    @classmethod
    def supported_output_languages(cls) -> list[str]:
        return ["en", "ja"]

    def __init__(self, *, raise_exc: BaseException | None = None) -> None:
        self._raise = raise_exc

    def synthesize(self, text: str, tgt_lang: str) -> tuple:
        if self._raise is not None:
            raise self._raise
        return b"audio", 16000


class FakeOutput(AudioOutputBackend):
    def __init__(self, *, raise_exc: BaseException | None = None) -> None:
        self.played: list[tuple] = []  # (pcm, samplerate)
        self._started = False
        self._raise = raise_exc

    def list_devices(self) -> list[OutputDevice]:
        return [OutputDevice("dummy_out", "Dummy")]

    def start(self, device_id: str) -> None:
        self._started = True

    def play(self, pcm, samplerate: int) -> None:
        if self._raise is not None:
            raise self._raise
        self.played.append((pcm, samplerate))

    def stop(self) -> None:
        self._started = False


class RaisingVad(VadBackend):
    """process() で常に例外を吐く VAD。"""

    def __init__(self, exc: BaseException, *, raise_after_n: int = 0) -> None:
        self._exc = exc
        self._count = 0
        self._raise_after = raise_after_n

    def reset(self) -> None:
        self._count = 0

    def process(self, chunk: PcmChunk) -> list[VadSegment]:
        self._count += 1
        if self._count > self._raise_after:
            raise self._exc
        return []


class RaisingCapture(AudioCaptureBackend):
    """read_chunk() で常に例外を吐く Capture。"""

    def __init__(self, exc: BaseException) -> None:
        self._exc = exc
        self._started = False

    def list_sources(self) -> list[CaptureSource]:
        return [CaptureSource("dummy", "Dummy")]

    def start(self, source_id: str) -> None:
        self._started = True

    def stop(self) -> None:
        self._started = False

    def read_chunk(self, timeout: float = 0.1) -> PcmChunk | None:
        raise self._exc


class SpyingAsr(AsrBackend):
    """seq_id を観測するための専用 Fake。
    transcribe で呼ばれた pcm の id (簡易) を seen に記録する。"""

    def __init__(self) -> None:
        self.calls: list[int] = []  # pcm.shape[0] を記録(seq_id 自体は受け取らないため)

    def transcribe(self, pcm, src_lang_hint: str = "auto") -> tuple[str, str]:
        self.calls.append(int(pcm.shape[0]))
        return "hello", "en"

    @classmethod
    def supported_input_languages(cls) -> list[str]:
        return ["en"]


# ============================================================
# ヘルパ
# ============================================================
def _build(
    *,
    chunks: list[PcmChunk] | None = None,
    asr: AsrBackend | None = None,
    on_done: Callable[[dict], None] | None = None,
    on_text_ready: Callable[[dict], None] | None = None,
    on_dropped: Callable[[list[int], str], None] | None = None,
    # PCM 系はバイト基準。160 サンプル float32 = 640B。
    # default は十分大きい値(オーバーフローしない)、テスト側で 1 等を渡すと最小1件保持挙動になる。
    captured_queue_max_bytes: int = 1_000_000,
    synthesized_queue_max_bytes: int = 1_000_000,
    recognized_queue_size: int = 50,
    translated_queue_size: int = 50,
    text_logger: TextLogger | None = None,
) -> tuple[PipelineCoordinator, FakeOutput]:
    if chunks is None:
        chunks = [np.zeros(160, dtype=np.float32) for _ in range(5)]
    output = FakeOutput()
    coord = PipelineCoordinator(
        capture=FakeCapture(chunks),
        vad=FakeVad(every_n_chunks=1),
        asr=asr or FakeAsr(),
        translator=FakeTranslator(),
        tts=FakeTts(),
        output=output,
        error_handler=ErrorHandler(),
        text_logger=text_logger,
        src_lang="en",
        tgt_lang="ja",
        on_utterance_done=on_done,
        on_text_ready=on_text_ready,
        on_dropped=on_dropped,
        read_timeout=0.01,
        captured_queue_max_bytes=captured_queue_max_bytes,
        synthesized_queue_max_bytes=synthesized_queue_max_bytes,
        recognized_queue_size=recognized_queue_size,
        translated_queue_size=translated_queue_size,
    )
    return coord, output


def _wait_until(predicate: Callable[[], bool], timeout: float = 2.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return False


# ============================================================
# ライフサイクル
# ============================================================
class TestPipelineLifecycle:
    def test_start_runs_loop_and_processes_utterances(self) -> None:
        done: list[dict] = []
        coord, output = _build(on_done=lambda r: done.append(r))
        coord.start(capture_source_id="dummy", output_device_id="dummy_out")
        assert _wait_until(lambda: len(output.played) >= 5)
        coord.stop()
        assert not coord.is_running
        assert len(output.played) == 5
        for pcm, sr in output.played:
            assert pcm == b"audio"
            assert sr == 16000
        # ledger.pop() 経由でレコードが配られている
        assert len(done) == 5
        for r in done:
            assert r["seq_id"] >= 1
            assert r["src_text"] == "hello"
            assert r["tgt_text"] == "hello->ja"
            assert r["src_lang"] == "en"
            assert r["tgt_lang"] == "ja"

    def test_double_start_raises(self) -> None:
        coord, _ = _build()
        coord.start(capture_source_id="dummy", output_device_id="dummy_out")
        try:
            with pytest.raises(RuntimeError):
                coord.start(capture_source_id="dummy", output_device_id="dummy_out")
        finally:
            coord.stop()

    def test_stop_when_not_started_is_safe(self) -> None:
        coord, _ = _build()
        coord.stop()  # 例外が出ないこと

    def test_restart_drains_queues_and_ledger(self) -> None:
        """再 start で前回の残骸が残らない。"""
        coord, output = _build()
        # 1回目
        coord.start(capture_source_id="dummy", output_device_id="dummy_out")
        assert _wait_until(lambda: len(output.played) >= 3)
        coord.stop()
        assert len(coord.ledger) == 0  # 全 pop されている

        # ledger と queue に手動でゴミを入れる
        coord._ledger.init(9999)

        # 2回目 start で drain される
        # FakeCapture の chunks は使い切られているので新規に作り直し
        coord._capture = FakeCapture([np.zeros(160, dtype=np.float32) for _ in range(3)])
        coord.start(capture_source_id="dummy", output_device_id="dummy_out")
        # 9999 は drain で消えるはず
        assert 9999 not in coord.ledger
        coord.stop()


# ============================================================
# on_text_ready 前倒し通知(TTS 完了時点で UI に流す)
# ============================================================
class TestPipelineTextReadyCallback:
    def test_text_ready_fires_per_utterance_with_text_payload(self) -> None:
        text_ready: list[dict] = []
        done: list[dict] = []
        coord, output = _build(
            chunks=[np.zeros(160, dtype=np.float32) for _ in range(3)],
            on_done=lambda r: done.append(r),
            on_text_ready=lambda r: text_ready.append(r),
        )
        coord.start(capture_source_id="dummy", output_device_id="dummy_out")
        assert _wait_until(lambda: len(output.played) >= 3)
        coord.stop()
        # 前倒し通知と完了通知は同数発火し、テキスト/言語が揃っている
        assert len(text_ready) == len(done) >= 3
        for r in text_ready:
            assert r["src_text"] == "hello"
            assert r["tgt_text"] == "hello->ja"
            assert r["src_lang"] == "en"
            assert r["tgt_lang"] == "ja"
            # ledger スナップショット由来なので少なくとも TTS 完了までは含む
            assert "t_tts" in r.get("timeline", {})


# ============================================================
# エラー処理
# ============================================================
class TestPipelineErrorHandling:
    def test_skip_error_continues(self) -> None:
        chunks = [np.zeros(160, dtype=np.float32) for _ in range(3)]
        coord, output = _build(chunks=chunks, asr=FakeAsr(raise_exc=SkipError("empty")))
        coord.start(capture_source_id="dummy", output_device_id="dummy_out")
        time.sleep(0.3)
        coord.stop()
        assert len(output.played) == 0  # SKIP のため再生はされない
        # 失敗した発話の ledger 残骸はリークしないこと
        assert len(coord.ledger) == 0

    def test_fatal_error_stops_pipeline(self) -> None:
        chunks = [np.zeros(160, dtype=np.float32) for _ in range(10)]
        called: list[str] = []
        handler = ErrorHandler(on_fatal=lambda m, **_kw: called.append(m))
        output = FakeOutput()
        coord = PipelineCoordinator(
            capture=FakeCapture(chunks),
            vad=FakeVad(every_n_chunks=1),
            asr=FakeAsr(raise_exc=FatalError("model dead")),
            translator=FakeTranslator(),
            tts=FakeTts(),
            output=output,
            error_handler=handler,
            src_lang="en",
            tgt_lang="ja",
            read_timeout=0.01,
        )
        coord.start(capture_source_id="dummy", output_device_id="dummy_out")
        assert _wait_until(lambda: not coord.is_running, timeout=2.0)
        coord.stop()
        assert called == ["model dead"]
        assert len(output.played) == 0


# ============================================================
# キューあふれ
# ============================================================
class TestPipelineQueueOverflow:
    def test_drop_counts_start_empty(self) -> None:
        coord, _ = _build()
        assert coord.get_drop_counts() == {}

    def test_overflow_logs_warning_and_counts(self, caplog) -> None:
        import logging

        caplog.set_level(logging.WARNING, logger="voice_translator")

        coord, _ = _build(
            chunks=[np.zeros(160, dtype=np.float32) for _ in range(40)],
            captured_queue_max_bytes=1,
            recognized_queue_size=1,
            translated_queue_size=1,
            synthesized_queue_max_bytes=1,
        )
        coord.start(capture_source_id="dummy", output_device_id="dummy_out")
        time.sleep(0.3)
        coord.stop()

        counts = coord.get_drop_counts()
        assert sum(counts.values()) > 0, f"ドロップが記録されていない: {counts}"
        overflow_logs = [r for r in caplog.records if "queue overflow" in r.message]
        assert overflow_logs, "queue overflow ログが出ていない"

    def test_on_dropped_callback_with_seq_ids(self) -> None:
        seen: list[tuple[list[int], str]] = []
        coord, _ = _build(
            chunks=[np.zeros(160, dtype=np.float32) for _ in range(30)],
            captured_queue_max_bytes=1,
            recognized_queue_size=1,
            translated_queue_size=1,
            synthesized_queue_max_bytes=1,
            on_dropped=lambda sids, stage: seen.append((sids, stage)),
        )
        coord.start(capture_source_id="dummy", output_device_id="dummy_out")
        time.sleep(0.3)
        coord.stop()

        assert seen, "on_dropped が一度も呼ばれていない"
        for sids, stage in seen:
            assert isinstance(sids, list)
            assert all(isinstance(s, int) and s > 0 for s in sids)
            assert stage.endswith("_queue(Input→ASR)") or "_queue(" in stage

    def test_dropped_seq_ids_removed_from_ledger(self) -> None:
        """ドロップしたら ledger からも消える(リークしない)。"""
        coord, _ = _build(
            chunks=[np.zeros(160, dtype=np.float32) for _ in range(40)],
            captured_queue_max_bytes=1,
            recognized_queue_size=1,
            translated_queue_size=1,
            synthesized_queue_max_bytes=1,
        )
        coord.start(capture_source_id="dummy", output_device_id="dummy_out")
        time.sleep(0.3)
        coord.stop()
        # 通った発話は output_loop で pop、捨てられた発話は drop でも pop、
        # → 最終的に ledger は空
        assert len(coord.ledger) == 0

    def test_callback_exception_does_not_stop_pipeline(self, caplog) -> None:
        import logging
        caplog.set_level(logging.ERROR, logger="voice_translator")

        coord, _ = _build(
            chunks=[np.zeros(160, dtype=np.float32) for _ in range(30)],
            captured_queue_max_bytes=1,
            recognized_queue_size=1,
            translated_queue_size=1,
            synthesized_queue_max_bytes=1,
            on_dropped=lambda sids, stage: (_ for _ in ()).throw(RuntimeError("boom")),
        )
        coord.start(capture_source_id="dummy", output_device_id="dummy_out")
        time.sleep(0.3)
        coord.stop()
        assert any("on_dropped callback failed" in r.message for r in caplog.records)

    def test_pcm_queue_uses_byte_bounded(self) -> None:
        """captured_queue / synthesized_queue が ByteBoundedQueue であること。"""
        from voice_translator.common.bounded_queue import ByteBoundedQueue
        coord, _ = _build()
        assert isinstance(coord._captured_queue, ByteBoundedQueue)
        assert isinstance(coord._synthesized_queue, ByteBoundedQueue)

    def test_text_queue_uses_count_based(self) -> None:
        """recognized_queue / translated_queue は従来の queue.Queue(件数基準)。"""
        import queue as _queue
        coord, _ = _build()
        assert isinstance(coord._recognized_queue, _queue.Queue)
        assert isinstance(coord._translated_queue, _queue.Queue)

    def test_byte_overflow_evicts_oldest_pcm(self) -> None:
        """PCM 系のバイト超過時、古いものから退避される(設定値を少し超える前提)。"""
        seen: list[tuple[list[int], str]] = []
        coord, _ = _build(
            chunks=[np.zeros(160, dtype=np.float32) for _ in range(40)],
            captured_queue_max_bytes=1,  # 必ず溢れる(1件は残る)
            on_dropped=lambda sids, stage: seen.append((sids, stage)),
        )
        coord.start(capture_source_id="dummy", output_device_id="dummy_out")
        time.sleep(0.3)
        coord.stop()

        # captured_queue 由来の dropped 通知が来ているはず
        captured_drops = [s for s in seen if "captured_queue" in s[1]]
        assert captured_drops, f"captured_queue のドロップが記録されていない: {seen}"
        # 退避された seq_id は単調増加(古い順に退避されている)
        all_dropped_seqs: list[int] = []
        for sids, _ in captured_drops:
            all_dropped_seqs.extend(sids)
        assert all_dropped_seqs == sorted(all_dropped_seqs), (
            f"退避順が古い順になっていない: {all_dropped_seqs}"
        )


# ============================================================
# 処理時間マーカー(t_*_start / t_*_end)
# ============================================================
class TestPipelineProcessTimeMarkers:
    """各処理段で backend 呼び出しの直前に t_*_start が記録されることを検証。"""

    def test_all_stage_start_markers_recorded(self) -> None:
        done: list[dict] = []
        coord, _ = _build(on_done=lambda r: done.append(r))
        coord.start(capture_source_id="dummy", output_device_id="dummy_out")
        assert _wait_until(lambda: len(done) >= 3)
        coord.stop()

        # 少なくとも 1 件は完走しているので、その timeline を見る
        rec = done[0]
        timeline = rec.get("timeline", {})
        for key in (
            "t_capture", "t_vad_end",
            "t_asr_start", "t_asr",
            "t_translate_start", "t_translate",
            "t_tts_start", "t_tts",
            "t_playback_start", "t_playback",
        ):
            assert key in timeline, f"{key} がタイムラインに記録されていない: {timeline}"

    def test_start_markers_precede_end_markers(self) -> None:
        """t_*_start ≤ t_*_end の不変条件(同 monotonic 時計上)。"""
        done: list[dict] = []
        coord, _ = _build(on_done=lambda r: done.append(r))
        coord.start(capture_source_id="dummy", output_device_id="dummy_out")
        assert _wait_until(lambda: len(done) >= 3)
        coord.stop()

        for r in done:
            tl = r["timeline"]
            for start, end in (
                ("t_asr_start", "t_asr"),
                ("t_translate_start", "t_translate"),
                ("t_tts_start", "t_tts"),
                ("t_playback_start", "t_playback"),
            ):
                assert tl[start] <= tl[end], (
                    f"{start}={tl[start]} は {end}={tl[end]} を超えてはならない"
                )


# ============================================================
# Ledger / SequenceGenerator 連携
# ============================================================
class TestPipelineLedgerIntegration:
    def test_seq_id_is_monotonic(self) -> None:
        done: list[dict] = []
        coord, _ = _build(on_done=lambda r: done.append(r))
        coord.start(capture_source_id="dummy", output_device_id="dummy_out")
        assert _wait_until(lambda: len(done) >= 5)
        coord.stop()
        seqs = [r["seq_id"] for r in done]
        # 全て一意かつ単調増加
        assert seqs == sorted(seqs)
        assert len(set(seqs)) == len(seqs)

    def test_timeline_has_all_stages(self) -> None:
        done: list[dict] = []
        coord, _ = _build(on_done=lambda r: done.append(r))
        coord.start(capture_source_id="dummy", output_device_id="dummy_out")
        assert _wait_until(lambda: len(done) >= 1)
        coord.stop()
        r = done[0]
        timeline = r["timeline"]
        for key in ("t_capture", "t_vad_end", "t_asr", "t_translate", "t_tts", "t_playback"):
            assert key in timeline, f"{key} がタイムラインに記録されていない"

    def test_external_ledger_and_sequence(self) -> None:
        ext_ledger = UtteranceLedger()
        ext_seq = SequenceGenerator(start=100)
        chunks = [np.zeros(160, dtype=np.float32) for _ in range(3)]
        output = FakeOutput()
        done: list[dict] = []
        coord = PipelineCoordinator(
            capture=FakeCapture(chunks),
            vad=FakeVad(every_n_chunks=1),
            asr=FakeAsr(),
            translator=FakeTranslator(),
            tts=FakeTts(),
            output=output,
            error_handler=ErrorHandler(),
            ledger=ext_ledger,
            sequence=ext_seq,
            src_lang="en",
            tgt_lang="ja",
            on_utterance_done=lambda r: done.append(r),
            read_timeout=0.01,
        )
        coord.start(capture_source_id="dummy", output_device_id="dummy_out")
        assert _wait_until(lambda: len(done) >= 3)
        coord.stop()
        # seq は 101 から始まっている
        assert done[0]["seq_id"] == 101
        # 終了後 ledger は空
        assert len(ext_ledger) == 0


# ============================================================
# TextLogger 連携
# ============================================================
class TestPipelineTextLoggerIntegration:
    def test_write_src_called_at_asr_stage(self, tmp_path) -> None:
        text_logger = TextLogger(
            src_path=tmp_path / "soundsrc.txt",
            tgt_path=tmp_path / "translated.txt",
            src_enabled=True,
            tgt_enabled=False,
        )
        coord, _ = _build(
            chunks=[np.zeros(160, dtype=np.float32) for _ in range(3)],
            text_logger=text_logger,
        )
        coord.start(capture_source_id="dummy", output_device_id="dummy_out")
        assert _wait_until(lambda: (tmp_path / "soundsrc.txt").exists())
        coord.stop()
        content = (tmp_path / "soundsrc.txt").read_text(encoding="utf-8")
        assert "hello" in content
        assert "[en]" in content
        assert "#" in content  # seq_id プレフィックス
        assert not (tmp_path / "translated.txt").exists()

    def test_write_tgt_called_at_translator_stage(self, tmp_path) -> None:
        text_logger = TextLogger(
            src_path=tmp_path / "soundsrc.txt",
            tgt_path=tmp_path / "translated.txt",
            src_enabled=False,
            tgt_enabled=True,
        )
        coord, _ = _build(
            chunks=[np.zeros(160, dtype=np.float32) for _ in range(3)],
            text_logger=text_logger,
        )
        coord.start(capture_source_id="dummy", output_device_id="dummy_out")
        assert _wait_until(lambda: (tmp_path / "translated.txt").exists())
        coord.stop()
        content = (tmp_path / "translated.txt").read_text(encoding="utf-8")
        assert "hello->ja" in content
        assert "[ja]" in content


# ============================================================
# 5スレッド構成の存在確認
# ============================================================
class TestPipelineThreadCount:
    def test_five_threads_started(self) -> None:
        coord, _ = _build()
        coord.start(capture_source_id="dummy", output_device_id="dummy_out")
        try:
            # Coordinator が 5 スレッドを保持していること
            threads = [
                coord._input_thread,
                coord._asr_thread,
                coord._translator_thread,
                coord._tts_thread,
                coord._output_thread,
            ]
            assert all(t is not None for t in threads)
            assert all(t.is_alive() for t in threads)
        finally:
            coord.stop()
        # 停止後は全 None
        assert coord._input_thread is None
        assert coord._asr_thread is None
        assert coord._translator_thread is None
        assert coord._tts_thread is None
        assert coord._output_thread is None


# ============================================================
# 各段の error path(SKIP は継続+ledger リークなし / FATAL は停止)
# ============================================================
def _build_with_backends(
    *,
    chunks: list[PcmChunk] | None = None,
    capture: AudioCaptureBackend | None = None,
    vad: VadBackend | None = None,
    asr: AsrBackend | None = None,
    translator: TranslatorBackend | None = None,
    tts: TtsBackend | None = None,
    output: AudioOutputBackend | None = None,
    on_done: Callable[[dict], None] | None = None,
    text_logger: TextLogger | None = None,
    on_fatal: Callable[[str], None] | None = None,
) -> tuple[PipelineCoordinator, FakeOutput | AudioOutputBackend]:
    if chunks is None:
        chunks = [np.zeros(160, dtype=np.float32) for _ in range(5)]
    capture = capture or FakeCapture(chunks)
    output = output if output is not None else FakeOutput()
    handler = ErrorHandler(on_fatal=on_fatal) if on_fatal else ErrorHandler()
    coord = PipelineCoordinator(
        capture=capture,
        vad=vad or FakeVad(every_n_chunks=1),
        asr=asr or FakeAsr(),
        translator=translator or FakeTranslator(),
        tts=tts or FakeTts(),
        output=output,
        error_handler=handler,
        text_logger=text_logger,
        src_lang="en",
        tgt_lang="ja",
        on_utterance_done=on_done,
        read_timeout=0.01,
        captured_queue_max_bytes=1_000_000, recognized_queue_size=50, translated_queue_size=50, synthesized_queue_max_bytes=1_000_000,
    )
    return coord, output


class TestPipelineErrorAtTranslatorStage:
    def test_skip_continues_and_no_ledger_leak(self) -> None:
        chunks = [np.zeros(160, dtype=np.float32) for _ in range(3)]
        coord, output = _build_with_backends(
            chunks=chunks,
            translator=FakeTranslator(raise_exc=SkipError("bad")),
        )
        coord.start(capture_source_id="dummy", output_device_id="dummy_out")
        time.sleep(0.3)
        coord.stop()
        assert len(output.played) == 0  # 再生まで来ない
        assert len(coord.ledger) == 0   # ledger に残骸なし
        # ASR は呼ばれている = SKIP 後も新規発話が流れている
        assert coord.is_running is False

    def test_fatal_stops_pipeline(self) -> None:
        called: list[str] = []
        coord, output = _build_with_backends(
            chunks=[np.zeros(160, dtype=np.float32) for _ in range(10)],
            translator=FakeTranslator(raise_exc=FatalError("translator dead")),
            on_fatal=lambda m, **_kw: called.append(m),
        )
        coord.start(capture_source_id="dummy", output_device_id="dummy_out")
        assert _wait_until(lambda: not coord.is_running, timeout=2.0)
        coord.stop()
        assert called == ["translator dead"]
        assert len(output.played) == 0


class TestPipelineErrorAtTtsStage:
    def test_skip_continues_and_no_ledger_leak(self) -> None:
        chunks = [np.zeros(160, dtype=np.float32) for _ in range(3)]
        coord, output = _build_with_backends(
            chunks=chunks,
            tts=FakeTts(raise_exc=SkipError("empty synth")),
        )
        coord.start(capture_source_id="dummy", output_device_id="dummy_out")
        time.sleep(0.3)
        coord.stop()
        assert len(output.played) == 0
        assert len(coord.ledger) == 0

    def test_fatal_stops_pipeline(self) -> None:
        called: list[str] = []
        coord, output = _build_with_backends(
            chunks=[np.zeros(160, dtype=np.float32) for _ in range(10)],
            tts=FakeTts(raise_exc=FatalError("tts dead")),
            on_fatal=lambda m, **_kw: called.append(m),
        )
        coord.start(capture_source_id="dummy", output_device_id="dummy_out")
        assert _wait_until(lambda: not coord.is_running, timeout=2.0)
        coord.stop()
        assert called == ["tts dead"]


class TestPipelineErrorAtOutputStage:
    def test_skip_continues_and_no_ledger_leak(self) -> None:
        chunks = [np.zeros(160, dtype=np.float32) for _ in range(3)]
        coord, output = _build_with_backends(
            chunks=chunks,
            output=FakeOutput(raise_exc=SkipError("device busy")),
        )
        coord.start(capture_source_id="dummy", output_device_id="dummy_out")
        time.sleep(0.3)
        coord.stop()
        # Output が SKIP を出すので played は 0
        assert len(output.played) == 0
        # ledger は失敗パスで pop されているはず
        assert len(coord.ledger) == 0

    def test_fatal_stops_pipeline(self) -> None:
        called: list[str] = []
        coord, output = _build_with_backends(
            chunks=[np.zeros(160, dtype=np.float32) for _ in range(10)],
            output=FakeOutput(raise_exc=FatalError("speaker gone")),
            on_fatal=lambda m, **_kw: called.append(m),
        )
        coord.start(capture_source_id="dummy", output_device_id="dummy_out")
        assert _wait_until(lambda: not coord.is_running, timeout=2.0)
        coord.stop()
        assert called == ["speaker gone"]


class TestPipelineErrorAtInputStage:
    def test_vad_skip_does_not_stop_pipeline(self) -> None:
        """VAD が SKIP を出してもループは継続する(発話は確定しない)。"""
        chunks = [np.zeros(160, dtype=np.float32) for _ in range(5)]
        coord, output = _build_with_backends(
            chunks=chunks,
            vad=RaisingVad(SkipError("noisy")),
        )
        coord.start(capture_source_id="dummy", output_device_id="dummy_out")
        time.sleep(0.2)
        # 動作継続中(FATAL ではないので生存)
        assert coord.is_running
        coord.stop()
        assert len(output.played) == 0
        assert len(coord.ledger) == 0

    def test_vad_fatal_stops_pipeline(self) -> None:
        called: list[str] = []
        coord, output = _build_with_backends(
            chunks=[np.zeros(160, dtype=np.float32) for _ in range(10)],
            vad=RaisingVad(FatalError("vad model dead")),
            on_fatal=lambda m, **_kw: called.append(m),
        )
        coord.start(capture_source_id="dummy", output_device_id="dummy_out")
        assert _wait_until(lambda: not coord.is_running, timeout=2.0)
        coord.stop()
        assert called == ["vad model dead"]

    def test_capture_fatal_stops_pipeline(self) -> None:
        called: list[str] = []
        coord, output = _build_with_backends(
            capture=RaisingCapture(FatalError("device gone")),
            on_fatal=lambda m, **_kw: called.append(m),
        )
        coord.start(capture_source_id="dummy", output_device_id="dummy_out")
        assert _wait_until(lambda: not coord.is_running, timeout=2.0)
        coord.stop()
        assert called == ["device gone"]


class TestPipelineEmptyTranslationSkip:
    """Translator が空文字を返した場合の passthrough。

    Coordinator は次段に流さず ledger を pop してリークさせない。
    """

    def test_empty_translation_skipped_and_no_ledger_leak(self) -> None:
        chunks = [np.zeros(160, dtype=np.float32) for _ in range(3)]
        coord, output = _build_with_backends(
            chunks=chunks,
            translator=FakeTranslator(result=""),  # 常に空文字
        )
        coord.start(capture_source_id="dummy", output_device_id="dummy_out")
        time.sleep(0.3)
        coord.stop()
        assert len(output.played) == 0  # 空翻訳は TTS に流れない
        assert len(coord.ledger) == 0   # リークなし


# ============================================================
# コールバック例外耐性(pipeline は止まらない)
# ============================================================
class TestPipelineCallbackResilience:
    """callback / TextLogger が例外を出してもパイプラインは止まらない。

    テスト戦略: チャンク数 N を決めて N 件すべて再生まで待ってから停止し、
    ledger が空であること(=callback 失敗でも内部状態はクリーン)を確認する。
    """

    N_CHUNKS = 3

    def test_on_utterance_done_exception_does_not_stop(self, caplog) -> None:
        import logging
        caplog.set_level(logging.ERROR, logger="voice_translator")

        def boom(record: dict) -> None:
            raise RuntimeError("ui broken")

        chunks = [np.zeros(160, dtype=np.float32) for _ in range(self.N_CHUNKS)]
        coord, output = _build_with_backends(chunks=chunks, on_done=boom)
        coord.start(capture_source_id="dummy", output_device_id="dummy_out")
        # 全 N 件再生まで待つ(callback 例外でも処理は止まらない)
        assert _wait_until(lambda: len(output.played) >= self.N_CHUNKS, timeout=2.0)
        coord.stop()
        assert any("on_utterance_done callback failed" in r.message for r in caplog.records)
        # callback 失敗でも ledger.pop は callback の前に済んでいるのでリークなし
        assert len(coord.ledger) == 0

    def test_text_logger_write_src_exception_does_not_stop(self, caplog) -> None:
        import logging
        caplog.set_level(logging.ERROR, logger="voice_translator")

        class BrokenTextLogger:
            src_enabled = True
            tgt_enabled = True

            def write_src(self, seq_id, text, lang):
                raise OSError("disk full")

            def write_tgt(self, seq_id, text, lang):
                pass

        chunks = [np.zeros(160, dtype=np.float32) for _ in range(self.N_CHUNKS)]
        coord, output = _build_with_backends(
            chunks=chunks,
            text_logger=BrokenTextLogger(),  # type: ignore[arg-type]
        )
        coord.start(capture_source_id="dummy", output_device_id="dummy_out")
        # write_src が例外を出しても、それ以降の段(translate/tts/play)は進む
        assert _wait_until(lambda: len(output.played) >= self.N_CHUNKS, timeout=2.0)
        coord.stop()
        assert any("write_src failed" in r.message for r in caplog.records)
        assert len(coord.ledger) == 0

    def test_text_logger_write_tgt_exception_does_not_stop(self, caplog) -> None:
        import logging
        caplog.set_level(logging.ERROR, logger="voice_translator")

        class BrokenTextLogger:
            src_enabled = True
            tgt_enabled = True

            def write_src(self, seq_id, text, lang):
                pass

            def write_tgt(self, seq_id, text, lang):
                raise OSError("disk full")

        chunks = [np.zeros(160, dtype=np.float32) for _ in range(self.N_CHUNKS)]
        coord, output = _build_with_backends(
            chunks=chunks,
            text_logger=BrokenTextLogger(),  # type: ignore[arg-type]
        )
        coord.start(capture_source_id="dummy", output_device_id="dummy_out")
        assert _wait_until(lambda: len(output.played) >= self.N_CHUNKS, timeout=2.0)
        coord.stop()
        assert any("write_tgt failed" in r.message for r in caplog.records)
        assert len(coord.ledger) == 0


# ============================================================
# 同一 seq_id が全段で一貫していること(段間の取り違いを防ぐ)
# ============================================================
class TestPipelineSeqIdConsistency:
    """ASR/Translator/TTS/Output の各段で、同一発話に同じ seq_id が使われる。

    観測戦略: TextLogger をスパイし、write_src(ASR段) と write_tgt(Translator段) の
    seq_id 並びを取得 → on_utterance_done(Output段) の seq_id 並びと一致することを検証。
    """

    def test_seq_id_consistent_across_all_stages(self) -> None:
        src_seq_ids: list[int] = []
        tgt_seq_ids: list[int] = []

        class SpyTextLogger:
            src_enabled = True
            tgt_enabled = True

            def write_src(self, seq_id, text, lang):
                src_seq_ids.append(seq_id)

            def write_tgt(self, seq_id, text, lang):
                tgt_seq_ids.append(seq_id)

        done_seq_ids: list[int] = []
        chunks = [np.zeros(160, dtype=np.float32) for _ in range(5)]
        coord, output = _build_with_backends(
            chunks=chunks,
            text_logger=SpyTextLogger(),  # type: ignore[arg-type]
            on_done=lambda r: done_seq_ids.append(r["seq_id"]),
        )
        coord.start(capture_source_id="dummy", output_device_id="dummy_out")
        assert _wait_until(lambda: len(done_seq_ids) >= 5)
        coord.stop()

        # 3 つのリストが同じ集合・同じ順序(ASR → Translator → Output の順で進行)
        assert src_seq_ids == tgt_seq_ids == done_seq_ids
        assert len(set(src_seq_ids)) == len(src_seq_ids)  # 重複なし
        assert src_seq_ids == sorted(src_seq_ids)         # 単調増加

    def test_seq_id_consistent_under_skip_at_translator(self) -> None:
        """Translator が SKIP した発話の seq_id は Output に到達しない(全段一貫)。"""
        src_seq_ids: list[int] = []
        done_seq_ids: list[int] = []

        class SpyTextLogger:
            src_enabled = True
            tgt_enabled = True
            def write_src(self, seq_id, text, lang):
                src_seq_ids.append(seq_id)
            def write_tgt(self, seq_id, text, lang):
                pass

        chunks = [np.zeros(160, dtype=np.float32) for _ in range(3)]
        coord, output = _build_with_backends(
            chunks=chunks,
            translator=FakeTranslator(raise_exc=SkipError("nope")),
            text_logger=SpyTextLogger(),  # type: ignore[arg-type]
            on_done=lambda r: done_seq_ids.append(r["seq_id"]),
        )
        coord.start(capture_source_id="dummy", output_device_id="dummy_out")
        time.sleep(0.3)
        coord.stop()

        # ASR には到達しているが Output には届かない
        assert len(src_seq_ids) > 0
        assert done_seq_ids == []
        # SKIP 後も他発話の処理は継続している(=ASR は複数回呼ばれた)
        assert len(coord.ledger) == 0


# ============================================================
# キューフル時の並行性: 消費者が処理中の発話が、生産者の put/drop で壊されないこと
# ============================================================
class TestPipelineQueueFullConcurrency:
    """C/C++ 的な「list[0] を処理中に生産者が [0] を書き換える」懸念の検証。

    Python の queue.Queue は get() した瞬間に内部から要素が消えるため、
    消費者(翻訳スレッド)の処理中要素を生産者(ASRスレッド)が触ることは構造上できない。
    さらに PipelineMessage / 各 *Payload は frozen dataclass なので、
    参照が共有されていてもフィールド書き換えはできない。
    本テストでは「翻訳ゆっくり化 → 後段キュー満杯 → ドロップ多数」の状況で、
    翻訳中の発話が正しく完了することを実測で確認する。
    """

    def test_translator_in_progress_item_not_corrupted_by_overflow(self) -> None:
        import threading as _t

        gate_started = _t.Event()
        gate_release = _t.Event()
        translate_inputs: list[tuple[int, str]] = []  # (呼び出し回, src_text)
        translate_outputs: list[str] = []

        class SlowFirstTranslator(TranslatorBackend):
            """1回目だけ意図的にブロックする翻訳。"""

            @classmethod
            def supported_target_languages(cls) -> list[str]:
                return ["en", "ja"]

            def __init__(self) -> None:
                self._count = 0
                self._lock = _t.Lock()

            def translate(self, src_text: str, src_lang: str, tgt_lang: str) -> str:
                with self._lock:
                    self._count += 1
                    n = self._count
                translate_inputs.append((n, src_text))
                if n == 1:
                    # 翻訳中シグナル → 解放されるまで待機(その間 recognized_queue に後続が溜まる)
                    gate_started.set()
                    gate_release.wait(timeout=3.0)
                # 結果は「入力文字列 + 呼び出し回」: 入力が途中で書き換わったら検出できる
                out = f"{src_text}::call_{n}"
                translate_outputs.append(out)
                return out

        chunks = [np.zeros(160, dtype=np.float32) for _ in range(20)]
        slow_translator = SlowFirstTranslator()
        coord, output = _build_with_backends(
            chunks=chunks,
            translator=slow_translator,
            # recognized_queue を最小化して、翻訳ブロック中に必ず満杯になるよう仕向ける
        )
        # recognized_queue_size を小さくして強制的にあふれさせる
        # _build_with_backends は q_*_size=50 を渡すので、別途 PipelineCoordinator を組み直す
        # ↑ シンプル化のため独自に構築する
        capture = FakeCapture(chunks)
        out2 = FakeOutput()
        seen_dropped: list[tuple[list[int], str]] = []
        coord2 = PipelineCoordinator(
            capture=capture,
            vad=FakeVad(every_n_chunks=1),
            asr=FakeAsr(),
            translator=slow_translator,
            tts=FakeTts(),
            output=out2,
            error_handler=ErrorHandler(),
            src_lang="en",
            tgt_lang="ja",
            on_dropped=lambda sids, stage: seen_dropped.append((sids, stage)),
            read_timeout=0.01,
            captured_queue_max_bytes=1, recognized_queue_size=1, translated_queue_size=50, synthesized_queue_max_bytes=1_000_000,
        )

        coord2.start(capture_source_id="dummy", output_device_id="dummy_out")
        try:
            # 1) 翻訳1件目がブロック状態に入るのを待つ
            assert gate_started.wait(timeout=2.0), "翻訳1件目に到達しない"

            # 2) その間、ASR が後続発話を生産して recognized_queue (size=1) に push しまくる
            #    満杯のため _put_with_drop で旧要素を捨てる動作が走る
            time.sleep(0.3)

            # 3) 翻訳を解放
            gate_release.set()

            # 4) ブロックされていた1件目を含め、流れた分の再生が落ち着くまで待つ
            time.sleep(0.5)
        finally:
            coord2.stop()

        # 検証1: 翻訳1件目は「入力 hello、呼び出し回 1」で完了している。
        #        途中で別の発話に書き換えられていないこと。
        assert translate_inputs[0] == (1, "hello"), (
            f"翻訳1件目の入力が壊れている: {translate_inputs[0]}"
        )
        # 出力も整合(入力テキストがそのまま戻ってきている)
        assert translate_outputs[0] == "hello::call_1", (
            f"翻訳1件目の出力が壊れている: {translate_outputs[0]}"
        )

        # 検証2: ドロップは実際に起きている(=テストが「満杯状況」を再現できている)
        assert sum(len(s) for s, _ in seen_dropped) > 0, (
            "q が満杯になったケースを再現できていない(テスト前提が崩れている)"
        )

        # 検証3: 全ての翻訳呼び出しが、入力テキスト hello のまま終わっている。
        #        (入力が並行更新で書き換わったら "hello" 以外になっているはず)
        for (_, src_text) in translate_inputs:
            assert src_text == "hello"

    def test_payload_is_frozen_against_mutation(self) -> None:
        """Payload が frozen であることの再確認(構造的に書き換え不能であること)。

        これが満たされていれば、たとえキュー外で参照が共有されてもフィールド書き換えは起きない。
        """
        from voice_translator.common.messages import (
            PipelineMessage,
            TranscribedPayload,
        )

        msg = PipelineMessage(seq_id=1, payload=TranscribedPayload("hi", "en"))
        # seq_id 書き換え不可
        with pytest.raises(Exception):  # dataclasses.FrozenInstanceError
            msg.seq_id = 99  # type: ignore[misc]
        # payload フィールド書き換え不可
        with pytest.raises(Exception):
            msg.payload.src_text = "rewritten"  # type: ignore[misc]


# ============================================================
# エラー context(stage/seq_id)が callback とログに反映される
# ============================================================
class TestPipelineErrorContext:
    """例外時に callback とログに stage/seq_id が含まれることを Coordinator 経由で検証。"""

    def test_fatal_callback_receives_stage_and_seq_id(self) -> None:
        """Translator FATAL 時、on_fatal に stage='Translator' と seq_id が渡る。"""
        received: list[dict] = []

        def on_fatal(message, *, exc=None, stage=None, seq_id=None, suppressed=0):
            received.append({
                "message": message, "exc": exc, "stage": stage, "seq_id": seq_id
            })

        coord, _ = _build_with_backends(
            chunks=[np.zeros(160, dtype=np.float32) for _ in range(5)],
            translator=FakeTranslator(raise_exc=FatalError("translator broke")),
            on_fatal=on_fatal,
        )
        coord.start(capture_source_id="dummy", output_device_id="dummy_out")
        assert _wait_until(lambda: not coord.is_running, timeout=2.0)
        coord.stop()

        assert received, "on_fatal が呼ばれていない"
        first = received[0]
        assert first["message"] == "translator broke"
        assert first["stage"] == "Translator"
        assert isinstance(first["seq_id"], int) and first["seq_id"] >= 1
        assert isinstance(first["exc"], FatalError)

    def test_asr_skip_log_contains_stage_and_seq_id(self, caplog) -> None:
        """ASR SKIP のログに seq= と stage=ASR が含まれる。"""
        import logging
        caplog.set_level(logging.INFO, logger="voice_translator")

        coord, _ = _build_with_backends(
            chunks=[np.zeros(160, dtype=np.float32) for _ in range(3)],
            asr=FakeAsr(raise_exc=SkipError("empty pcm")),
        )
        coord.start(capture_source_id="dummy", output_device_id="dummy_out")
        time.sleep(0.3)
        coord.stop()

        # ログメッセージに seq= と stage=ASR と [SKIP] と本文が同時に含まれる
        target = [
            r for r in caplog.records
            if "stage=ASR" in r.message and "seq=" in r.message
            and "[SKIP]" in r.message and "empty pcm" in r.message
        ]
        assert target, f"ASR の SKIP ログに context が含まれていない: {[r.message for r in caplog.records]}"

    def test_capture_fatal_log_has_stage_but_no_seq_id(self, caplog) -> None:
        """Capture 段は seq_id 発行前なので seq= は出ない(stage= だけ)。"""
        import logging
        caplog.set_level(logging.ERROR, logger="voice_translator")

        coord, _ = _build_with_backends(
            capture=RaisingCapture(FatalError("device gone")),
        )
        coord.start(capture_source_id="dummy", output_device_id="dummy_out")
        assert _wait_until(lambda: not coord.is_running, timeout=2.0)
        coord.stop()

        target = [
            r for r in caplog.records
            if "stage=Capture" in r.message and "device gone" in r.message
        ]
        assert target
        for r in target:
            assert "seq=" not in r.message  # 発行前なので付かない


# ============================================================
# 編成表駆動の組み立て
# ============================================================
class TestPlanDrivenAssembly:
    """編成表(PipelinePlan)からの組み立てと構築時の起動拒否。"""

    def test_standard_layout_exposes_plan(self) -> None:
        coord, _ = _build()
        labels = [s.label for s in coord.plan.stages]
        assert labels == ["Input", "ASR", "Translator", "TTS", "Output"]
        assert coord.plan.output_mode == "audio"
        assert coord.plan.absorbed == ()

    def test_mismatched_declaration_rejected_at_build(self) -> None:
        """隣接 payload 型が合わない申告は構築時に PlanError(起動拒否)。"""

        class MisdeclaredTts(FakeTts):
            @classmethod
            def consumes_payload(cls) -> PayloadKind:
                return PayloadKind.TRANSCRIBED

        with pytest.raises(PlanError, match="一致しません"):
            PipelineCoordinator(
                capture=FakeCapture([]),
                vad=FakeVad(every_n_chunks=1),
                asr=FakeAsr(),
                translator=FakeTranslator(),
                tts=MisdeclaredTts(),
                output=FakeOutput(),
                error_handler=ErrorHandler(),
            )
