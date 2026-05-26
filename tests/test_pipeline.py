"""PipelineCoordinator の単体テスト。モックバックエンドで動作確認する。"""

from __future__ import annotations

import threading
import time
from typing import Callable

import numpy as np
import pytest

from voice_translator.asr.backend import AsrBackend
from voice_translator.capture.backend import AudioCaptureBackend
from voice_translator.common.error_handler import ErrorHandler
from voice_translator.common.errors import FatalError, SkipError
from voice_translator.common.pipeline import PipelineCoordinator
from voice_translator.common.types import CaptureSource, OutputDevice, PcmChunk
from voice_translator.common.utterance import Utterance
from voice_translator.output.backend import AudioOutputBackend
from voice_translator.translator.backend import TranslatorBackend
from voice_translator.tts.backend import TtsBackend
from voice_translator.vad.backend import VadBackend


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
    """N チャンクごとに1発話を作るVAD。"""

    def __init__(self, every_n_chunks: int = 1) -> None:
        self._n = every_n_chunks
        self._count = 0

    def reset(self) -> None:
        self._count = 0

    def process(self, chunk: PcmChunk) -> list[Utterance]:
        self._count += 1
        if self._count % self._n != 0:
            return []
        u = Utterance(pcm=chunk)
        u.timeline.mark("t_capture")
        u.timeline.mark("t_vad_end")
        return [u]


class FakeAsr(AsrBackend):
    def __init__(self, *, raise_exc: BaseException | None = None) -> None:
        self._raise = raise_exc

    def transcribe(self, utterance: Utterance, src_lang: str = "auto") -> Utterance:
        if self._raise is not None:
            raise self._raise
        utterance.src_text = "hello"
        return utterance


class FakeTranslator(TranslatorBackend):
    def translate(self, utterance: Utterance, tgt_lang: str) -> Utterance:
        utterance.tgt_text = utterance.src_text + "->ja"
        utterance.tgt_lang = tgt_lang
        return utterance


class FakeTts(TtsBackend):
    def synthesize(self, utterance: Utterance) -> Utterance:
        utterance.tts_pcm = b"audio"
        return utterance


class FakeOutput(AudioOutputBackend):
    def __init__(self) -> None:
        self.played: list[Utterance] = []
        self._started = False

    def list_devices(self) -> list[OutputDevice]:
        return [OutputDevice("dummy_out", "Dummy")]

    def start(self, device_id: str) -> None:
        self._started = True

    def play(self, utterance: Utterance) -> None:
        self.played.append(utterance)

    def stop(self) -> None:
        self._started = False


# ============================================================
# テスト
# ============================================================
def _build(
    *,
    chunks: list[PcmChunk] | None = None,
    asr: AsrBackend | None = None,
    on_done: Callable[[Utterance], None] | None = None,
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
        src_lang="en",
        tgt_lang="ja",
        on_utterance_done=on_done,
        read_timeout=0.01,
    )
    return coord, output


def _wait_until(predicate: Callable[[], bool], timeout: float = 2.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return False


class TestPipelineLifecycle:
    def test_start_runs_loop_and_processes_utterances(self) -> None:
        coord, output = _build()
        coord.start(capture_source_id="dummy", output_device_id="dummy_out")
        assert _wait_until(lambda: len(output.played) >= 5)
        coord.stop()
        assert not coord.is_running
        assert len(output.played) == 5
        for u in output.played:
            assert u.src_text == "hello"
            assert u.tgt_text == "hello->ja"
            assert u.tts_pcm == b"audio"

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


class TestPipelineErrorHandling:
    def test_skip_error_continues(self) -> None:
        # 最初の発話のみ ASR が SKIP を出す動作はモックで難しいので、
        # 全発話 SKIP を投げて「停止せず尽きるまで回る」ことを確認する
        chunks = [np.zeros(160, dtype=np.float32) for _ in range(3)]
        coord, output = _build(chunks=chunks, asr=FakeAsr(raise_exc=SkipError("empty")))
        coord.start(capture_source_id="dummy", output_device_id="dummy_out")
        # チャンクが尽きるまで待つ
        time.sleep(0.3)
        coord.stop()
        assert len(output.played) == 0  # SKIP のため再生はされない

    def test_fatal_error_stops_pipeline(self) -> None:
        chunks = [np.zeros(160, dtype=np.float32) for _ in range(10)]
        called: list[str] = []
        handler = ErrorHandler(on_fatal=lambda m: called.append(m))
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


class TestPipelineTimeline:
    def test_timeline_is_populated(self) -> None:
        seen: list[Utterance] = []
        coord, output = _build(
            chunks=[np.zeros(160, dtype=np.float32)],
            on_done=lambda u: seen.append(u),
        )
        coord.start(capture_source_id="dummy", output_device_id="dummy_out")
        assert _wait_until(lambda: len(seen) >= 1)
        coord.stop()
        u = seen[0]
        for key in ("t_capture", "t_vad_end", "t_asr", "t_translate", "t_tts", "t_playback"):
            assert u.timeline.get(key) is not None, f"{key} がタイムラインに記録されていない"
        latency = u.timeline.elapsed("t_capture", "t_playback")
        assert latency is not None and latency >= 0
