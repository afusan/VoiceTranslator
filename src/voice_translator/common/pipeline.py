"""PipelineCoordinator: パイプライン全体の制御。

役割: capture → VAD → ASR → translator → TTS → output を直列に接続し、
バックグラウンドスレッドで発話単位 (Utterance) を流す。
start/stop のライフサイクル管理、ステージ別タイムスタンプ付与、
例外を ErrorHandler に委譲して4分類で振り分ける。
"""

from __future__ import annotations

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


class PipelineCoordinator:
    """各レイヤを直列に動かすコーディネータ。

    役割: start() でループスレッドを起動、stop() で停止。
    各発話で全ステージを順に呼び、Utterance に timeline を打ちつつ流す。
    例外は ErrorHandler に委ねて挙動を決める(STOP=ループ停止 / SKIP/CONTINUE=継続 / RETRY=次発話で再試行扱い)。
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

        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()

    @property
    def is_running(self) -> bool:
        """ループスレッドが動作中かどうか。"""
        return self._thread is not None and self._thread.is_alive()

    def start(self, *, capture_source_id: str, output_device_id: str) -> None:
        """パイプラインを開始する。

        - capture/output を起動 → ループスレッド開始。
        - 既に動いている場合は RuntimeError。
        """
        if self.is_running:
            raise RuntimeError("PipelineCoordinator は既に動作中です")

        self._stop_event.clear()
        self._capture.start(capture_source_id)
        self._output.start(output_device_id)
        self._vad.reset()

        self._thread = threading.Thread(
            target=self._loop, name="voice_translator_pipeline", daemon=True
        )
        self._thread.start()

    def stop(self, *, join_timeout: float = 5.0) -> None:
        """パイプラインを停止する。複数回呼ばれても安全。"""
        self._stop_event.set()
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=join_timeout)
        self._thread = None
        # capture/output 側の停止は例外を抑止しつつ呼ぶ(複数回呼ばれてもよいI/F前提)
        try:
            self._capture.stop()
        except Exception:  # noqa: BLE001 - 停止中の例外は致命にせず継続
            pass
        try:
            self._output.stop()
        except Exception:  # noqa: BLE001
            pass

    # ---- 内部 ----
    def _loop(self) -> None:
        """メインループ: 停止指示があるまで chunk を読み続け、発話を下流に流す。"""
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
                completed = self._vad.process(chunk)
            except Exception as exc:  # noqa: BLE001
                if self._dispatch_error(exc) == ErrorAction.STOP:
                    break
                continue

            for utt in completed:
                if self._stop_event.is_set():
                    break
                self._process_utterance(utt)

    def _process_utterance(self, utt: Utterance) -> None:
        """1発話を ASR → 翻訳 → TTS → 出力 の順で処理する。"""
        # src_lang は設定値を反映(ASRの自動検出に任せる場合は "auto")
        utt.src_lang = self._src_lang
        utt.tgt_lang = self._tgt_lang

        try:
            self._asr.transcribe(utt, self._src_lang)
            utt.timeline.mark("t_asr")

            self._translator.translate(utt, self._tgt_lang)
            utt.timeline.mark("t_translate")

            self._tts.synthesize(utt)
            utt.timeline.mark("t_tts")

            self._output.play(utt)
            utt.timeline.mark("t_playback")

            if self._on_utterance_done is not None:
                self._on_utterance_done(utt)
        except Exception as exc:  # noqa: BLE001 - 中央で severity 分類
            action = self._dispatch_error(exc)
            if action == ErrorAction.STOP:
                self._stop_event.set()
            # SKIP/CONTINUE/RETRY はこの発話を破棄して継続(MVPは RETRY も次発話まで持ち越さない)

    def _dispatch_error(self, exc: BaseException) -> str:
        """ErrorHandler に振り分けを委譲し、決定アクションを返す。"""
        return self._error_handler.handle(exc)
