"""ASR+Translator 複合 backend を含む編成の動作テスト(モック)。

複合ステージが RAW → TRANSLATED を直接産出して TTS 段へ流れること、
timeline が入口・出口のみ記録されること(内側は欠損)、text_only との併用を固定する。
"""

from __future__ import annotations

import time
from typing import Any, Callable

import numpy as np

from tests.test_pipeline import FakeCapture, FakeOutput, FakeTts, FakeVad
from voice_translator.asr.backend import AsrTranslatorBackend
from voice_translator.common.error_handler import ErrorHandler
from voice_translator.common.pipeline import PipelineCoordinator
from voice_translator.common.types import LayerKind


class FakeAsrTranslator(AsrTranslatorBackend):
    """書き起こし+翻訳を 1 呼び出しで返す複合 backend のモック。"""

    def __init__(self, *, tgt_text: str = "hello in english") -> None:
        super().__init__()
        self.calls: int = 0
        self._tgt_text = tgt_text

    def transcribe_translate(
        self, pcm: Any, src_lang_hint: str = "auto", tgt_lang: str = "en"
    ) -> tuple[str, str, str, str]:
        self.calls += 1
        return "", "ja", self._tgt_text, "en"

    @classmethod
    def supported_input_languages(cls) -> list[str]:
        return ["ja", "en"]

    @classmethod
    def supported_target_languages(cls) -> list[str]:
        return ["en"]


def _build_composite(
    *,
    output_mode: str = "audio",
    on_done: Callable[[dict], None] | None = None,
    on_text_ready: Callable[[dict], None] | None = None,
    tgt_text: str = "hello in english",
) -> tuple[PipelineCoordinator, FakeAsrTranslator, FakeOutput | None]:
    chunks = [np.zeros(160, dtype=np.float32) for _ in range(3)]
    composite = FakeAsrTranslator(tgt_text=tgt_text)
    output = FakeOutput() if output_mode == "audio" else None
    coord = PipelineCoordinator(
        capture=FakeCapture(chunks),
        vad=FakeVad(every_n_chunks=1),
        asr=composite,
        translator=None,  # 吸収されるため未提供
        tts=FakeTts() if output_mode == "audio" else None,
        output=output,
        error_handler=ErrorHandler(),
        src_lang="auto",
        tgt_lang="en",
        on_utterance_done=on_done,
        on_text_ready=on_text_ready,
        read_timeout=0.01,
        output_mode=output_mode,
    )
    return coord, composite, output


def _wait_until(predicate: Callable[[], bool], timeout: float = 2.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return False


class TestCompositePlan:
    def test_translator_stage_absorbed(self) -> None:
        coord, _, _ = _build_composite()
        labels = [s.label for s in coord.plan.stages]
        assert labels == ["Input", "ASR+Translator", "TTS", "Output"]
        assert coord.plan.absorbed_map == {LayerKind.TRANSLATOR: LayerKind.ASR}


class TestCompositeAudioFlow:
    def test_utterances_flow_through_composite_to_output(self) -> None:
        done: list[dict] = []
        coord, composite, output = _build_composite(on_done=lambda r: done.append(r))
        coord.start(capture_source_id="dummy", output_device_id="dummy_out")
        assert _wait_until(lambda: len(output.played) >= 3)
        coord.stop()

        assert composite.calls == 3
        assert len(done) == 3
        for r in done:
            assert r["src_text"] == ""          # Whisper translate 相当: 源文なし
            assert r["src_lang"] == "ja"        # hint=auto → 検出言語を採用
            assert r["tgt_text"] == "hello in english"
            assert r["tgt_lang"] == "en"

    def test_timeline_has_entry_and_exit_only(self) -> None:
        done: list[dict] = []
        coord, _, output = _build_composite(on_done=lambda r: done.append(r))
        coord.start(capture_source_id="dummy", output_device_id="dummy_out")
        assert _wait_until(lambda: len(done) >= 1)
        coord.stop()

        tl = done[0]["timeline"]
        assert "t_asr_start" in tl      # 入口
        assert "t_translate" in tl      # 出口
        assert "t_asr" not in tl        # 内側の境界は欠損
        assert "t_translate_start" not in tl

    def test_empty_translation_is_dropped(self) -> None:
        done: list[dict] = []
        coord, _, output = _build_composite(
            on_done=lambda r: done.append(r), tgt_text="",
        )
        coord.start(capture_source_id="dummy", output_device_id="dummy_out")
        time.sleep(0.3)
        coord.stop()
        assert done == []
        assert output.played == []
        assert len(coord.ledger) == 0  # 破棄分の ledger リークなし


class TestCompositeTextOnly:
    def test_composite_is_final_stage_in_text_only(self) -> None:
        ready: list[dict] = []
        coord, _, _ = _build_composite(
            output_mode="text_only", on_text_ready=lambda r: ready.append(r),
        )
        assert [s.label for s in coord.plan.stages] == ["Input", "ASR+Translator"]
        coord.start(capture_source_id="dummy", output_device_id="")
        assert _wait_until(lambda: len(ready) >= 3)
        coord.stop()
        for r in ready:
            assert r["tgt_text"] == "hello in english"
        assert len(coord.ledger) == 0  # 最終段で pop 済み
