"""アプリログ + 翻訳履歴 jsonl 出力 + デバッグ用テキストログ。

役割:
- 標準ロガーの初期化(stdout + ファイル)
- 翻訳1件 = jsonl 1行(機械処理向け、`TranslationLogger`)
- 翻訳前/翻訳後テキストの個別追記(人間用デバッグ、`TextLogger`)
出力先と各 ON/OFF は `ConfigStore` から取得する想定。

R-3 で I/F 更新:
- TextLogger.write_src(seq_id, text, lang) / write_tgt(seq_id, text, lang) に分離。
  各ステージから直接呼べるように粒度を細かくし、seq_id を付与してログ間対応を取れるようにする。
- TranslationLogger.write_record(record: dict) で UtteranceLedger.pop() の戻り値を直接書ける。

詳細は docs/design/Class.md を参照。
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def setup_app_logger(
    *,
    name: str = "voice_translator",
    log_dir: Path | str | None = None,
    level: int | str = logging.INFO,
) -> logging.Logger:
    """アプリの基本ロガーを構築して返す。

    - stdout に常時出力。
    - log_dir 指定時は `<log_dir>/app.log` にもファイル出力(append)。
    - level は logging 定数(int)または文字列("INFO"/"WARNING" 等)。
      文字列で未知の値が来た場合は INFO にフォールバック。
    """
    resolved_level = _resolve_level(level)

    logger = logging.getLogger(name)
    logger.setLevel(resolved_level)
    logger.propagate = False

    fmt = logging.Formatter("[%(asctime)s] [%(levelname)s] %(message)s")

    if logger.handlers:
        # 既に初期化済みならハンドラ重複を避ける。レベルだけは更新する
        # (config 再読込で level が変わった場合などへの追従)。
        for h in logger.handlers:
            h.setLevel(resolved_level)
        return logger

    stream = logging.StreamHandler()
    stream.setFormatter(fmt)
    logger.addHandler(stream)

    if log_dir is not None:
        log_dir_path = Path(log_dir)
        log_dir_path.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(log_dir_path / "app.log", encoding="utf-8")
        file_handler.setFormatter(fmt)
        logger.addHandler(file_handler)

    return logger


def _resolve_level(level: int | str) -> int:
    """level を logging の int 定数に解決。文字列は大文字小文字を無視して名前解決。"""
    if isinstance(level, int):
        return level
    if isinstance(level, str):
        normalized = level.strip().upper()
        # logging.getLevelName(str) は未知名で文字列 "Level XX" を返す挙動なので、
        # known table を自前で見る
        known = {
            "CRITICAL": logging.CRITICAL,
            "ERROR": logging.ERROR,
            "WARNING": logging.WARNING,
            "WARN": logging.WARNING,
            "INFO": logging.INFO,
            "DEBUG": logging.DEBUG,
        }
        return known.get(normalized, logging.INFO)
    return logging.INFO


class TranslationLogger:
    """翻訳1件を jsonl に追記するロガー。

    役割: パイプライン終端で UtteranceLedger.pop() の戻り値(dict)を
    1行 JSON にして履歴ファイルに追記する。`enabled=False` のときは no-op。
    """

    def __init__(self, jsonl_path: Path | str, *, enabled: bool = True) -> None:
        self._path = Path(jsonl_path)
        self._enabled = enabled
        if self._enabled:
            self._path.parent.mkdir(parents=True, exist_ok=True)

    @property
    def enabled(self) -> bool:
        """jsonl 書き出しが有効かどうか。"""
        return self._enabled

    def write_record(self, record: dict[str, Any]) -> None:
        """ledger record(dict) を 1行 JSON として追記する。

        期待されるキー:
          - seq_id: int
          - timeline: {stage_name: float, ...}
          - src_text / src_lang / tgt_text / tgt_lang(各任意)
        latency_ms は timeline["t_capture"] と timeline["t_playback"] から自動算出する。
        """
        if not self._enabled:
            return
        line = self._build_line(record)
        with self._path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(line, ensure_ascii=False) + "\n")

    @staticmethod
    def _build_line(record: dict[str, Any]) -> dict[str, Any]:
        """ledger record からログ行用 dict を組み立てる。"""
        timeline = record.get("timeline", {}) or {}
        latency_ms: float | None = None
        t_cap = timeline.get("t_capture")
        t_play = timeline.get("t_playback")
        if t_cap is not None and t_play is not None:
            latency_ms = round((t_play - t_cap) * 1000.0, 2)

        return {
            "ts": datetime.now(timezone.utc).isoformat(),
            "seq_id": record.get("seq_id"),
            "src_lang": record.get("src_lang", ""),
            "src_text": record.get("src_text", ""),
            "tgt_lang": record.get("tgt_lang", ""),
            "tgt_text": record.get("tgt_text", ""),
            "latency_ms": latency_ms,
            "timeline": timeline,
        }


class TextLogger:
    """翻訳前後テキストを人間用に追記するロガー(デバッグ用)。

    役割: 各ステージから直接呼べるよう src/tgt を分離。
    - write_src(seq_id, text, lang) で ASR 直後に `soundsrc.txt` に追記
    - write_tgt(seq_id, text, lang) で Translator 直後に `translated.txt` に追記

    出力 ON/OFF は src/tgt 個別に設定可能。jsonl は機械処理用、本クラスは人間が斜め読みするのが目的。
    """

    def __init__(
        self,
        *,
        src_path: Path | str,
        tgt_path: Path | str,
        src_enabled: bool = False,
        tgt_enabled: bool = False,
    ) -> None:
        self._src_path = Path(src_path)
        self._tgt_path = Path(tgt_path)
        self._src_enabled = bool(src_enabled)
        self._tgt_enabled = bool(tgt_enabled)
        # 有効な側だけ親ディレクトリを作成しておく(無効側はファイルすら作らない)
        if self._src_enabled:
            self._src_path.parent.mkdir(parents=True, exist_ok=True)
        if self._tgt_enabled:
            self._tgt_path.parent.mkdir(parents=True, exist_ok=True)

    @property
    def src_enabled(self) -> bool:
        """src 側(翻訳前)が有効かどうか。"""
        return self._src_enabled

    @property
    def tgt_enabled(self) -> bool:
        """tgt 側(翻訳後)が有効かどうか。"""
        return self._tgt_enabled

    def write_src(self, seq_id: int, text: str, lang: str = "") -> None:
        """翻訳前テキスト(src)を soundsrc.txt に追記する。"""
        if not self._src_enabled:
            return
        text = (text or "").strip()
        if not text:
            return
        self._append(self._src_path, seq_id, text, lang)

    def write_tgt(self, seq_id: int, text: str, lang: str = "") -> None:
        """翻訳後テキスト(tgt)を translated.txt に追記する。"""
        if not self._tgt_enabled:
            return
        text = (text or "").strip()
        if not text:
            return
        self._append(self._tgt_path, seq_id, text, lang)

    @staticmethod
    def _append(path: Path, seq_id: int, text: str, lang: str) -> None:
        """1行を UTF-8 / LF で追記する。書式: `[YYYY-MM-DD HH:MM:SS] #SEQ [lang] text\\n`"""
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        lang_part = f"[{lang}] " if lang else ""
        # newline="" で Python の自動改行変換を無効化し、明示的に \n を書く
        with path.open("a", encoding="utf-8", newline="") as f:
            f.write(f"[{ts}] #{seq_id} {lang_part}{text}\n")
