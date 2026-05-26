"""アプリログ + 翻訳履歴 jsonl 出力。

役割: 標準ロガーの初期化(stdout + ファイル)と、翻訳1件あたりの jsonl 追記を担う。
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
