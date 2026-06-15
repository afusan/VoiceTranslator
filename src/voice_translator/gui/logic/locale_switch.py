"""locale_switch: UI ロケール切替の判断を担う純関数。

役割: 「起動時にどのロケールへ解決するか」「いま切り替えてよいか」「選択された表示名を
どのロケールコードに変換し、切り替えるべきか(no-op か)」を計算する。i18n / widget /
ConfigStore には触らない(依存は標準ライブラリのみ)。MainWindow は入力収集 → 本関数 →
反映(set_locale + 再構築)の配線役に徹する。
"""

from __future__ import annotations

from typing import Mapping, Sequence


def resolve_initial_locale(
    saved: str, available: Sequence[str], *, fallback: str = "ja",
) -> str:
    """起動時に適用するロケール。保存値が対応ロケールに無ければ fallback に縮退する。"""
    return saved if saved in available else fallback


def can_switch_locale(is_running: bool) -> bool:
    """いま切替を許可してよいか(動作中は再構築で表示と実行状態が食い違うため不可)。"""
    return not is_running


def resolve_target_locale(
    display: str, display_to_code: Mapping[str, str], current: str,
) -> str | None:
    """選択された表示名を対象ロケールコードに変換する。

    現在ロケールと同じ(= no-op)、または未知の表示名のときは None を返す。
    """
    code = display_to_code.get(display)
    if code is None or code == current:
        return None
    return code
