"""UtteranceLedger: seq_id をキーにした発話メタ情報の中央管理。

役割: 5スレッド版パイプラインで、各ステージのタイムスタンプ・言語・テキスト等を
seq_id ごとに集約する。stage間 payload を「次段に必要なデータだけ」に絞れるのは
このレジャに横断情報を寄せているため。
最終段(Output 完了)で pop() し、jsonl へまとめて書き出す。

スレッドセーフ: 内部 dict への全アクセスを Lock 配下で行う。
詳細は docs/design/Class.md / Architecture.html を参照。
"""

from __future__ import annotations

import threading
from time import monotonic
from typing import Any


class UtteranceLedger:
    """seq_id -> レコード辞書を保持する集中レジャ。

    レコード形式:
        {
            "timeline": {stage_name: float, ...},   # mark_time で追記
            <他のキー>: <値>,                       # record(**fields) で追記
        }
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._records: dict[int, dict[str, Any]] = {}

    # ----------------------------------------------------------
    def init(self, seq_id: int) -> None:
        """空レコードを作る(既存ならノーオペ)。

        Input 段で発話を確定した瞬間に呼ぶことを想定。
        """
        with self._lock:
            if seq_id not in self._records:
                self._records[seq_id] = {"timeline": {}}

    def mark_time(self, seq_id: int, stage: str) -> float:
        """ステージ通過時刻を `monotonic()` で記録。

        未登録 seq_id なら自動で init する(取りこぼし防止)。同名ステージは上書き。
        """
        now = monotonic()
        with self._lock:
            rec = self._records.setdefault(seq_id, {"timeline": {}})
            tl = rec.setdefault("timeline", {})
            tl[stage] = now
        return now

    def record(self, seq_id: int, **fields: Any) -> None:
        """任意フィールドを追記(merge)。

        未登録 seq_id なら自動で init する。timeline キーは update では潰さない
        (mark_time のために確保)。
        """
        if not fields:
            return
        with self._lock:
            rec = self._records.setdefault(seq_id, {"timeline": {}})
            # timeline を上書きされないように除外してから merge
            for k, v in fields.items():
                if k == "timeline":
                    # timeline をまとめて差し替えたい場合は明示的に dict を渡す想定
                    existing = rec.get("timeline", {})
                    if isinstance(v, dict):
                        existing.update(v)
                        rec["timeline"] = existing
                    continue
                rec[k] = v

    def pop(self, seq_id: int) -> dict[str, Any]:
        """全情報を取り出して ledger から削除。

        未登録なら空 dict を返す(KeyError しない)。メモリリーク防止のため
        最終段(Output 完了)で必ず呼ぶ。
        """
        with self._lock:
            return self._records.pop(seq_id, {})

    def peek(self, seq_id: int) -> dict[str, Any]:
        """覗き見(削除しない)。テスト/診断用。コピーを返す。"""
        with self._lock:
            rec = self._records.get(seq_id)
            if rec is None:
                return {}
            # shallow copy(timeline も独立 dict にする)
            copy = dict(rec)
            if "timeline" in copy and isinstance(copy["timeline"], dict):
                copy["timeline"] = dict(copy["timeline"])
            return copy

    def clear(self) -> None:
        """全レコードを破棄。再 start 時の drain 用。"""
        with self._lock:
            self._records.clear()

    def __len__(self) -> int:
        with self._lock:
            return len(self._records)

    def __contains__(self, seq_id: int) -> bool:
        with self._lock:
            return seq_id in self._records
