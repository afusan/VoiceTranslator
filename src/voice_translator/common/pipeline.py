"""PipelineCoordinator: 5スレッド版(Input / ASR / Translator / TTS / Output)。

役割: ASR・翻訳・TTS を独立スレッド化して直列ボトルネックを解消する。
各ステージは次段に必要な最小ペイロード(`PipelineMessage` + 各 `*Payload`)だけを渡し、
横断メタ(timeline / 各種テキスト / 言語等)は `UtteranceLedger` に seq_id をキーに集約する。

スレッド/キュー構成:
- Input    --(captured_queue)-->  ASR        (PCM、バイト基準 ByteBoundedQueue)
- ASR      --(recognized_queue)--> Translator (テキスト、件数基準 queue.Queue)
- Translator -(translated_queue)-> TTS        (テキスト、件数基準 queue.Queue)
- TTS      --(synthesized_queue)-> Output    (PCM、バイト基準 ByteBoundedQueue)

キューがあふれた場合は古いものから捨てる(リアルタイム性を優先)。PCM 系はバイト数で
制限し、設定値を少し超える前提で「push してから超過分を退避」する。
"""

from __future__ import annotations

import logging
import queue
import threading
from typing import Callable, Union

from voice_translator.asr.backend import AsrBackend
from voice_translator.capture.backend import AudioCaptureBackend
from voice_translator.output.backend import AudioOutputBackend
from voice_translator.translator.backend import TranslatorBackend
from voice_translator.tts.backend import TtsBackend
from voice_translator.vad.backend import VadBackend

from .bounded_queue import ByteBoundedQueue
from .error_handler import ErrorAction, ErrorHandler
from .ledger import UtteranceLedger
from .logger import TextLogger
from .messages import (
    PipelineMessage,
    RawPayload,
    SynthesizedPayload,
    TranscribedPayload,
    TranslatedPayload,
)
from .sequence import SequenceGenerator
from .stage_dump import NullStageDumpWriter, StageDumpWriter
from .types import INTERNAL_SAMPLE_RATE

# 停止シグナル代わりにキューへ流すセンチネル値
_SENTINEL: object = object()

# 両方のキュー実装をまとめて指すエイリアス
_StageQueue = Union[queue.Queue, ByteBoundedQueue]


def _pcm_message_bytes(item: object) -> int:
    """PipelineMessage 内の PCM バイト数を返す。非該当(センチネル等)は 0。

    captured_queue / synthesized_queue 用の size_of(`ByteBoundedQueue`)。
    """
    if not isinstance(item, PipelineMessage):
        return 0
    payload = item.payload
    if isinstance(payload, RawPayload):
        arr = payload.pcm
        return int(getattr(arr, "nbytes", 0))
    if isinstance(payload, SynthesizedPayload):
        arr = payload.tts_pcm
        nbytes = getattr(arr, "nbytes", None)
        if nbytes is not None:
            return int(nbytes)
        try:
            return len(arr)  # bytes/bytearray 互換
        except TypeError:
            return 0
    return 0


class PipelineCoordinator:
    """5スレッド構成のパイプライン制御。

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
        ledger: UtteranceLedger | None = None,
        sequence: SequenceGenerator | None = None,
        text_logger: TextLogger | None = None,
        src_lang: str = "auto",
        tgt_lang: str = "ja",
        on_utterance_done: Callable[[dict], None] | None = None,
        on_dropped: Callable[[list[int], str], None] | None = None,
        read_timeout: float = 0.1,
        captured_queue_max_bytes: int = 10_000_000,
        synthesized_queue_max_bytes: int = 5_000_000,
        recognized_queue_size: int = 10,
        translated_queue_size: int = 10,
        logger: logging.Logger | None = None,
        dump: StageDumpWriter | NullStageDumpWriter | None = None,
    ) -> None:
        self._capture = capture
        self._vad = vad
        self._asr = asr
        self._translator = translator
        self._tts = tts
        self._output = output
        self._error_handler = error_handler
        self._ledger = ledger if ledger is not None else UtteranceLedger()
        self._sequence = sequence if sequence is not None else SequenceGenerator()
        self._text_logger = text_logger
        self._src_lang = src_lang
        self._tgt_lang = tgt_lang
        self._on_utterance_done = on_utterance_done
        self._on_dropped = on_dropped
        self._read_timeout = read_timeout
        self._logger = logger or logging.getLogger("voice_translator")
        # ステージ間データのダンプフック。無効時は no-op の NullStageDumpWriter。
        # ライフサイクル(start_run/stop_run)は呼び出し側(AppController)で管理する。
        self._dump: StageDumpWriter | NullStageDumpWriter = (
            dump if dump is not None else NullStageDumpWriter()
        )

        # キュー(PipelineMessage または _SENTINEL を流す)
        # PCM 系はバイト基準(ByteBoundedQueue) / テキスト系は件数基準(queue.Queue)。
        self._captured_queue: _StageQueue = ByteBoundedQueue(
            max_bytes=captured_queue_max_bytes, size_of=_pcm_message_bytes
        )
        self._recognized_queue: _StageQueue = queue.Queue(maxsize=recognized_queue_size)
        self._translated_queue: _StageQueue = queue.Queue(maxsize=translated_queue_size)
        self._synthesized_queue: _StageQueue = ByteBoundedQueue(
            max_bytes=synthesized_queue_max_bytes, size_of=_pcm_message_bytes
        )

        # overflow 累計(stage名 → 累積ドロップ数)
        self._drop_counts: dict[str, int] = {}

        # スレッド + 停止フラグ
        self._stop_event = threading.Event()
        self._input_thread: threading.Thread | None = None
        self._asr_thread: threading.Thread | None = None
        self._translator_thread: threading.Thread | None = None
        self._tts_thread: threading.Thread | None = None
        self._output_thread: threading.Thread | None = None

    # ============================================================
    @property
    def is_running(self) -> bool:
        """スレッドのいずれかが動作中なら True。"""
        for t in (
            self._input_thread,
            self._asr_thread,
            self._translator_thread,
            self._tts_thread,
            self._output_thread,
        ):
            if t is not None and t.is_alive():
                return True
        return False

    @property
    def ledger(self) -> UtteranceLedger:
        """中央レジャ(テスト・診断用)。"""
        return self._ledger

    @property
    def sequence(self) -> SequenceGenerator:
        """seq_id 発行器(テスト・診断用)。"""
        return self._sequence

    def start(self, *, capture_source_id: str, output_device_id: str) -> None:
        """5スレッドを起動。既に動作中なら RuntimeError。"""
        if self.is_running:
            raise RuntimeError("PipelineCoordinator は既に動作中です")

        self._stop_event.clear()
        # キュー + ledger 残骸をクリア
        for q in (self._captured_queue, self._recognized_queue, self._translated_queue, self._synthesized_queue):
            self._drain_queue(q)
        self._ledger.clear()

        self._capture.start(capture_source_id)
        self._output.start(output_device_id)
        self._vad.reset()

        self._input_thread = threading.Thread(
            target=self._input_loop, name="vt_input", daemon=True
        )
        self._asr_thread = threading.Thread(
            target=self._asr_loop, name="vt_asr", daemon=True
        )
        self._translator_thread = threading.Thread(
            target=self._translator_loop, name="vt_translator", daemon=True
        )
        self._tts_thread = threading.Thread(
            target=self._tts_loop, name="vt_tts", daemon=True
        )
        self._output_thread = threading.Thread(
            target=self._output_loop, name="vt_output", daemon=True
        )
        for t in (
            self._input_thread,
            self._asr_thread,
            self._translator_thread,
            self._tts_thread,
            self._output_thread,
        ):
            t.start()

    def stop(self, *, join_timeout: float = 2.0) -> None:
        """停止: stop_event を立てる → 各スレッドを上流から順に終了させる。"""
        self._stop_event.set()

        # Input を待つ(read_chunk の戻りで終わる)
        self._join_quietly(self._input_thread, join_timeout)
        self._input_thread = None

        # 各処理スレッドにセンチネルを投入して順次 join
        for q, thread_attr in (
            (self._captured_queue, "_asr_thread"),
            (self._recognized_queue, "_translator_thread"),
            (self._translated_queue, "_tts_thread"),
            (self._synthesized_queue, "_output_thread"),
        ):
            self._try_put_sentinel(q)
            thread = getattr(self, thread_attr)
            self._join_quietly(thread, join_timeout)
            setattr(self, thread_attr, None)

        # バックエンドの片付け(失敗は握りつぶす)
        try:
            self._capture.stop()
        except Exception:  # noqa: BLE001
            pass
        try:
            self._output.stop()
        except Exception:  # noqa: BLE001
            pass

    def get_drop_counts(self) -> dict[str, int]:
        """ステージ別の累計ドロップ件数のコピーを返す(診断用)。"""
        return dict(self._drop_counts)

    # ============================================================
    # スレッド本体
    # ============================================================
    def _input_loop(self) -> None:
        """capture.read_chunk → vad.process → captured_queue へ Raw メッセージを流す。"""
        while not self._stop_event.is_set():
            try:
                chunk = self._capture.read_chunk(timeout=self._read_timeout)
            except Exception as exc:  # noqa: BLE001
                if self._dispatch_error(exc, stage="Capture") == ErrorAction.STOP:
                    # FATAL: 他スレッドにも停止を伝える(自分だけ break すると他が回り続ける)
                    self._stop_event.set()
                    break
                continue

            if chunk is None:
                continue

            try:
                segments = self._vad.process(chunk)
            except Exception as exc:  # noqa: BLE001
                if self._dispatch_error(exc, stage="VAD") == ErrorAction.STOP:
                    self._stop_event.set()
                    break
                continue

            for seg in segments:
                if self._stop_event.is_set():
                    break
                seq_id = self._sequence.next()
                self._ledger.init(seq_id)
                # 発話開始時刻を t_capture として正確に記録
                self._ledger.record(
                    seq_id,
                    timeline={"t_capture": seg.started_at_monotonic},
                )
                self._ledger.mark_time(seq_id, "t_vad_end")
                self._dump.on_vad(seq_id, seg.pcm, INTERNAL_SAMPLE_RATE)
                msg = PipelineMessage(
                    seq_id=seq_id,
                    payload=RawPayload(pcm=seg.pcm, src_lang_hint=self._src_lang),
                )
                self._put_with_drop(self._captured_queue, msg, "captured_queue(Input→ASR)")

    def _asr_loop(self) -> None:
        """captured_queue → ASR → recognized_queue。"""
        while not self._stop_event.is_set():
            try:
                item = self._captured_queue.get(timeout=self._read_timeout)
            except queue.Empty:
                continue
            if item is _SENTINEL:
                break

            msg: PipelineMessage = item
            payload: RawPayload = msg.payload
            # t_asr_start: backend 呼び出しの直前(キュー待ち時間と純処理時間を切り分けるため)
            self._ledger.mark_time(msg.seq_id, "t_asr_start")
            try:
                text, lang = self._asr.transcribe(payload.pcm, payload.src_lang_hint)
            except Exception as exc:  # noqa: BLE001
                action = self._dispatch_error(exc, stage="ASR", seq_id=msg.seq_id)
                if action == ErrorAction.STOP:
                    self._stop_event.set()
                    break
                self._ledger.pop(msg.seq_id)  # 失敗ぶんはレジャから削除
                continue

            # 言語: hint が auto/空でモデルが検出した場合は採用
            src_lang = lang if payload.src_lang_hint in ("auto", "", None) else payload.src_lang_hint

            self._ledger.mark_time(msg.seq_id, "t_asr")
            self._dump.on_asr(msg.seq_id, text, src_lang)
            self._ledger.record(msg.seq_id, src_text=text, src_lang=src_lang)
            if self._text_logger is not None:
                try:
                    self._text_logger.write_src(msg.seq_id, text, src_lang)
                except Exception:  # noqa: BLE001 - テキストログ失敗で停止しない
                    self._logger.exception("write_src failed")

            next_msg = PipelineMessage(
                seq_id=msg.seq_id,
                payload=TranscribedPayload(src_text=text, src_lang=src_lang),
            )
            self._put_with_drop(self._recognized_queue, next_msg, "recognized_queue(ASR→Translator)")

    def _translator_loop(self) -> None:
        """recognized_queue → Translator → translated_queue。"""
        while not self._stop_event.is_set():
            try:
                item = self._recognized_queue.get(timeout=self._read_timeout)
            except queue.Empty:
                continue
            if item is _SENTINEL:
                break

            msg: PipelineMessage = item
            payload: TranscribedPayload = msg.payload
            self._ledger.mark_time(msg.seq_id, "t_translate_start")
            try:
                tgt_text = self._translator.translate(
                    payload.src_text, payload.src_lang, self._tgt_lang
                )
            except Exception as exc:  # noqa: BLE001
                action = self._dispatch_error(exc, stage="Translator", seq_id=msg.seq_id)
                if action == ErrorAction.STOP:
                    self._stop_event.set()
                    break
                self._ledger.pop(msg.seq_id)
                continue

            self._ledger.mark_time(msg.seq_id, "t_translate")
            self._dump.on_translate(
                msg.seq_id,
                payload.src_text,
                payload.src_lang,
                tgt_text,
                self._tgt_lang,
            )
            self._ledger.record(msg.seq_id, tgt_text=tgt_text, tgt_lang=self._tgt_lang)
            if self._text_logger is not None:
                try:
                    self._text_logger.write_tgt(msg.seq_id, tgt_text, self._tgt_lang)
                except Exception:  # noqa: BLE001
                    self._logger.exception("write_tgt failed")

            if not tgt_text:
                # 空翻訳は次段に流す意味がないのでスキップ(レジャは出力で pop されないので
                # ここで pop してリークを防ぐ)
                self._ledger.pop(msg.seq_id)
                continue

            next_msg = PipelineMessage(
                seq_id=msg.seq_id,
                payload=TranslatedPayload(tgt_text=tgt_text, tgt_lang=self._tgt_lang),
            )
            self._put_with_drop(self._translated_queue, next_msg, "translated_queue(Translator→TTS)")

    def _tts_loop(self) -> None:
        """translated_queue → TTS → synthesized_queue。"""
        while not self._stop_event.is_set():
            try:
                item = self._translated_queue.get(timeout=self._read_timeout)
            except queue.Empty:
                continue
            if item is _SENTINEL:
                break

            msg: PipelineMessage = item
            payload: TranslatedPayload = msg.payload
            self._ledger.mark_time(msg.seq_id, "t_tts_start")
            try:
                pcm, samplerate = self._tts.synthesize(payload.tgt_text, payload.tgt_lang)
            except Exception as exc:  # noqa: BLE001
                action = self._dispatch_error(exc, stage="TTS", seq_id=msg.seq_id)
                if action == ErrorAction.STOP:
                    self._stop_event.set()
                    break
                self._ledger.pop(msg.seq_id)
                continue

            self._ledger.mark_time(msg.seq_id, "t_tts")
            self._dump.on_tts(msg.seq_id, pcm, samplerate)
            next_msg = PipelineMessage(
                seq_id=msg.seq_id,
                payload=SynthesizedPayload(tts_pcm=pcm, tts_samplerate=samplerate),
            )
            self._put_with_drop(self._synthesized_queue, next_msg, "synthesized_queue(TTS→Output)")

    def _output_loop(self) -> None:
        """synthesized_queue → Output → ledger.pop → on_utterance_done。"""
        while not self._stop_event.is_set():
            try:
                item = self._synthesized_queue.get(timeout=self._read_timeout)
            except queue.Empty:
                continue
            if item is _SENTINEL:
                break

            msg: PipelineMessage = item
            payload: SynthesizedPayload = msg.payload
            self._ledger.mark_time(msg.seq_id, "t_playback_start")
            try:
                self._output.play(payload.tts_pcm, payload.tts_samplerate)
            except Exception as exc:  # noqa: BLE001
                action = self._dispatch_error(exc, stage="Output", seq_id=msg.seq_id)
                if action == ErrorAction.STOP:
                    self._stop_event.set()
                    break
                self._ledger.pop(msg.seq_id)  # 失敗ぶんはレジャから削除
                continue

            self._ledger.mark_time(msg.seq_id, "t_playback")
            record = self._ledger.pop(msg.seq_id)
            record.setdefault("seq_id", msg.seq_id)

            if self._on_utterance_done is not None:
                try:
                    self._on_utterance_done(record)
                except Exception:  # noqa: BLE001 - UI 通知失敗で停止させない
                    self._logger.exception("on_utterance_done callback failed")

    # ============================================================
    # ユーティリティ
    # ============================================================
    def _dispatch_error(
        self,
        exc: BaseException,
        *,
        stage: str | None = None,
        seq_id: int | None = None,
    ) -> str:
        return self._error_handler.handle(exc, stage=stage, seq_id=seq_id)

    @staticmethod
    def _join_quietly(thread: threading.Thread | None, timeout: float) -> None:
        """スレッドを join。None や未起動なら何もしない。"""
        if thread is None:
            return
        if thread.is_alive():
            thread.join(timeout=timeout)

    @staticmethod
    def _drain_queue(q: _StageQueue) -> None:
        """キューの残骸を捨てる(start 直前に呼ぶ)。両キュータイプ対応。"""
        if isinstance(q, ByteBoundedQueue):
            q.drain()
            return
        try:
            while True:
                q.get_nowait()
        except queue.Empty:
            pass

    @staticmethod
    def _try_put_sentinel(q: _StageQueue) -> None:
        """キューにセンチネルを入れる。

        - ByteBoundedQueue: `push_evicting` は常に成功するのでそのまま入れる。
        - queue.Queue: 満杯なら 1つ捨てて再投入する。
        """
        if isinstance(q, ByteBoundedQueue):
            q.push_evicting(_SENTINEL)
            return
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

    def _put_with_drop(
        self, q: _StageQueue, item: PipelineMessage, stage_name: str
    ) -> None:
        """満杯なら古いものから捨てて新しいものを入れる(リアルタイム性優先)。

        捨てた発話があれば WARN ログ + 累計更新 + `on_dropped(seq_ids, stage)` 呼び出し。
        ドロップした seq_id のレジャ entry もここで pop する(リーク防止)。

        - ByteBoundedQueue: `push_evicting` で「設定値を超えるまで積み、超えたら退避」。
        - queue.Queue:     `put_nowait` → Full なら先頭を捨てて再試行(従来の count 基準)。
        """
        if isinstance(q, ByteBoundedQueue):
            evicted = q.push_evicting(item)
            dropped = [d for d in evicted if isinstance(d, PipelineMessage)]
        else:
            dropped = []
            while True:
                try:
                    q.put_nowait(item)
                    break
                except queue.Full:
                    try:
                        dropped.append(q.get_nowait())
                    except queue.Empty:
                        pass

        if not dropped:
            return

        seq_ids = [d.seq_id for d in dropped]
        count = len(seq_ids)
        total = self._drop_counts.get(stage_name, 0) + count
        self._drop_counts[stage_name] = total
        self._logger.warning(
            "queue overflow in %s: dropped %d utterance(s) (seq=%s), total=%d",
            stage_name, count, seq_ids, total,
        )
        # 捨てたぶんのレジャは削除(リーク防止)。テキストログは既に各段で残っている。
        for sid in seq_ids:
            self._ledger.pop(sid)
        if self._on_dropped is not None:
            try:
                self._on_dropped(seq_ids, stage_name)
            except Exception:  # noqa: BLE001 - コールバック失敗で停止しない
                self._logger.exception("on_dropped callback failed")
