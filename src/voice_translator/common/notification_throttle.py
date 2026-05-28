"""NotificationThrottle: エラー通知の集約・抑制(キー別の時間窓 rate limit)。

役割: 同じ種類のエラーが短時間に連発したとき、UI コールバックの呼び出しを
キー(=`(stage, exception_type_name)`)単位で時間窓ベースに抑制する。
ログは抑制しない(情報源を失わないため)。抑制された件数は次の「許可」時に
合算してコールバックに渡し、利用側で「裏で N 件起きた」と分かる形にする。

スレッドセーフ: 全アクセスを内部 `threading.Lock` で保護する。
"""

from __future__ import annotations

import threading
from time import monotonic
from typing import Any


class NotificationThrottle:
    """キーごとの時間窓 rate limit。

    使い方:
        throttle = NotificationThrottle(window_sec=5.0)
        allow, suppressed = throttle.check(key=("ASR", "FatalError"))
        if allow:
            on_fatal(msg, suppressed=suppressed)  # suppressed は前回 allow からの抑制累計
        # 通常は ErrorHandler 内部で利用する
    """

    def __init__(self, *, window_sec: float = 5.0) -> None:
        """`window_sec` 秒に1回だけキー別に通知を許可する。

        `window_sec <= 0` を渡すと「無効化」=常に通知する(`disabled` プロパティで参照可)。
        """
        self._window_sec = float(window_sec)
        self._lock = threading.Lock()
        # キー -> {"last_allow": float, "suppressed": int}
        self._state: dict[Any, dict[str, Any]] = {}

    @property
    def window_sec(self) -> float:
        """現在の時間窓(秒)。"""
        return self._window_sec

    @property
    def disabled(self) -> bool:
        """`window_sec <= 0` のとき True(常に通知)。"""
        return self._window_sec <= 0

    def check(self, key: Any) -> tuple[bool, int]:
        """通知してよいか判定する。

        戻り値:
            allow (bool): True なら呼び出し側はコールバックを発火する
            suppressed (int): 前回 allow 以降に抑制した件数(allow=True のときのみ意味あり)

        挙動:
            - `disabled` なら常に (True, 0)
            - 初回は (True, 0)、内部カウンタリセット
            - 同キーが window_sec 内に再来 → (False, _++)、カウンタ加算
            - window_sec を超えて再来 → (True, 抑制件数)、カウンタリセット
        """
        if self.disabled:
            return True, 0

        now = monotonic()
        with self._lock:
            entry = self._state.get(key)
            if entry is None:
                self._state[key] = {"last_allow": now, "suppressed": 0}
                return True, 0

            elapsed = now - entry["last_allow"]
            if elapsed >= self._window_sec:
                # 窓を抜けた: 許可して suppressed を回収
                suppressed = entry["suppressed"]
                entry["last_allow"] = now
                entry["suppressed"] = 0
                return True, suppressed
            else:
                # 抑制対象: カウンタ加算
                entry["suppressed"] += 1
                return False, 0

    def pending_suppressed(self, key: Any) -> int:
        """キーに蓄積されている抑制件数を覗き見(削除しない)。テスト/診断用。"""
        with self._lock:
            entry = self._state.get(key)
            if entry is None:
                return 0
            return int(entry["suppressed"])

    def reset(self) -> None:
        """全状態を破棄。再 start 時等に呼ぶ。"""
        with self._lock:
            self._state.clear()
