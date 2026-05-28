"""ProcessTimeLogger: 各レイヤの処理時間を CSV で記録する。

役割: `UtteranceLedger` のタイムラインから 1 発話あたりの
「キュー待ち」「純処理時間」「合計レイテンシ」を計算し、`processtime.csv`
に 1 行ずつ追記する。プロファイリング/最適化のためのオプション機能。

CSV スキーマ(1 行 = 1 発話):
    timestamp, seq_id, src_lang, tgt_lang,
    utterance_ms,           # t_vad_end - t_capture(発話音声長 + VAD のラグ)
    asr_wait_ms,            # captured_queue 待ち
    asr_proc_ms,            # ASR の純処理時間
    translate_wait_ms,      # recognized_queue 待ち
    translate_proc_ms,      # 翻訳の純処理時間
    tts_wait_ms,            # translated_queue 待ち
    tts_proc_ms,            # TTS の純処理時間
    output_wait_ms,         # synthesized_queue 待ち
    output_proc_ms,         # output.play() 呼び出しの時間
    total_ms,               # t_playback - t_capture(端から端まで)
    src_chars, tgt_chars    # テキスト長(参考値)

欠損したマーカーがある場合(失敗等)はその列は空欄。ON/OFF は config の
`log.process_time_enabled` で切替。書き込みは追記モード(起動ごとに継続)。
"""

from __future__ import annotations

import csv
import threading
from datetime import datetime
from pathlib import Path
from typing import Any


_CSV_HEADER: list[str] = [
    "timestamp",
    "seq_id",
    "src_lang",
    "tgt_lang",
    "utterance_ms",
    "asr_wait_ms",
    "asr_proc_ms",
    "translate_wait_ms",
    "translate_proc_ms",
    "tts_wait_ms",
    "tts_proc_ms",
    "output_wait_ms",
    "output_proc_ms",
    "total_ms",
    "src_chars",
    "tgt_chars",
]


def _ms(a: float | None, b: float | None) -> str:
    """`b - a` を ミリ秒(整数寄り float、小数1桁) で返す。どちらか欠損なら空文字。"""
    if a is None or b is None:
        return ""
    delta = (b - a) * 1000.0
    return f"{delta:.1f}"


def derive_stage_durations(record: dict[str, Any]) -> dict[str, str]:
    """`UtteranceLedger.pop()` 由来の record から CSV 用の各列を計算する。

    純粋関数。ProcessTimeLogger の本体に依存せずテストできるよう分離。
    """
    timeline = record.get("timeline", {}) or {}
    t_cap = timeline.get("t_capture")
    t_vad_end = timeline.get("t_vad_end")
    t_asr_s = timeline.get("t_asr_start")
    t_asr = timeline.get("t_asr")
    t_tr_s = timeline.get("t_translate_start")
    t_tr = timeline.get("t_translate")
    t_tts_s = timeline.get("t_tts_start")
    t_tts = timeline.get("t_tts")
    t_pb_s = timeline.get("t_playback_start")
    t_pb = timeline.get("t_playback")

    src_text = record.get("src_text") or ""
    tgt_text = record.get("tgt_text") or ""

    return {
        "timestamp": datetime.now().isoformat(timespec="milliseconds"),
        "seq_id": str(record.get("seq_id", "")),
        "src_lang": str(record.get("src_lang", "") or ""),
        "tgt_lang": str(record.get("tgt_lang", "") or ""),
        "utterance_ms": _ms(t_cap, t_vad_end),
        "asr_wait_ms": _ms(t_vad_end, t_asr_s),
        "asr_proc_ms": _ms(t_asr_s, t_asr),
        "translate_wait_ms": _ms(t_asr, t_tr_s),
        "translate_proc_ms": _ms(t_tr_s, t_tr),
        "tts_wait_ms": _ms(t_tr, t_tts_s),
        "tts_proc_ms": _ms(t_tts_s, t_tts),
        "output_wait_ms": _ms(t_tts, t_pb_s),
        "output_proc_ms": _ms(t_pb_s, t_pb),
        "total_ms": _ms(t_cap, t_pb),
        "src_chars": str(len(src_text)),
        "tgt_chars": str(len(tgt_text)),
    }


class ProcessTimeLogger:
    """1 発話 = 1 行で処理時間を CSV に追記するロガー。

    - `enabled=False` なら何もしない(GUI / config から切替可能)。
    - ファイル不存在ならヘッダ付きで作成、既存なら追記。
    - 書き込みは Lock 配下(複数スレッドからの呼び出しを想定)。
    """

    def __init__(self, path: Path | str, *, enabled: bool = True) -> None:
        self._path = Path(path)
        self._enabled = enabled
        self._lock = threading.Lock()
        if self._enabled:
            self._ensure_header()

    @property
    def path(self) -> Path:
        return self._path

    @property
    def enabled(self) -> bool:
        return self._enabled

    # ----------------------------------------------------------
    def _ensure_header(self) -> None:
        """ファイルが無いか空ならヘッダを書く(冪等)。"""
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
        except OSError:
            # 親フォルダ作成失敗は write_record 側でも検知させるためここでは握りつぶす
            return

        # 存在&非空ならヘッダ書かない
        if self._path.exists() and self._path.stat().st_size > 0:
            return

        with self._path.open("a", encoding="utf-8", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(_CSV_HEADER)

    # ----------------------------------------------------------
    def write_record(self, record: dict[str, Any]) -> None:
        """1 発話ぶんを 1 行追記する。`enabled=False` ならノーオペ。"""
        if not self._enabled:
            return
        row_dict = derive_stage_durations(record)
        row = [row_dict[col] for col in _CSV_HEADER]
        with self._lock:
            with self._path.open("a", encoding="utf-8", newline="") as f:
                csv.writer(f).writerow(row)
