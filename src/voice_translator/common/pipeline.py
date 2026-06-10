"""PipelineCoordinator: 編成表(PipelinePlan)駆動のステージ実行基盤。

役割: 構築時に backend の申告から編成表を組み、ステージ数ぶんのスレッドと
ステージ間キューを編成して動かす。各ステージは次段に必要な最小ペイロード
(`PipelineMessage` + 各 `*Payload`)だけを渡し、横断メタ(timeline / 各種テキスト /
言語等)は `UtteranceLedger` に seq_id をキーに集約する。

標準構成(単体 backend×6)での編成:
- 入力(Capture+VAD) --(captured_queue)--> ASR --(recognized_queue)--> Translator
  --(translated_queue)--> TTS --(synthesized_queue)--> Output
- PCM 系キューはバイト基準(ByteBoundedQueue)、テキスト系は件数基準(queue.Queue)。

縮退と複合:
- text_only(TTS=none)は「TTS / Output が編成に載らない」縮退。最終ステージ
  (Translator)完了で `on_text_ready` を発火し、ledger を解放する。
- 複合 backend(例: ASR+Translator)は 1 ステージ = 1 スレッドで複数ロールを担う。
  ロール内側の境界時刻は欠損とし、入口・出口のみ ledger に記録する。
- 一般則: 編成の最終ステージが Output なら完了時に `on_utterance_done`、
  Output でなければ完了時に `on_text_ready` + ledger 解放。

エラー方針: backend 例外は severity 駆動(`ErrorHandler`)。RECOVERABLE は指数
バックオフでリトライし、枯渇 / FATAL でパイプライン停止、SKIP / WARN は当該発話を
破棄して継続する。キューがあふれた場合は古いものから捨てる(リアルタイム性を優先)。
"""

from __future__ import annotations

import logging
import queue
import threading
import time
from dataclasses import dataclass
from time import monotonic
from typing import Any, Callable, Union

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
    Payload,
    PayloadKind,
    PipelineMessage,
    RawPayload,
    SynthesizedPayload,
    TranscribedPayload,
    TranslatedPayload,
)
from .pipeline_plan import (
    DEFAULT_DECLARATIONS,
    QUEUE_BASENAMES,
    PipelinePlan,
    PlanError,
    RoleDeclaration,
    StageSpec,
    build_pipeline_plan,
    declaration_of,
)
from .sequence import SequenceGenerator
from .stage_dump import NullStageDumpWriter, StageDumpWriter
from .types import INTERNAL_SAMPLE_RATE, LayerKind

# 停止シグナル代わりにキューへ流すセンチネル値
_SENTINEL: object = object()

# 両方のキュー実装をまとめて指すエイリアス
_StageQueue = Union[queue.Queue, ByteBoundedQueue]

# ステージ lead レイヤ → 互換スレッド属性名(テスト・診断が参照する従来名を維持)
_THREAD_ATTRS: dict[LayerKind, str] = {
    LayerKind.CAPTURE: "_input_thread",
    LayerKind.ASR: "_asr_thread",
    LayerKind.TRANSLATOR: "_translator_thread",
    LayerKind.TTS: "_tts_thread",
    LayerKind.OUTPUT: "_output_thread",
}

# ロール → ledger 計時キー(入口, 出口)。複合ステージは先頭ロールの入口と
# 末尾ロールの出口だけを使う(内側は欠損)。
_TIME_KEYS: dict[LayerKind, tuple[str, str]] = {
    LayerKind.ASR: ("t_asr_start", "t_asr"),
    LayerKind.TRANSLATOR: ("t_translate_start", "t_translate"),
    LayerKind.TTS: ("t_tts_start", "t_tts"),
    LayerKind.OUTPUT: ("t_playback_start", "t_playback"),
}


def _decl_for(backend: Any, layer: LayerKind) -> RoleDeclaration:
    """backend の編成申告を取り出す。

    申告は classmethod(= クラスレベルの契約)なので、インスタンスではなく
    `type(backend)` から読む。クラスに申告 I/F(covers_roles 等)が無い backend は
    レイヤ既定の単体ロールとみなす(registry の `backend_cls` 未登録エントリと同じ
    fallback 規則。レイヤ ABC を継承しない実装を単体 backend として受け入れるため)。
    """
    cls = type(backend)
    if callable(getattr(cls, "covers_roles", None)):
        return declaration_of(cls)
    return DEFAULT_DECLARATIONS[layer]


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


@dataclass
class _StageRuntime:
    """編成表の 1 ステージぶんの実行時情報(キュー・処理関数・スレッド)。"""

    spec: StageSpec
    backend: Any                                                 # 入力ステージは None
    processor: Callable[[PipelineMessage], tuple[Payload | None, str]] | None
    in_queue: _StageQueue | None
    out_queue: _StageQueue | None
    out_label: str                                               # ドロップ通知用のキュー名
    entry_time_key: str | None                                   # 入口の ledger 計時キー
    thread: threading.Thread | None = None


class PipelineCoordinator:
    """編成表に従ってステージスレッド群を動かすパイプライン制御。

    役割: 構築時に編成表を確定し(構成ミスは PlanError = 起動拒否)、各ステージを
    スレッド分離して動かす。キューあふれ時は最古を捨てて新しい発話を優先
    (リアルタイム性確保)。
    """

    def __init__(
        self,
        *,
        capture: AudioCaptureBackend,
        vad: VadBackend,
        asr: AsrBackend | None,
        translator: TranslatorBackend | None,
        tts: TtsBackend | None,
        output: AudioOutputBackend | None,
        error_handler: ErrorHandler,
        ledger: UtteranceLedger | None = None,
        sequence: SequenceGenerator | None = None,
        text_logger: TextLogger | None = None,
        src_lang: str = "auto",
        tgt_lang: str = "ja",
        on_utterance_done: Callable[[dict], None] | None = None,
        on_text_ready: Callable[[dict], None] | None = None,
        on_dropped: Callable[[list[int], str], None] | None = None,
        read_timeout: float = 0.1,
        captured_queue_max_bytes: int = 10_000_000,
        synthesized_queue_max_bytes: int = 5_000_000,
        recognized_queue_size: int = 10,
        translated_queue_size: int = 10,
        max_retries: int = 3,
        retry_base_sec: float = 0.5,
        retry_max_sec: float = 8.0,
        logger: logging.Logger | None = None,
        dump: StageDumpWriter | NullStageDumpWriter | None = None,
        output_mode: str = "audio",
    ) -> None:
        self._capture = capture
        self._vad = vad
        self._asr = asr
        self._translator = translator
        self._tts = tts
        self._output = output
        self._error_handler = error_handler
        # 出力モード: "audio"(既定) / "text_only"。未知の値は audio として扱う(防衛)。
        self._output_mode = output_mode if output_mode == "text_only" else "audio"
        # text_only モードでは tts / output が None でも OK。audio モードでは必須。
        if self._output_mode == "audio":
            if tts is None or output is None:
                raise ValueError(
                    "audio output_mode では tts と output backend が必須です"
                )
        self._ledger = ledger if ledger is not None else UtteranceLedger()
        self._sequence = sequence if sequence is not None else SequenceGenerator()
        self._text_logger = text_logger
        self._src_lang = src_lang
        self._tgt_lang = tgt_lang
        self._on_utterance_done = on_utterance_done
        # on_text_ready: 「翻訳テキストが確定し、音が鳴るより前」の前倒し通知。
        # audio 編成では TTS 完了時(ledger の `peek` スナップショット)、
        # Output を含まない編成では最終ステージ完了時(`pop` の戻り値)に発火する。
        self._on_text_ready = on_text_ready
        self._on_dropped = on_dropped
        self._read_timeout = read_timeout
        # リトライ機構のパラメータ。RecoverableError → 指数バックオフで再試行。
        # 最大回数を超過 or FatalError なら復帰不能としてパイプライン停止(意図的設計。
        # 3 連続失敗 ≒ 依存 API 側の障害とみなし、ユーザのローカル backend 切替でしのぐ)。
        self._max_retries = max(0, int(max_retries))
        self._retry_base_sec = max(0.0, float(retry_base_sec))
        self._retry_max_sec = max(self._retry_base_sec, float(retry_max_sec))
        self._logger = logger or logging.getLogger("voice_translator")
        # ステージ間データのダンプフック。無効時は no-op の NullStageDumpWriter。
        # ライフサイクル(start_run/stop_run)は呼び出し側(AppController)で管理する。
        self._dump: StageDumpWriter | NullStageDumpWriter = (
            dump if dump is not None else NullStageDumpWriter()
        )

        # 編成表を組む(申告の矛盾・型不整合はここで PlanError = 起動拒否)
        provided: dict[LayerKind, Any] = {
            LayerKind.CAPTURE: capture,
            LayerKind.VAD: vad,
            LayerKind.ASR: asr,
            LayerKind.TRANSLATOR: translator,
            LayerKind.TTS: tts,
            LayerKind.OUTPUT: output,
        }
        declarations = {
            layer: _decl_for(backend, layer)
            for layer, backend in provided.items()
            if backend is not None
        }
        self._plan: PipelinePlan = build_pipeline_plan(
            declarations, text_only=(self._output_mode == "text_only")
        )

        # キュー(PipelineMessage または _SENTINEL を流す)。payload 形式ごとに 1 本で、
        # 編成に載らない形式のキューも従来どおり構築しておく(restart 時の drain 対象、
        # および診断・テストからの参照互換のため。未使用キューのコストは無視できる)。
        self._captured_queue: _StageQueue = ByteBoundedQueue(
            max_bytes=captured_queue_max_bytes, size_of=_pcm_message_bytes
        )
        self._recognized_queue: _StageQueue = queue.Queue(maxsize=recognized_queue_size)
        self._translated_queue: _StageQueue = queue.Queue(maxsize=translated_queue_size)
        self._synthesized_queue: _StageQueue = ByteBoundedQueue(
            max_bytes=synthesized_queue_max_bytes, size_of=_pcm_message_bytes
        )
        self._queues_by_kind: dict[PayloadKind, _StageQueue] = {
            PayloadKind.RAW: self._captured_queue,
            PayloadKind.TRANSCRIBED: self._recognized_queue,
            PayloadKind.TRANSLATED: self._translated_queue,
            PayloadKind.SYNTHESIZED: self._synthesized_queue,
        }

        # 編成表 → ステージ実行時情報。隣接形式の整合は build 済みなので、ここでは
        # 「次段の consumes 形式のキューへ流す」だけでよい。
        backends_by_lead = {
            unit.lead: provided[unit.lead]
            for stage in self._plan.stages
            for unit in stage.units
        }
        self._stage_runtimes: list[_StageRuntime] = []
        stages = self._plan.stages
        for i, spec in enumerate(stages):
            nxt = stages[i + 1] if i + 1 < len(stages) else None
            if nxt is not None:
                out_q: _StageQueue | None = self._queues_by_kind[nxt.consumes]
                out_label = (
                    f"{QUEUE_BASENAMES[nxt.consumes]}({spec.label}→{nxt.label})"
                )
            else:
                out_q, out_label = None, ""
            if spec.is_input:
                rt = _StageRuntime(
                    spec=spec, backend=None, processor=None,
                    in_queue=None, out_queue=out_q, out_label=out_label,
                    entry_time_key=None,
                )
            else:
                rt = _StageRuntime(
                    spec=spec,
                    backend=backends_by_lead[spec.lead],
                    processor=self._make_processor(spec),
                    in_queue=self._queues_by_kind[spec.consumes],
                    out_queue=out_q,
                    out_label=out_label,
                    entry_time_key=_TIME_KEYS[spec.roles[0]][0],
                )
            self._stage_runtimes.append(rt)

        # overflow 累計(stage名 → 累積ドロップ数)
        self._drop_counts: dict[str, int] = {}

        # スレッド + 停止フラグ(互換属性は編成に載らないレイヤでは常に None)
        self._stop_event = threading.Event()
        self._input_thread: threading.Thread | None = None
        self._asr_thread: threading.Thread | None = None
        self._translator_thread: threading.Thread | None = None
        self._tts_thread: threading.Thread | None = None
        self._output_thread: threading.Thread | None = None

    # ============================================================
    @property
    def is_running(self) -> bool:
        """ステージスレッドのいずれかが動作中なら True。"""
        for rt in self._stage_runtimes:
            if rt.thread is not None and rt.thread.is_alive():
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

    @property
    def plan(self) -> PipelinePlan:
        """確定済みの編成表(診断・UI 表示用)。走行中は不変。"""
        return self._plan

    def start(self, *, capture_source_id: str, output_device_id: str) -> None:
        """編成表どおりにステージスレッドを起動。既に動作中なら RuntimeError。

        Output を含まない編成(text_only 等)では `output_device_id` は使わない
        (`output.start` も呼ばない)。
        """
        if self.is_running:
            raise RuntimeError("PipelineCoordinator は既に動作中です")

        self._stop_event.clear()
        # キュー + ledger 残骸をクリア(前回と編成が違っても残骸が残らないよう、
        # 編成に載らないキューも含め毎回全部 drain する)
        for q in self._queues_by_kind.values():
            self._drain_queue(q)
        self._ledger.clear()

        self._capture.start(capture_source_id)
        if self._plan.has_role(LayerKind.OUTPUT):
            assert self._output is not None  # 編成に載る = backend 提供済み
            self._output.start(output_device_id)
        self._vad.reset()

        threads: list[threading.Thread] = []
        for rt in self._stage_runtimes:
            if rt.spec.is_input:
                t = threading.Thread(
                    target=self._input_loop, name="vt_input", daemon=True
                )
            else:
                t = threading.Thread(
                    target=self._worker_loop,
                    args=(rt,),
                    name=f"vt_{rt.spec.lead.value}",
                    daemon=True,
                )
            rt.thread = t
            attr = _THREAD_ATTRS.get(rt.spec.lead)
            if attr is not None:
                setattr(self, attr, t)
            threads.append(t)
        for t in threads:
            t.start()

    def stop(self, *, join_timeout: float = 2.0) -> None:
        """停止: stop_event を立てる → 各ステージを上流から順に終了させる。

        入力ステージは `read_chunk` の戻りで終わる。後続ステージは前段キューへ
        センチネルを投入してから join する。編成に載らないキュー・スレッドには触れない
        (残骸は次回 start の drain で除去される)。
        """
        self._stop_event.set()

        for rt in self._stage_runtimes:
            if not rt.spec.is_input:
                assert rt.in_queue is not None
                self._try_put_sentinel(rt.in_queue)
            self._join_quietly(rt.thread, join_timeout)
            rt.thread = None
            attr = _THREAD_ATTRS.get(rt.spec.lead)
            if attr is not None:
                setattr(self, attr, None)

        # バックエンドの片付け(失敗は握りつぶす)
        try:
            self._capture.stop()
        except Exception:  # noqa: BLE001
            pass
        if self._plan.has_role(LayerKind.OUTPUT) and self._output is not None:
            try:
                self._output.stop()
            except Exception:  # noqa: BLE001
                pass

    def get_drop_counts(self) -> dict[str, int]:
        """ステージ別の累計ドロップ件数のコピーを返す(診断用)。"""
        return dict(self._drop_counts)

    def set_languages(
        self, *, src: str | None = None, tgt: str | None = None,
    ) -> None:
        """動作中に翻訳の入出力言語を差し替える(次発話から反映)。

        - `src` は入力ステージが `RawPayload` を作る際に読む `self._src_lang` を差し替える。
          既にキューに入っている発話は古い hint のまま流れる(各発話の言語 hint は
          capture 時点で確定する設計)。
        - `tgt` は翻訳ロールの処理が `translate(..., self._tgt_lang)` を呼ぶ際に読む。
          キューに積まれている発話も、処理する時点の最新値で訳される。
        - `None` を渡したフィールドは変更しない。

        スレッド安全性: `self._src_lang` / `self._tgt_lang` は単一の str 参照で、
        書き換えと読み出しは Python 参照型代入の atomic 性で保護される。
        """
        if src is not None:
            self._src_lang = str(src)
        if tgt is not None:
            self._tgt_lang = str(tgt)

    # ============================================================
    # スレッド本体
    # ============================================================
    def _input_loop(self) -> None:
        """capture.read_chunk → vad.process → 発話メッセージを先頭キューへ流す。"""
        rt = self._stage_runtimes[0]
        assert rt.out_queue is not None  # 入力ステージの次段は必ず存在する
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
                self._put_with_drop(rt.out_queue, msg, rt.out_label)

    def _worker_loop(self, rt: _StageRuntime) -> None:
        """共通ステージループ: キュー get → ロール処理 → 次段 put / 終端処理。

        ロール固有の中身(backend 呼び出し・計時・dump・テキストログ)は
        `rt.processor` に委ね、ここではキュー機構・エラー縮退・終端通知だけを扱う:
        - processor が `(payload, CONTINUE)` → 次段へ put(終端なら完了通知 + ledger 解放)
        - `(None, CONTINUE)` → 当該発話を破棄して継続(空翻訳・WARN 縮退)
        - `(None, SKIP)` → 同上(エラー由来)
        - `(None, STOP)` → 全ステージ停止
        """
        assert rt.in_queue is not None and rt.processor is not None
        while not self._stop_event.is_set():
            try:
                item = rt.in_queue.get(timeout=self._read_timeout)
            except queue.Empty:
                continue
            if item is _SENTINEL:
                break

            msg: PipelineMessage = item
            if rt.entry_time_key is not None:
                # 入口時刻はリトライ待ちを含む(キュー待ちと処理時間を切り分ける仕様)
                self._ledger.mark_time(msg.seq_id, rt.entry_time_key)
            payload, action = rt.processor(msg)
            if action == ErrorAction.STOP:
                self._stop_event.set()
                break
            if payload is None:
                # エラー縮退(SKIP / WARN)や空結果。当該発話を破棄して継続。
                self._ledger.pop(msg.seq_id)
                continue

            if rt.out_queue is not None:
                next_msg = PipelineMessage(seq_id=msg.seq_id, payload=payload)
                self._put_with_drop(rt.out_queue, next_msg, rt.out_label)
                continue

            # 終端ステージ: ledger を解放して完了通知。
            # Output を含むなら on_utterance_done、含まない編成(text_only 等)なら
            # on_text_ready(これが最終通知になる)。
            record = self._ledger.pop(msg.seq_id)
            record.setdefault("seq_id", msg.seq_id)
            if LayerKind.OUTPUT in rt.spec.roles:
                callback, cb_name = self._on_utterance_done, "on_utterance_done"
            else:
                callback, cb_name = self._on_text_ready, "on_text_ready"
            if callback is not None:
                try:
                    callback(record)
                except Exception:  # noqa: BLE001 - UI 通知失敗で停止させない
                    self._logger.exception("%s callback failed", cb_name)

    # ============================================================
    # ロール固有の処理(processor)
    # ============================================================
    def _make_processor(
        self, spec: StageSpec
    ) -> Callable[[PipelineMessage], tuple[Payload | None, str]]:
        """ステージのロール構成に対応する処理関数を返す。未対応の構成は組めない。"""
        processors: dict[
            tuple[LayerKind, ...],
            Callable[[PipelineMessage], tuple[Payload | None, str]],
        ] = {
            (LayerKind.ASR,): self._process_asr,
            (LayerKind.TRANSLATOR,): self._process_translator,
            (LayerKind.TTS,): self._process_tts,
            (LayerKind.OUTPUT,): self._process_output,
        }
        processor = processors.get(spec.roles)
        if processor is None:
            raise PlanError(f"未対応のステージ構成です: {spec.label}")
        return processor

    def _process_asr(self, msg: PipelineMessage) -> tuple[Payload | None, str]:
        """RAW → TRANSCRIBED(書き起こし + 言語確定 + 記録)。"""
        payload: RawPayload = msg.payload
        result, action = self._call_with_retry(
            lambda: self._asr.transcribe(payload.pcm, payload.src_lang_hint),
            stage="ASR", seq_id=msg.seq_id, backend=self._asr,
        )
        if action != ErrorAction.CONTINUE or result is self._SENTINEL_RESULT:
            return None, action
        text, lang = result

        # 言語: hint が auto/空でモデルが検出した場合は採用
        src_lang = (
            lang if payload.src_lang_hint in ("auto", "", None)
            else payload.src_lang_hint
        )

        self._ledger.mark_time(msg.seq_id, "t_asr")
        self._dump.on_asr(msg.seq_id, text, src_lang)
        self._ledger.record(msg.seq_id, src_text=text, src_lang=src_lang)
        if self._text_logger is not None:
            try:
                self._text_logger.write_src(msg.seq_id, text, src_lang)
            except Exception:  # noqa: BLE001 - テキストログ失敗で停止しない
                self._logger.exception("write_src failed")

        return TranscribedPayload(src_text=text, src_lang=src_lang), ErrorAction.CONTINUE

    def _process_translator(self, msg: PipelineMessage) -> tuple[Payload | None, str]:
        """TRANSCRIBED → TRANSLATED(翻訳 + 記録)。空翻訳は破棄。"""
        payload: TranscribedPayload = msg.payload
        result, action = self._call_with_retry(
            lambda: self._translator.translate(
                payload.src_text, payload.src_lang, self._tgt_lang
            ),
            stage="Translator", seq_id=msg.seq_id, backend=self._translator,
        )
        if action != ErrorAction.CONTINUE or result is self._SENTINEL_RESULT:
            return None, action
        tgt_text = result

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
            # 空翻訳は次段に流す意味がないので破棄(ledger 解放は worker loop 側)
            return None, ErrorAction.CONTINUE

        return (
            TranslatedPayload(tgt_text=tgt_text, tgt_lang=self._tgt_lang),
            ErrorAction.CONTINUE,
        )

    def _process_tts(self, msg: PipelineMessage) -> tuple[Payload | None, str]:
        """TRANSLATED → SYNTHESIZED(音声合成 + 前倒しテキスト通知)。"""
        payload: TranslatedPayload = msg.payload
        result, action = self._call_with_retry(
            lambda: self._tts.synthesize(payload.tgt_text, payload.tgt_lang),
            stage="TTS", seq_id=msg.seq_id, backend=self._tts,
        )
        if action != ErrorAction.CONTINUE or result is self._SENTINEL_RESULT:
            return None, action
        pcm, samplerate = result

        self._ledger.mark_time(msg.seq_id, "t_tts")
        self._dump.on_tts(msg.seq_id, pcm, samplerate)
        # 音声合成完了の時点で UI に「テキストできた」通知を流す(前倒し表示用)。
        # 再生待ち / 再生時間ぶんだけ早く履歴に出せる。失敗しても本体は止めない。
        if self._on_text_ready is not None:
            try:
                snapshot = self._ledger.peek(msg.seq_id)
                snapshot.setdefault("seq_id", msg.seq_id)
                self._on_text_ready(snapshot)
            except Exception:  # noqa: BLE001
                self._logger.exception("on_text_ready callback failed")

        return (
            SynthesizedPayload(tts_pcm=pcm, tts_samplerate=samplerate),
            ErrorAction.CONTINUE,
        )

    def _process_output(self, msg: PipelineMessage) -> tuple[Payload | None, str]:
        """SYNTHESIZED → 再生(終端処理は worker loop 側で on_utterance_done)。"""
        payload: SynthesizedPayload = msg.payload
        result, action = self._call_with_retry(
            lambda: self._output.play(payload.tts_pcm, payload.tts_samplerate),
            stage="Output", seq_id=msg.seq_id, backend=self._output,
        )
        if action != ErrorAction.CONTINUE or result is self._SENTINEL_RESULT:
            return None, action

        self._ledger.mark_time(msg.seq_id, "t_playback")
        return payload, ErrorAction.CONTINUE

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

    # ---- リトライ機構 ----
    _SENTINEL_RESULT: Any = object()  # _call_with_retry のフェイル戻り値マーカー

    def _call_with_retry(
        self,
        fn: Callable[[], Any],
        *,
        stage: str,
        seq_id: int | None,
        backend: Any,
    ) -> tuple[Any, str]:
        """backend 呼び出しを RecoverableError リトライ付きで実行する。

        - 成功: `(result, CONTINUE)`
        - FATAL / 未分類例外: `(_SENTINEL_RESULT, STOP)`(呼び出し側でパイプライン停止)
        - SKIP: `(_SENTINEL_RESULT, SKIP)`(当該発話を破棄して継続)
        - WARN: `(_SENTINEL_RESULT, CONTINUE)` 扱い(継続だが結果なし)
        - RECOVERABLE: 指数バックオフで `max_retries` まで再試行。全失敗で STOP に escalate。

        backend には `record_error(exc, context=stage)` で履歴を残す。失敗しても本体は止めない。
        """
        delay = self._retry_base_sec
        attempts = self._max_retries + 1  # 初回 + リトライ回数
        last_action = ErrorAction.STOP
        for attempt in range(attempts):
            try:
                return fn(), ErrorAction.CONTINUE
            except Exception as exc:  # noqa: BLE001
                self._record_backend_error(backend, exc, context=stage)
                action = self._dispatch_error(exc, stage=stage, seq_id=seq_id)
                last_action = action
                if action != ErrorAction.RETRY:
                    return self._SENTINEL_RESULT, action
                # RETRY: 残り回数があればバックオフして再試行
                if attempt >= self._max_retries:
                    self._logger.warning(
                        "stage=%s retries exhausted (%d) → STOP",
                        stage, self._max_retries,
                    )
                    return self._SENTINEL_RESULT, ErrorAction.STOP
                self._sleep_responsive(delay)
                # stop_event が立っているなら即時抜ける
                if self._stop_event.is_set():
                    return self._SENTINEL_RESULT, ErrorAction.STOP
                delay = min(delay * 2.0, self._retry_max_sec)
        return self._SENTINEL_RESULT, last_action

    @staticmethod
    def _record_backend_error(backend: Any, exc: BaseException, *, context: str) -> None:
        """backend.record_error を安全に呼ぶ(無い backend には no-op)。"""
        if backend is None:
            return
        recorder = getattr(backend, "record_error", None)
        if recorder is None:
            return
        try:
            recorder(exc, context=context)
        except Exception:  # noqa: BLE001
            # 履歴記録の失敗は本体に伝播させない
            pass

    def _sleep_responsive(self, total_sec: float) -> None:
        """指定秒スリープ。stop_event の応答性を保つために細かく区切って待つ。"""
        end = monotonic() + total_sec
        while True:
            remaining = end - monotonic()
            if remaining <= 0:
                return
            if self._stop_event.is_set():
                return
            time.sleep(min(0.1, remaining))

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
