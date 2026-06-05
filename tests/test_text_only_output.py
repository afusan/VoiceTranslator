"""text_only 出力モード(P3)の挙動テスト。

主要観点:
- PipelineCoordinator(output_mode="text_only") で TTS/Output スレッドが起動しない
- Translator 完了で on_text_ready が呼ばれ、ledger が即 pop される(バッファ即解放)
- translated_queue / synthesized_queue に何も流れない(リーク無し)
- audio モードの既存挙動は変わらない
- restart 時(text_only → audio / audio → text_only)に残骸が引き継がれない
- AppController._active_layers / output_mode の挙動
- AppController._handle_text_ready で text_only のとき jsonl / processtime を書く
- ConfigStore デフォルト
"""

from __future__ import annotations

import queue
import time
from time import monotonic
from typing import Callable
from unittest.mock import MagicMock

import numpy as np
import pytest

from voice_translator.asr.backend import AsrBackend
from voice_translator.capture.backend import AudioCaptureBackend
from voice_translator.common.app_controller import AppController
from voice_translator.common.config_store import ConfigStore, DEFAULT_CONFIG
from voice_translator.common.error_handler import ErrorHandler
from voice_translator.common.pipeline import PipelineCoordinator
from voice_translator.common.types import (
    CaptureSource,
    LayerKind,
    OutputDevice,
    PcmChunk,
)
from voice_translator.output.backend import AudioOutputBackend
from voice_translator.translator.backend import TranslatorBackend
from voice_translator.tts.backend import TtsBackend
from voice_translator.vad.backend import VadBackend, VadSegment


# ============================================================
# モック群(test_pipeline.py 由来の最小版)
# ============================================================
class _SineCapture(AudioCaptureBackend):
    """pcm をチャンクごとに返し、尽きたら None。"""

    def __init__(self, pcm: np.ndarray, chunk_size: int = 512) -> None:
        self._pcm = pcm
        self._chunk = chunk_size
        self._pos = 0
        self._started = False

    def list_sources(self) -> list[CaptureSource]:
        return [CaptureSource("sine", "sine")]

    def start(self, source_id: str) -> None:
        self._started = True
        self._pos = 0

    def stop(self) -> None:
        self._started = False

    def read_chunk(self, timeout: float = 0.1) -> PcmChunk | None:
        if not self._started:
            raise RuntimeError("not started")
        if self._pos >= self._pcm.size:
            time.sleep(timeout)
            return None
        end = min(self._pos + self._chunk, self._pcm.size)
        chunk = self._pcm[self._pos:end]
        self._pos = end
        return chunk


class _VadEveryN(VadBackend):
    def __init__(self, every_n: int = 3) -> None:
        self._n = every_n
        self._count = 0
        self._buf: list[np.ndarray] = []

    def reset(self) -> None:
        self._count = 0
        self._buf = []

    def process(self, chunk: PcmChunk) -> list[VadSegment]:
        self._buf.append(chunk.copy())
        self._count += 1
        if self._count % self._n != 0:
            return []
        pcm = np.concatenate(self._buf)
        self._buf = []
        return [VadSegment(pcm=pcm, started_at_monotonic=monotonic())]


class _EchoAsr(AsrBackend):
    def transcribe(self, pcm, src_lang_hint: str = "auto") -> tuple[str, str]:
        return f"text({pcm.shape[0]})", "en"

    @classmethod
    def supported_input_languages(cls) -> list[str]:
        return ["en"]


class _SuffixTranslator(TranslatorBackend):
    @classmethod
    def supported_target_languages(cls) -> list[str]:
        return ["en", "ja"]

    def __init__(self) -> None:
        self.calls = 0

    def translate(self, src_text: str, src_lang: str, tgt_lang: str) -> str:
        self.calls += 1
        return f"{src_text}->{tgt_lang}"


class _SpyTts(TtsBackend):
    """synthesize 呼び出し回数を記録。text_only モードでは絶対に呼ばれないはず。"""

    @classmethod
    def supported_output_languages(cls) -> list[str]:
        return ["en", "ja"]

    def __init__(self) -> None:
        self.calls = 0

    def synthesize(self, text: str, tgt_lang: str) -> tuple:
        self.calls += 1
        return np.zeros(16, dtype=np.float32), 16000


class _SpyOutput(AudioOutputBackend):
    """play 呼び出し回数を記録。text_only モードでは絶対に呼ばれないはず。"""

    def __init__(self) -> None:
        self.calls = 0

    def list_devices(self) -> list[OutputDevice]:
        return [OutputDevice("out", "out")]

    def start(self, device_id: str) -> None:
        pass

    def stop(self) -> None:
        pass

    def play(self, pcm, samplerate: int) -> None:
        self.calls += 1


def _wait_until(predicate: Callable[[], bool], timeout: float = 3.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return False


def _make_pcm(seconds: float = 2.0) -> np.ndarray:
    t = np.linspace(0, seconds, int(16000 * seconds), endpoint=False)
    return (0.1 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)


# ============================================================
# PipelineCoordinator: 構造的な振る舞い
# ============================================================
class TestCoordinatorTextOnlyConstruction:
    def test_audio_mode_requires_tts_and_output(self) -> None:
        """audio モードでは tts/output が None だと ValueError。"""
        with pytest.raises(ValueError):
            PipelineCoordinator(
                capture=_SineCapture(_make_pcm(0.5)),
                vad=_VadEveryN(),
                asr=_EchoAsr(),
                translator=_SuffixTranslator(),
                tts=None,  # type: ignore[arg-type]
                output=None,  # type: ignore[arg-type]
                error_handler=ErrorHandler(),
            )

    def test_text_only_allows_none_tts_and_output(self) -> None:
        """text_only モードでは tts/output が None で OK。"""
        coord = PipelineCoordinator(
            capture=_SineCapture(_make_pcm(0.5)),
            vad=_VadEveryN(),
            asr=_EchoAsr(),
            translator=_SuffixTranslator(),
            tts=None,
            output=None,
            error_handler=ErrorHandler(),
            output_mode="text_only",
        )
        assert coord._output_mode == "text_only"  # noqa: SLF001

    def test_unknown_mode_falls_back_to_audio(self) -> None:
        """未知の output_mode は audio として扱う(防衛)。"""
        coord = PipelineCoordinator(
            capture=_SineCapture(_make_pcm(0.5)),
            vad=_VadEveryN(),
            asr=_EchoAsr(),
            translator=_SuffixTranslator(),
            tts=_SpyTts(),
            output=_SpyOutput(),
            error_handler=ErrorHandler(),
            output_mode="unknown_value",
        )
        assert coord._output_mode == "audio"  # noqa: SLF001


# ============================================================
# PipelineCoordinator: 実行時の挙動(text_only)
# ============================================================
class TestCoordinatorTextOnlyRuntime:
    def test_no_tts_output_threads_in_text_only(self) -> None:
        """text_only モードで start すると tts_thread / output_thread が生成されない。"""
        coord = PipelineCoordinator(
            capture=_SineCapture(_make_pcm(1.0)),
            vad=_VadEveryN(),
            asr=_EchoAsr(),
            translator=_SuffixTranslator(),
            tts=None,
            output=None,
            error_handler=ErrorHandler(),
            output_mode="text_only",
        )
        coord.start(capture_source_id="sine", output_device_id="out")
        # 起動直後は短時間で確認
        assert coord._tts_thread is None  # noqa: SLF001
        assert coord._output_thread is None  # noqa: SLF001
        # Input / ASR / Translator は起動している
        assert coord._input_thread is not None  # noqa: SLF001
        assert coord._asr_thread is not None  # noqa: SLF001
        assert coord._translator_thread is not None  # noqa: SLF001
        coord.stop()

    def test_text_only_calls_on_text_ready(self) -> None:
        """Translator 完了で on_text_ready が呼ばれる(text_only 時の最終通知)。"""
        ready_records: list[dict] = []
        done_records: list[dict] = []
        coord = PipelineCoordinator(
            capture=_SineCapture(_make_pcm(2.0)),
            vad=_VadEveryN(every_n=3),
            asr=_EchoAsr(),
            translator=_SuffixTranslator(),
            tts=None,
            output=None,
            error_handler=ErrorHandler(),
            output_mode="text_only",
            on_text_ready=lambda r: ready_records.append(r),
            on_utterance_done=lambda r: done_records.append(r),
            read_timeout=0.01,
        )
        coord.start(capture_source_id="sine", output_device_id="out")
        assert _wait_until(lambda: len(ready_records) >= 1, timeout=3.0)
        coord.stop()

        # text_only では on_utterance_done は呼ばれない
        assert done_records == []
        # on_text_ready で record が届く(seq_id / src_text / tgt_text が入る)
        rec = ready_records[0]
        assert "seq_id" in rec
        assert rec["src_text"].startswith("text(")
        assert rec["tgt_text"].endswith("->ja")
        # timeline には t_translate までしか入らない(t_tts / t_playback は無い)
        tl = rec.get("timeline", {})
        assert "t_translate" in tl
        assert "t_tts" not in tl
        assert "t_playback" not in tl

    def test_text_only_does_not_invoke_tts_or_output(self) -> None:
        """text_only モードで _SpyTts/_SpyOutput を渡しても呼ばれない(防衛: スレッド未起動)。"""
        tts = _SpyTts()
        output = _SpyOutput()
        ready_records: list[dict] = []
        coord = PipelineCoordinator(
            capture=_SineCapture(_make_pcm(2.0)),
            vad=_VadEveryN(every_n=3),
            asr=_EchoAsr(),
            translator=_SuffixTranslator(),
            tts=tts,
            output=output,
            error_handler=ErrorHandler(),
            output_mode="text_only",
            on_text_ready=lambda r: ready_records.append(r),
            read_timeout=0.01,
        )
        coord.start(capture_source_id="sine", output_device_id="out")
        assert _wait_until(lambda: len(ready_records) >= 1, timeout=3.0)
        coord.stop()
        # スレッドが起動していないので呼ばれていない
        assert tts.calls == 0
        assert output.calls == 0

    def test_text_only_ledger_drained_after_translator(self) -> None:
        """Translator 完了直後にレジャから当該 seq_id が pop されている(バッファ即解放)。"""
        ready_records: list[dict] = []
        coord = PipelineCoordinator(
            capture=_SineCapture(_make_pcm(2.0)),
            vad=_VadEveryN(every_n=3),
            asr=_EchoAsr(),
            translator=_SuffixTranslator(),
            tts=None,
            output=None,
            error_handler=ErrorHandler(),
            output_mode="text_only",
            on_text_ready=lambda r: ready_records.append(r),
            read_timeout=0.01,
        )
        coord.start(capture_source_id="sine", output_device_id="out")
        # 数件 done するまで待つ
        assert _wait_until(lambda: len(ready_records) >= 3, timeout=3.0)
        coord.stop()

        # ready した seq_id はレジャに残っていない(peek で空 dict が返る)
        ledger = coord.ledger
        for rec in ready_records:
            seq = rec["seq_id"]
            assert ledger.peek(seq) == {}, (
                f"seq={seq} がレジャに残存。pop されていない可能性"
            )

    def test_text_only_does_not_use_translated_or_synthesized_queue(self) -> None:
        """text_only モードでは translated_queue / synthesized_queue が空のまま終わる。"""
        ready_records: list[dict] = []
        coord = PipelineCoordinator(
            capture=_SineCapture(_make_pcm(2.0)),
            vad=_VadEveryN(every_n=3),
            asr=_EchoAsr(),
            translator=_SuffixTranslator(),
            tts=None,
            output=None,
            error_handler=ErrorHandler(),
            output_mode="text_only",
            on_text_ready=lambda r: ready_records.append(r),
            read_timeout=0.01,
        )
        coord.start(capture_source_id="sine", output_device_id="out")
        assert _wait_until(lambda: len(ready_records) >= 3, timeout=3.0)
        # ステージ間キュー(translated / synthesized) は触られていない
        assert coord._translated_queue.qsize() == 0  # noqa: SLF001
        assert coord._synthesized_queue.qsize() == 0  # noqa: SLF001
        coord.stop()


# ============================================================
# モード切替時のバッファ クリーン
# ============================================================
class TestCoordinatorModeSwitchBuffers:
    """設定変更で text_only ↔ audio を切り替えるシナリオでの restart 安全性。

    Coordinator は output_mode を __init__ で受け取る。実運用では
    AppController が mode に応じて新しい Coordinator を作るので、ここでは
    新 Coordinator を作って start するシナリオを再現する。
    """

    def test_audio_to_text_only_no_leak(self) -> None:
        """audio で 1 サイクル → stop → 同じ backend で text_only Coordinator を新規 → 残骸なし。"""
        capture = _SineCapture(_make_pcm(1.5))
        tts = _SpyTts()
        output = _SpyOutput()
        # audio モード
        coord_a = PipelineCoordinator(
            capture=capture,
            vad=_VadEveryN(every_n=3),
            asr=_EchoAsr(),
            translator=_SuffixTranslator(),
            tts=tts, output=output,
            error_handler=ErrorHandler(),
            output_mode="audio",
            read_timeout=0.01,
        )
        done_a: list[dict] = []
        coord_a._on_utterance_done = lambda r: done_a.append(r)  # noqa: SLF001
        coord_a.start(capture_source_id="sine", output_device_id="out")
        assert _wait_until(lambda: len(done_a) >= 1, timeout=3.0)
        coord_a.stop()

        # text_only モードの Coordinator を新規。capture/asr/translator は使い回しでも OK
        capture2 = _SineCapture(_make_pcm(1.5))
        coord_b = PipelineCoordinator(
            capture=capture2,
            vad=_VadEveryN(every_n=3),
            asr=_EchoAsr(),
            translator=_SuffixTranslator(),
            tts=None, output=None,
            error_handler=ErrorHandler(),
            output_mode="text_only",
            read_timeout=0.01,
        )
        ready_b: list[dict] = []
        coord_b._on_text_ready = lambda r: ready_b.append(r)  # noqa: SLF001
        coord_b.start(capture_source_id="sine", output_device_id="out")
        assert _wait_until(lambda: len(ready_b) >= 1, timeout=3.0)
        coord_b.stop()
        # コーディネータ B のキューは空、レジャも空(ready した seq は pop 済み)
        assert coord_b._translated_queue.qsize() == 0  # noqa: SLF001
        assert coord_b._synthesized_queue.qsize() == 0  # noqa: SLF001
        for rec in ready_b:
            assert coord_b.ledger.peek(rec["seq_id"]) == {}

    def test_restart_drains_old_queues(self) -> None:
        """同じ Coordinator(text_only) を 2 回 start しても残骸が残らない。

        実運用では Coordinator は使い捨てだが、stop → start を同一インスタンスで
        行っても drain が走ることをここで担保(バッファ処理回りの安全弁)。
        """
        capture = _SineCapture(_make_pcm(1.5))
        coord = PipelineCoordinator(
            capture=capture,
            vad=_VadEveryN(every_n=3),
            asr=_EchoAsr(),
            translator=_SuffixTranslator(),
            tts=None, output=None,
            error_handler=ErrorHandler(),
            output_mode="text_only",
            read_timeout=0.01,
        )
        ready: list[dict] = []
        coord._on_text_ready = lambda r: ready.append(r)  # noqa: SLF001

        # 1 周目
        coord.start(capture_source_id="sine", output_device_id="out")
        assert _wait_until(lambda: len(ready) >= 1, timeout=3.0)
        coord.stop()
        # 2 周目: capture の pos リセット + pcm 補給
        capture._pcm = _make_pcm(1.5)  # noqa: SLF001
        capture._pos = 0  # noqa: SLF001
        before = len(ready)
        coord.start(capture_source_id="sine", output_device_id="out")
        assert _wait_until(lambda: len(ready) > before, timeout=3.0)
        coord.stop()
        # 2 周目で start 時点の drain がうまく動いた = ledger leak 無し
        assert coord._translated_queue.qsize() == 0  # noqa: SLF001
        assert coord._synthesized_queue.qsize() == 0  # noqa: SLF001


# ============================================================
# audio モード回帰(text_only 機能追加で壊れていないこと)
# ============================================================
class TestCoordinatorAudioRegression:
    def test_audio_mode_still_works(self) -> None:
        """audio モード(既定)では従来通り Output まで通る。"""
        capture = _SineCapture(_make_pcm(2.0))
        tts = _SpyTts()
        output = _SpyOutput()
        done_records: list[dict] = []
        coord = PipelineCoordinator(
            capture=capture,
            vad=_VadEveryN(every_n=3),
            asr=_EchoAsr(),
            translator=_SuffixTranslator(),
            tts=tts, output=output,
            error_handler=ErrorHandler(),
            output_mode="audio",
            on_utterance_done=lambda r: done_records.append(r),
            read_timeout=0.01,
        )
        coord.start(capture_source_id="sine", output_device_id="out")
        assert _wait_until(lambda: len(done_records) >= 1, timeout=3.0)
        coord.stop()
        assert tts.calls > 0
        assert output.calls > 0
        # tt_playback まで含まれる
        for r in done_records:
            tl = r.get("timeline", {})
            assert "t_playback" in tl


# ============================================================
# AppController: output_mode / _active_layers
# ============================================================
class TestAppControllerOutputMode:
    def _make_controller(self, mode: str) -> AppController:
        from voice_translator.common.backend_registry import BackendRegistry

        config = ConfigStore(path="dummy", data={"pipeline": {"output_mode": mode}})
        registry = BackendRegistry()
        return AppController(registry=registry, config=config)

    def test_output_mode_default_is_audio(self) -> None:
        from voice_translator.common.backend_registry import BackendRegistry

        config = ConfigStore(path="dummy", data={})
        ctrl = AppController(registry=BackendRegistry(), config=config)
        assert ctrl.output_mode == "audio"

    def test_output_mode_text_only(self) -> None:
        ctrl = self._make_controller("text_only")
        assert ctrl.output_mode == "text_only"

    def test_output_mode_unknown_falls_back_to_audio(self) -> None:
        ctrl = self._make_controller("bogus")
        assert ctrl.output_mode == "audio"

    def test_active_layers_audio_has_all(self) -> None:
        ctrl = self._make_controller("audio")
        assert set(ctrl._active_layers()) == set(LayerKind)  # noqa: SLF001

    def test_active_layers_text_only_excludes_tts_output(self) -> None:
        ctrl = self._make_controller("text_only")
        active = set(ctrl._active_layers())  # noqa: SLF001
        assert LayerKind.TTS not in active
        assert LayerKind.OUTPUT not in active
        assert LayerKind.CAPTURE in active
        assert LayerKind.VAD in active
        assert LayerKind.ASR in active
        assert LayerKind.TRANSLATOR in active


# ============================================================
# AppController: _handle_text_ready の jsonl / processtime 書き出し
# ============================================================
class TestAppControllerHandleTextReady:
    def _make_ctrl_with_logs(self, mode: str):
        from voice_translator.common.backend_registry import BackendRegistry

        config = ConfigStore(path="dummy", data={"pipeline": {"output_mode": mode}})
        ctrl = AppController(registry=BackendRegistry(), config=config)
        ctrl._translation_logger = MagicMock()  # noqa: SLF001
        ctrl._process_time_logger = MagicMock()  # noqa: SLF001
        ui_records: list[dict] = []
        ctrl._on_text_ready = lambda r: ui_records.append(r)  # noqa: SLF001
        return ctrl, ui_records

    def test_text_only_writes_logs(self) -> None:
        ctrl, ui = self._make_ctrl_with_logs("text_only")
        record = {
            "seq_id": 1,
            "timeline": {"t_capture": 0.0, "t_vad_end": 0.1, "t_translate": 0.5},
            "src_text": "hi", "src_lang": "en",
            "tgt_text": "やぁ", "tgt_lang": "ja",
        }
        ctrl._handle_text_ready(record)  # noqa: SLF001
        # text_only モード: 最終扱いで jsonl / processtime に書く
        ctrl._translation_logger.write_record.assert_called_once_with(record)  # noqa: SLF001
        ctrl._process_time_logger.write_record.assert_called_once_with(record)  # noqa: SLF001
        # UI 通知も呼ばれる
        assert ui == [record]

    def test_audio_does_not_write_logs_in_text_ready(self) -> None:
        """audio モードでは _handle_text_ready は UI 通知のみ。jsonl 等は _handle_utterance_done で書く。"""
        ctrl, ui = self._make_ctrl_with_logs("audio")
        record = {
            "seq_id": 1,
            "timeline": {"t_capture": 0.0, "t_translate": 0.5},
            "src_text": "hi", "tgt_text": "やぁ",
        }
        ctrl._handle_text_ready(record)  # noqa: SLF001
        ctrl._translation_logger.write_record.assert_not_called()  # noqa: SLF001
        ctrl._process_time_logger.write_record.assert_not_called()  # noqa: SLF001
        # UI 通知だけ
        assert ui == [record]

    def test_text_only_log_failure_does_not_break_ui_notify(self) -> None:
        """jsonl / processtime 書き出しが例外でも UI 通知は呼ばれる。"""
        ctrl, ui = self._make_ctrl_with_logs("text_only")
        ctrl._translation_logger.write_record.side_effect = RuntimeError("disk full")  # noqa: SLF001
        ctrl._process_time_logger.write_record.side_effect = RuntimeError("csv error")  # noqa: SLF001
        record = {"seq_id": 1, "timeline": {}, "src_text": "a", "tgt_text": "b"}
        ctrl._handle_text_ready(record)  # noqa: SLF001
        # UI 通知だけは届く
        assert ui == [record]


# ============================================================
# ConfigStore のデフォルト
# ============================================================
class TestConfigStoreDefault:
    def test_pipeline_output_mode_default_is_audio(self) -> None:
        assert DEFAULT_CONFIG["pipeline"]["output_mode"] == "audio"

    def test_loaded_config_keeps_user_value(self, tmp_path) -> None:
        """ユーザが output_mode=text_only を保存していたら、load 後も text_only。"""
        import yaml

        path = tmp_path / "config.yaml"
        path.write_text(
            yaml.safe_dump({"pipeline": {"output_mode": "text_only"}}),
            encoding="utf-8",
        )
        store = ConfigStore(path=path)
        store.load()
        assert store.get("pipeline", "output_mode") == "text_only"
