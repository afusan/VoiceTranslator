"""PipelineCoordinator: 5スレッド版(Input / ASR / Translator / TTS / Output)。

役割: ASR・翻訳・TTS を独立スレッド化して直列ボトルネックを解消する。
各ステージは次段に必要な最小ペイロード(`PipelineMessage` + 各 `*Payload`)だけを渡し、
横断メタ(timeline / 各種テキスト / 言語等)は `UtteranceLedger` に seq_id をキーに集約する。

スレッド/キュー構成:
- Input    --(q_raw)-->  ASR
- ASR      --(q_tr)-->   Translator
- Translator -(q_xl)-->  TTS
- TTS      --(q_syn)-->  Output

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

# 停止シグナル代わりにキューへ流すセンチネル値
_SENTINEL: object = object()


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
        q_raw_size: int = 5,
        q_tr_size: int = 10,
        q_xl_size: int = 10,
        q_syn_size: int = 5,
        logger: logging.Logger | None = None,
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

        # キュー(PipelineMessage または _SENTINEL を流す)
        self._q_raw: queue.Queue = queue.Queue(maxsize=q_raw_size)
        self._q_tr: queue.Queue = queue.Queue(maxsize=q_tr_size)
        self._q_xl: queue.Queue = queue.Queue(maxsize=q_xl_size)
        self._q_syn: queue.Queue = queue.Queue(maxsize=q_syn_size)

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
        for q in (self._q_raw, self._q_tr, self._q_xl, self._q_syn):
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
            (self._q_raw, "_asr_thread"),
            (self._q_tr, "_translator_thread"),
            (self._q_xl, "_tts_thread"),
            (self._q_syn, "_output_thread"),
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
        """capture.read_chunk → vad.process → q_raw へ Raw メッセージを流す。"""
        while not self._stop_event.is_set():
            try:
                chunk = self._capture.read_chunk(timeout=self._read_timeout)
            except Exception as exc:  # noqa: BLE001
                if self._dispatch_error(exc) == ErrorAction.STOP:
                    # FATAL: 他スレッドにも停止を伝える(自分だけ break すると他が回り続ける)
                    self._stop_event.set()
                    break
                continue

            if chunk is None:
                continue

            try:
                segments = self._vad.process(chunk)
            except Exception as exc:  # noqa: BLE001
                if self._dispatch_error(exc) == ErrorAction.STOP:
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
                msg = PipelineMessage(
                    seq_id=seq_id,
                    payload=RawPayload(pcm=seg.pcm, src_lang_hint=self._src_lang),
                )
                self._put_with_drop(self._q_raw, msg, "q_raw(Input→ASR)")

    def _asr_loop(self) -> None:
        """q_raw → ASR → q_tr。"""
        while not self._stop_event.is_set():
            try:
                item = self._q_raw.get(timeout=self._read_timeout)
            except queue.Empty:
                continue
            if item is _SENTINEL:
                break

            msg: PipelineMessage = item
            payload: RawPayload = msg.payload
            try:
                text, lang = self._asr.transcribe(payload.pcm, payload.src_lang_hint)
            except Exception as exc:  # noqa: BLE001
                action = self._dispatch_error(exc)
                if action == ErrorAction.STOP:
                    self._stop_event.set()
                    break
                self._ledger.pop(msg.seq_id)  # 失敗ぶんはレジャから削除
                continue

            # 言語: hint が auto/空でモデルが検出した場合は採用
            src_lang = lang if payload.src_lang_hint in ("auto", "", None) else payload.src_lang_hint

            self._ledger.mark_time(msg.seq_id, "t_asr")
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
            self._put_with_drop(self._q_tr, next_msg, "q_tr(ASR→Translator)")

    def _translator_loop(self) -> None:
        """q_tr → Translator → q_xl。"""
        while not self._stop_event.is_set():
            try:
                item = self._q_tr.get(timeout=self._read_timeout)
            except queue.Empty:
                continue
            if item is _SENTINEL:
                break

            msg: PipelineMessage = item
            payload: TranscribedPayload = msg.payload
            try:
                tgt_text = self._translator.translate(
                    payload.src_text, payload.src_lang, self._tgt_lang
                )
            except Exception as exc:  # noqa: BLE001
                action = self._dispatch_error(exc)
                if action == ErrorAction.STOP:
                    self._stop_event.set()
                    break
                self._ledger.pop(msg.seq_id)
                continue

            self._ledger.mark_time(msg.seq_id, "t_translate")
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
            self._put_with_drop(self._q_xl, next_msg, "q_xl(Translator→TTS)")

    def _tts_loop(self) -> None:
        """q_xl → TTS → q_syn。"""
        while not self._stop_event.is_set():
            try:
                item = self._q_xl.get(timeout=self._read_timeout)
            except queue.Empty:
                continue
            if item is _SENTINEL:
                break

            msg: PipelineMessage = item
            payload: TranslatedPayload = msg.payload
            try:
                pcm, samplerate = self._tts.synthesize(payload.tgt_text, payload.tgt_lang)
            except Exception as exc:  # noqa: BLE001
                action = self._dispatch_error(exc)
                if action == ErrorAction.STOP:
                    self._stop_event.set()
                    break
                self._ledger.pop(msg.seq_id)
                continue

            self._ledger.mark_time(msg.seq_id, "t_tts")
            next_msg = PipelineMessage(
                seq_id=msg.seq_id,
                payload=SynthesizedPayload(tts_pcm=pcm, tts_samplerate=samplerate),
            )
            self._put_with_drop(self._q_syn, next_msg, "q_syn(TTS→Output)")

    def _output_loop(self) -> None:
        """q_syn → Output → ledger.pop → on_utterance_done。"""
        while not self._stop_event.is_set():
            try:
                item = self._q_syn.get(timeout=self._read_timeout)
            except queue.Empty:
                continue
            if item is _SENTINEL:
                break

            msg: PipelineMessage = item
            payload: SynthesizedPayload = msg.payload
            try:
                self._output.play(payload.tts_pcm, payload.tts_samplerate)
            except Exception as exc:  # noqa: BLE001
                action = self._dispatch_error(exc)
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

    def _put_with_drop(self, q: queue.Queue, item: PipelineMessage, stage_name: str) -> None:
        """満杯なら古いものから捨てて新しいものを入れる(リアルタイム性優先)。

        捨てた発話があれば WARN ログ + 累計更新 + `on_dropped(seq_ids, stage)` 呼び出し。
        ドロップした seq_id のレジャ entry もここで pop する(リーク防止)。
        """
        dropped: list[PipelineMessage] = []
        while True:
            try:
                q.put_nowait(item)
                if dropped:
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
                return
            except queue.Full:
                try:
                    dropped.append(q.get_nowait())
                except queue.Empty:
                    pass
