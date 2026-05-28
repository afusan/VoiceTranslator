"""PipelineCoordinator がステージ間ダンプフックを正しい順序・引数で呼ぶことの検証。

実 StageDumpWriter ではなく Spy(イベント記録だけ)を注入することで、ファイルI/O や
ワーカスレッドの絡みなしに「いつ・どの seq_id で・どのデータが」フックに渡るかを観測する。
StageDumpWriter 本体の入出力規約は test_stage_dump.py を参照。
"""

from __future__ import annotations

import time
from typing import Any, Callable

import numpy as np

from voice_translator.common.error_handler import ErrorHandler
from voice_translator.common.pipeline import PipelineCoordinator

# モックバックエンドは test_pipeline.py のものを再利用する
from tests.test_pipeline import (
    FakeAsr,
    FakeCapture,
    FakeOutput,
    FakeTranslator,
    FakeTts,
    FakeVad,
    _wait_until,
)


class SpyDumpWriter:
    """役割: PipelineCoordinator が dump フックを呼んだ事実だけを記録するスパイ。

    StageDumpWriter / NullStageDumpWriter と同じ I/F を持つ duck-typed 実装。
    """

    def __init__(self) -> None:
        self.events: list[tuple] = []

    def start_run(self, meta: dict[str, Any] | None = None) -> None:
        self.events.append(("start_run", meta))

    def stop_run(self, *, join_timeout: float = 2.0) -> None:
        self.events.append(("stop_run",))

    def on_vad(self, seq_id: int, pcm: Any, samplerate: int) -> None:
        self.events.append(("vad", seq_id, int(getattr(pcm, "size", 0)), samplerate))

    def on_asr(self, seq_id: int, text: str, src_lang: str) -> None:
        self.events.append(("asr", seq_id, text, src_lang))

    def on_translate(
        self,
        seq_id: int,
        src_text: str,
        src_lang: str,
        tgt_text: str,
        tgt_lang: str,
    ) -> None:
        self.events.append(("translate", seq_id, src_text, tgt_text))

    def on_tts(self, seq_id: int, pcm: Any, samplerate: int) -> None:
        self.events.append(("tts", seq_id, samplerate))


def _build(
    *,
    dump: SpyDumpWriter | None,
    n_chunks: int = 3,
    on_done: Callable[[dict], None] | None = None,
) -> tuple[PipelineCoordinator, FakeOutput, SpyDumpWriter | None]:
    chunks = [np.zeros(160, dtype=np.float32) for _ in range(n_chunks)]
    output = FakeOutput()
    coord = PipelineCoordinator(
        capture=FakeCapture(chunks),
        vad=FakeVad(every_n_chunks=1),
        asr=FakeAsr(),
        translator=FakeTranslator(),
        tts=FakeTts(),
        output=output,
        error_handler=ErrorHandler(),
        src_lang="en",
        tgt_lang="ja",
        on_utterance_done=on_done,
        read_timeout=0.01,
        captured_queue_max_bytes=1_000_000,
        synthesized_queue_max_bytes=1_000_000,
        recognized_queue_size=50,
        translated_queue_size=50,
        dump=dump,
    )
    return coord, output, dump


# ============================================================
# 既定(dump 未指定)は NullStageDumpWriter になる
# ============================================================
def test_default_dump_is_null_writer() -> None:
    """`dump=None` を渡したとき、Coordinator は Null 実装を内部で生成して動く。

    回帰チェック: 既存テスト群(`PipelineCoordinator(..., dump=...)` を渡さない)
    がこの変更で壊れないことの担保。
    """
    coord, output, _ = _build(dump=None)
    coord.start(capture_source_id="dummy", output_device_id="dummy_out")
    try:
        _wait_until(lambda: len(output.played) >= 3, timeout=2.0)
    finally:
        coord.stop()
    assert len(output.played) >= 1


# ============================================================
# フックの順序・引数
# ============================================================
def test_hooks_called_in_pipeline_order_per_utterance() -> None:
    """1 発話あたり vad → asr → translate → tts の順で同じ seq_id でフックが呼ばれる。

    FakeVad(every_n_chunks=1) なので 3 チャンク = 3 発話。
    """
    spy = SpyDumpWriter()
    coord, output, _ = _build(dump=spy, n_chunks=3)
    coord.start(capture_source_id="dummy", output_device_id="dummy_out")
    try:
        _wait_until(lambda: len(output.played) >= 3, timeout=2.0)
    finally:
        coord.stop()

    # seq_id ごとに、出現順を抽出する
    by_seq: dict[int, list[str]] = {}
    for ev in spy.events:
        kind = ev[0]
        if kind in {"vad", "asr", "translate", "tts"}:
            seq_id = ev[1]
            by_seq.setdefault(seq_id, []).append(kind)

    assert len(by_seq) == 3, f"3 発話ぶんの seq_id が出るはず: {by_seq}"
    for seq_id, order in by_seq.items():
        assert order == ["vad", "asr", "translate", "tts"], (
            f"seq_id={seq_id} の順序が不正: {order}"
        )


def test_hook_payload_matches_backend_outputs() -> None:
    """各フックに渡る payload が FakeAsr/FakeTranslator の戻り値と一致する。"""
    spy = SpyDumpWriter()
    coord, output, _ = _build(dump=spy, n_chunks=1)
    coord.start(capture_source_id="dummy", output_device_id="dummy_out")
    try:
        _wait_until(lambda: len(output.played) >= 1, timeout=2.0)
    finally:
        coord.stop()

    asr_events = [e for e in spy.events if e[0] == "asr"]
    tr_events = [e for e in spy.events if e[0] == "translate"]
    assert asr_events and asr_events[0][2] == "hello"   # FakeAsr の戻り値
    assert asr_events[0][3] == "en"
    assert tr_events and tr_events[0][2] == "hello"     # src_text
    assert tr_events[0][3] == "hello->ja"               # FakeTranslator の戻り値


def test_coordinator_does_not_invoke_start_or_stop_run() -> None:
    """ライフサイクル(start_run / stop_run)は呼び出し側(AppController)の責務。

    Coordinator は on_* のみを呼ぶ。これにより
    「Coordinator は dump をパススルーするだけ」という責務分離が保たれる。
    """
    spy = SpyDumpWriter()
    coord, output, _ = _build(dump=spy, n_chunks=1)
    coord.start(capture_source_id="dummy", output_device_id="dummy_out")
    try:
        _wait_until(lambda: len(output.played) >= 1, timeout=2.0)
    finally:
        coord.stop()

    kinds = {e[0] for e in spy.events}
    assert "start_run" not in kinds
    assert "stop_run" not in kinds
