"""アプリログ + 翻訳履歴 jsonl 出力 + デバッグ用テキストログ。

役割:
- 標準ロガーの初期化(stdout + ファイル)
- 翻訳1件 = jsonl 1行(機械処理向け、`TranslationLogger`)
- 翻訳前/翻訳後テキストの個別追記(人間用デバッグ、`TextLogger`)
出力先と各 ON/OFF は `ConfigStore` から取得する想定。
詳細は docs/design/Class.md を参照。
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .utterance import Utterance


def setup_app_logger(
    *,
    name: str = "voice_translator",
    log_dir: Path | str | None = None,
    level: int = logging.INFO,
) -> logging.Logger:
    """アプリの基本ロガーを構築して返す。

    - stdout に常時出力。
    - log_dir 指定時は `<log_dir>/app.log` にもファイル出力(append)。
    """
    logger = logging.getLogger(name)
    logger.setLevel(level)
    logger.propagate = False

    if logger.handlers:
        # 既に初期化済みならハンドラ重複を避ける
        return logger

    fmt = logging.Formatter("[%(asctime)s] [%(levelname)s] %(message)s")

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


class TranslationLogger:
    """翻訳1件を jsonl に追記するロガー。

    役割: パイプライン終端で Utterance を1行 JSON にして履歴ファイルに追記する。
    `enabled=False` のときは no-op。出力先は ConfigStore から渡される想定。
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

    def write(self, utt: Utterance) -> None:
        """Utterance 1件を1行JSONとして追記する。"""
        if not self._enabled:
            return
        record = self._build_record(utt)
        with self._path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    @staticmethod
    def _build_record(utt: Utterance) -> dict[str, Any]:
        """Utterance からログ行用 dict を組み立てる。"""
        timeline = utt.timeline.as_dict()
        latency_ms: float | None = None
        elapsed = utt.timeline.elapsed("t_capture", "t_playback")
        if elapsed is not None:
            latency_ms = round(elapsed * 1000.0, 2)

        return {
            "ts": datetime.now(timezone.utc).isoformat(),
            "src_lang": utt.src_lang,
            "src_text": utt.src_text,
            "tgt_lang": utt.tgt_lang,
            "tgt_text": utt.tgt_text,
            "latency_ms": latency_ms,
            "timeline": timeline,
        }


class TextLogger:
    """翻訳前後テキストを人間用に追記するロガー(デバッグ用)。

    役割: 1発話につき
    - 翻訳前テキスト(src_text)を `soundsrc.txt` に
    - 翻訳後テキスト(tgt_text)を `translated.txt` に
    それぞれ追記する。出力 ON/OFF は src/tgt 個別に設定可能。
    既存 jsonl は機械処理用、本クラスは人間が斜め読みするのが目的。
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

    def write(self, utt: Utterance) -> None:
        """Utterance の src_text/tgt_text を各々のファイルに追記する。

        - 空文字なら該当側はスキップ(無音や空応答のノイズを抑える)。
        - 無効側はノーオペレーション。
        """
        if self._src_enabled:
            text = (utt.src_text or "").strip()
            if text:
                self._append(self._src_path, text, utt.src_lang or "")
        if self._tgt_enabled:
            text = (utt.tgt_text or "").strip()
            if text:
                self._append(self._tgt_path, text, utt.tgt_lang or "")

    @staticmethod
    def _append(path: Path, text: str, lang: str) -> None:
        """1行を UTF-8 / LF で追記する。書式: `[YYYY-MM-DD HH:MM:SS] [lang] text\\n`"""
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        lang_part = f"[{lang}] " if lang else ""
        # newline="" で Python の自動改行変換を無効化し、明示的に \n を書く
        with path.open("a", encoding="utf-8", newline="") as f:
            f.write(f"[{ts}] {lang_part}{text}\n")
