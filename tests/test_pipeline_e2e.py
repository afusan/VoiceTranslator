"""パイプライン縦通しの E2E テスト(モックの ML バックエンド使用)。

役割: WavReplayCapture から WAV を流し、PipelineCoordinator(5スレッド版)が
VAD → ASR → 翻訳 → TTS → 出力 を順に呼ぶことを再現可能に検証する。
実モデルを使わないため CI/ローカルで安定して動く。
"""

from __future__ import annotations

import time
from pathlib import Path
from time import monotonic
from typing import Callable

import numpy as np

from voice_translator.asr.backend import AsrBackend
from voice_translator.common.error_handler import ErrorHandler
from voice_translator.common.pipeline import PipelineCoordinator
from voice_translator.common.types import OutputDevice, PcmChunk
from voice_translator.output.backend import AudioOutputBackend
from voice_translator.translator.backend import TranslatorBackend
from voice_translator.tts.backend import TtsBackend
from voice_translator.vad.backend import VadBackend, VadSegment

from ._fixtures import WavReplayCapture


# ============================================================
# 軽量モック
# ============================================================
class VadEveryN(VadBackend):
    """N ウィンドウぶん受け取ったら 1発話確定するVAD。テスト用。"""

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


class EchoAsr(AsrBackend):
    def transcribe(self, pcm, src_lang_hint: str = "auto") -> tuple[str, str]:
        return f"text({pcm.shape[0]} samples)", "en"


class SuffixTranslator(TranslatorBackend):
    def translate(self, src_text: str, src_lang: str, tgt_lang: str) -> str:
        return f"{src_text} -> {tgt_lang}"


class SilentTts(TtsBackend):
    def synthesize(self, text: str, tgt_lang: str) -> tuple[np.ndarray, int]:
        return np.zeros(160, dtype=np.float32), 16000


class RecordingOutput(AudioOutputBackend):
    def __init__(self) -> None:
        self.played: list[tuple] = []
        self._started = False

    def list_devices(self) -> list[OutputDevice]:
        return [OutputDevice("dummy_out", "Dummy")]

    def start(self, device_id: str) -> None:
        self._started = True

    def play(self, pcm, samplerate: int) -> None:
        self.played.append((pcm, samplerate))

    def stop(self) -> None:
        self._started = False


# ============================================================
def _wait_until(predicate: Callable[[], bool], timeout: float = 3.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return False


# ============================================================
class TestPipelineE2EWithSynthPcm:
    def test_pipeline_processes_wav_pcm_through_all_stages(self, tmp_path: Path) -> None:
        # 1秒ぶんの簡単なサイン波(16kHz mono float32)
        t = np.linspace(0, 1.0, 16000, endpoint=False)
        pcm = (0.1 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)

        capture = WavReplayCapture(pcm, chunk_size=512)
        output = RecordingOutput()
        done_records: list[dict] = []

        coord = PipelineCoordinator(
            capture=capture,
            vad=VadEveryN(every_n=3),
            asr=EchoAsr(),
            translator=SuffixTranslator(),
            tts=SilentTts(),
            output=output,
            error_handler=ErrorHandler(),
            src_lang="en",
            tgt_lang="ja",
            on_utterance_done=lambda r: done_records.append(r),
            read_timeout=0.01,
            q_raw_size=100, q_tr_size=100, q_xl_size=100, q_syn_size=100,
        )

        coord.start(capture_source_id="wav_replay", output_device_id="dummy_out")
        assert _wait_until(lambda: len(output.played) >= 5, timeout=3.0)
        coord.stop()

        assert len(output.played) > 0
        assert len(done_records) > 0
        for r in done_records:
            assert r["src_text"].startswith("text(")
            assert r["tgt_text"].endswith("-> ja")
            for key in ("t_capture", "t_vad_end", "t_asr", "t_translate", "t_tts", "t_playback"):
                assert key in r["timeline"], f"{key} がタイムラインに記録されていない"

    def test_pipeline_loads_real_wav_file(self, tmp_path: Path) -> None:
        """WAV ファイルを書き出して、from_wav() で読み込んで流す。"""
        import wave

        # 0.5秒、16kHz mono int16 のサイン波
        sr = 16000
        t = np.linspace(0, 0.5, sr // 2, endpoint=False)
        samples = (0.3 * np.sin(2 * np.pi * 220 * t) * 32767).astype(np.int16)

        wav_path = tmp_path / "test.wav"
        with wave.open(str(wav_path), "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(sr)
            wf.writeframes(samples.tobytes())

        capture = WavReplayCapture.from_wav(wav_path, chunk_size=256)
        output = RecordingOutput()

        coord = PipelineCoordinator(
            capture=capture,
            vad=VadEveryN(every_n=2),
            asr=EchoAsr(),
            translator=SuffixTranslator(),
            tts=SilentTts(),
            output=output,
            error_handler=ErrorHandler(),
            src_lang="en",
            tgt_lang="ja",
            read_timeout=0.01,
            q_raw_size=100, q_tr_size=100, q_xl_size=100, q_syn_size=100,
        )
        coord.start(capture_source_id="wav_replay", output_device_id="dummy_out")
        assert _wait_until(lambda: len(output.played) >= 1, timeout=3.0)
        coord.stop()
        assert len(output.played) > 0
