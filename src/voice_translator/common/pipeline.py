"""PipelineCoordinator: 3スレッド版(Input / Process / Output)。

役割: 取得→VAD を Input スレッド、ASR→翻訳→TTS を Process スレッド、
再生を Output スレッドで動かす。UIスレッドは一切ブロックしない。
発話は2本のキュー(q1: Input→Process, q2: Process→Output)で受け渡す。
キューがあふれた場合は古いものから捨てる(リアルタイム性を優先)。
"""

from __future__ import annotations

import logging
import queue
import threading
from typing import Callable

from voice_translator.asr.backend import AsrBackend
from voice_translator.capture.backend import AudioCaptureBackend
from voice_translator.output.backend import AudioOutputBackend
from voice_translator.translator.backend import TranslatorBackend
from voice_translator.tts.backend import TtsBackend
from voice_translator.vad.backend import VadBackend

from .error_handler import ErrorAction, ErrorHandler
from .utterance import Utterance

# 停止シグナル代わりにキューへ流すセンチネル値
_SENTINEL: object = object()


class PipelineCoordinator:
    """3スレッド構成のパイプライン制御。

    役割: 各レイヤをスレッド分離して動かし、UI/取得/処理/再生を独立させる。
    キューあふれ時は最古を捨てて新しい発話を優先(リアルタイム性確保)。
    """

    def __init__(
        self,
        *,
        capture: AudioCaptureBackend,
        vad: VadBackend,
        asr: AsrBackend,
        translator: TranslatorBackend,
        tts: TtsBackend,
        output: AudioOutputBackend,
        error_handler: ErrorHandler,
        src_lang: str = "auto",
        tgt_lang: str = "ja",
        on_utterance_done: Callable[[Utterance], None] | None = None,
        read_timeout: float = 0.1,
        queue_size: int = 3,
        logger: logging.Logger | None = None,
    ) -> None:
        self._capture = capture
        self._vad = vad
        self._asr = asr
        self._translator = translator
        self._tts = tts
        self._output = output
        self._error_handler = error_handler
        self._src_lang = src_lang
        self._tgt_lang = tgt_lang
        self._on_utterance_done = on_utterance_done
        self._read_timeout = read_timeout
        self._logger = logger or logging.getLogger("voice_translator")

        # キュー(Utterance または _SENTINEL を流す)
        self._q1: queue.Queue = queue.Queue(maxsize=queue_size)
        self._q2: queue.Queue = queue.Queue(maxsize=queue_size)

        # overflow 累計(stage名 → 累積ドロップ数)
        self._drop_counts: dict[str, int] = {}

        # スレッド + 停止フラグ
        self._stop_event = threading.Event()
        self._input_thread: threading.Thread | None = None
        self._process_thread: threading.Thread | None = None
        self._output_thread: threading.Thread | None = None

    # ============================================================
    @property
    def is_running(self) -> bool:
        """スレッドのいずれかが動作中なら True。"""
        for t in (self._input_thread, self._process_thread, self._output_thread):
            if t is not None and t.is_alive():
                return True
        return False

    def start(self, *, capture_source_id: str, output_device_id: str) -> None:
        """3スレッドを起動。既に動作中なら RuntimeError。"""
        if self.is_running:
            raise RuntimeError("PipelineCoordinator は既に動作中です")

        self._stop_event.clear()
        # キュー残骸をクリア
        self._drain_queue(self._q1)
        self._drain_queue(self._q2)

        self._capture.start(capture_source_id)
        self._output.start(output_device_id)
        self._vad.reset()

        self._input_thread = threading.Thread(
            target=self._input_loop, name="vt_input", daemon=True
        )
        self._process_thread = threading.Thread(
            target=self._process_loop, name="vt_process", daemon=True
        )
        self._output_thread = threading.Thread(
            target=self._output_loop, name="vt_output", daemon=True
        )
        self._input_thread.start()
        self._process_thread.start()
        self._output_thread.start()

    def stop(self, *, join_timeout: float = 2.0) -> None:
        """停止: stop_event を立てる → Input → Process → Output の順に join。"""
        self._stop_event.set()

        # Input を待つ(read_chunk の戻りで終わる)
        self._join_quietly(self._input_thread, join_timeout)
        self._input_thread = None

        # Process を起こすためセンチネル投入
        self._try_put_sentinel(self._q1)
        self._join_quietly(self._process_thread, join_timeout)
        self._process_thread = None

        # Output を起こすためセンチネル投入
        self._try_put_sentinel(self._q2)
        self._join_quietly(self._output_thread, join_timeout)
        self._output_thread = None

        # バックエンドの片付け(失敗は握りつぶす)
        try:
            self._capture.stop()
        except Exception:  # noqa: BLE001
            pass
        try:
            self._output.stop()
        except Exception:  # noqa: BLE001
            pass

    # ============================================================
    # スレッド本体
    # ============================================================
    def _input_loop(self) -> None:
        """capture.read_chunk → vad.process → q1 へ Utterance を流す。"""
        while not self._stop_event.is_set():
            try:
                chunk = self._capture.read_chunk(timeout=self._read_timeout)
            except Exception as exc:  # noqa: BLE001
                if self._dispatch_error(exc) == ErrorAction.STOP:
                    break
                continue

            if chunk is None:
                continue

            try:
                utterances = self._vad.process(chunk)
            except Exception as exc:  # noqa: BLE001
                if self._dispatch_error(exc) == ErrorAction.STOP:
                    break
                continue

            for utt in utterances:
                if self._stop_event.is_set():
                    break
                self._put_with_drop(self._q1, utt, "q1(Input→Process)")

    def _process_loop(self) -> None:
        """q1 から Utterance を取り、ASR→翻訳→TTS して q2 へ渡す。"""
        while not self._stop_event.is_set():
            try:
                item = self._q1.get(timeout=self._read_timeout)
            except queue.Empty:
                continue
            if item is _SENTINEL:
                break

            utt: Utterance = item
            utt.src_lang = self._src_lang or utt.src_lang
            utt.tgt_lang = self._tgt_lang

            try:
                self._asr.transcribe(utt, self._src_lang)
                utt.timeline.mark("t_asr")

                self._translator.translate(utt, self._tgt_lang)
                utt.timeline.mark("t_translate")

                self._tts.synthesize(utt)
                utt.timeline.mark("t_tts")
            except Exception as exc:  # noqa: BLE001
                action = self._dispatch_error(exc)
                if action == ErrorAction.STOP:
                    self._stop_event.set()
                    break
                continue  # SKIP/CONTINUE/RETRY: 当該発話は破棄して継続

            self._put_with_drop(self._q2, utt, "q2(Process→Output)")

    def _output_loop(self) -> None:
        """q2 から Utterance を取り、再生 + UI 通知。"""
        while not self._stop_event.is_set():
            try:
                item = self._q2.get(timeout=self._read_timeout)
            except queue.Empty:
                continue
            if item is _SENTINEL:
                break

            utt: Utterance = item
            try:
                self._output.play(utt)
                utt.timeline.mark("t_playback")
            except Exception as exc:  # noqa: BLE001
                action = self._dispatch_error(exc)
                if action == ErrorAction.STOP:
                    self._stop_event.set()
                    break
                continue

            if self._on_utterance_done is not None:
                try:
                    self._on_utterance_done(utt)
                except Exception:  # noqa: BLE001
                    # UI 通知の失敗で停止させない
                    pass

    # ============================================================
    # ユーティリティ
    # ============================================================
    def _dispatch_error(self, exc: BaseException) -> str:
        return self._error_handler.handle(exc)

    @staticmethod
    def _join_quietly(thread: threading.Thread | None, timeout: float) -> None:
        """スレッドを join。None や未起動なら何もしない。"""
        if thread is None:
            return
        if thread.is_alive():
            thread.join(timeout=timeout)

    @staticmethod
    def _drain_queue(q: queue.Queue) -> None:
        """キューの残骸を捨てる(start 直前に呼ぶ)。"""
        try:
            while True:
                q.get_nowait()
        except queue.Empty:
            pass

    @staticmethod
    def _try_put_sentinel(q: queue.Queue) -> None:
        """キューにセンチネルを入れる。満杯なら 1つ捨ててから入れる。"""
        try:
            q.put_nowait(_SENTINEL)
        except queue.Full:
            try:
                q.get_nowait()
            except queue.Empty:
                pass
            try:
                q.put_nowait(_SENTINEL)
            except queue.Full:
                pass

    def _put_with_drop(self, q: queue.Queue, item: object, stage_name: str) -> None:
        """満杯なら古いものから捨てて新しいものを入れる(リアルタイム性優先)。

        捨てたら WARN ログを出して累計を更新する(案C: 完全並列化が必要かの判断材料)。
        """
        dropped = 0
        while True:
            try:
                q.put_nowait(item)
                if dropped > 0:
                    total = self._drop_counts.get(stage_name, 0) + dropped
                    self._drop_counts[stage_name] = total
                    self._logger.warning(
                        "queue overflow in %s: dropped %d utterance(s), total=%d",
                        stage_name, dropped, total,
                    )
                return
            except queue.Full:
                try:
                    q.get_nowait()
                    dropped += 1
                except queue.Empty:
                    pass

    def get_drop_counts(self) -> dict[str, int]:
        """ステージ別の累計ドロップ件数のコピーを返す(診断用)。"""
        return dict(self._drop_counts)
