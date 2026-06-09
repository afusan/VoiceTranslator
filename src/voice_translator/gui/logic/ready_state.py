"""ready_state: ControlPanel のボタン/ラベル表示を決める純関数。

役割: レイヤ状態・出力モード・入力ソース状況から「各ボタンの表示文言と有効/無効」を
計算して返す。widget には触らない(適用は View 側)。

移行元(P1 / refactor-ui-3move): control_panel.py の `_sync_ready_state` /
`_sync_load_button_state` / `_sync_test_button_state` / `_capture_source_required_but_empty` /
`_active_layer_statuses` の判断部。表示文言・優先順位は移行元と同一に保つこと。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from voice_translator.common.types import CaptureKind, LayerKind, ModelStatus


@dataclass(frozen=True)
class WidgetSpec:
    """ボタン 1 つ分の表示指示。enabled は True → state="normal" / False → "disabled"。"""

    text: str
    enabled: bool


@dataclass(frozen=True)
class ReadyState:
    """idle 時の ControlPanel 表示一式(トグル / ラベル / ロード / 出力テスト)。"""

    toggle: WidgetSpec
    status_text: str
    load: WidgetSpec
    test: WidgetSpec


def filter_active_statuses(
    statuses: Mapping[LayerKind, ModelStatus], output_mode: str,
) -> dict[LayerKind, ModelStatus]:
    """text_only モードなら TTS / OUTPUT を除外したレイヤ状態 dict を返す。"""
    if output_mode == "text_only":
        return {
            layer: status
            for layer, status in statuses.items()
            if layer not in (LayerKind.TTS, LayerKind.OUTPUT)
        }
    return dict(statuses)


def compute_ready_state(
    statuses: Mapping[LayerKind, ModelStatus],
    *,
    output_mode: str,
    capture_kind: CaptureKind,
    has_input_source: bool,
    has_output_device: bool,
) -> ReadyState | None:
    """idle 状態のときの 3 ボタン + ラベルの表示を一括計算する。

    statuses は全レイヤ分(フィルタ前)を渡す。対象レイヤが空のときは None を返し、
    View 側は何もしない(移行元 `_sync_ready_state` の早期 return と同じ)。

    トグルの優先順位(移行元の分岐順を維持):
    MISSING_CREDENTIALS > DOWNLOADING > (PROCESS kind かつ入力未選択) > 通常
    """
    active = filter_active_statuses(statuses, output_mode)
    vals = list(active.values())
    if not vals:
        return None

    # --- トグルボタン + ステータスラベル ---
    if any(s == ModelStatus.MISSING_CREDENTIALS for s in vals):
        toggle = WidgetSpec("認証情報未設定", enabled=False)
        status_text = "認証情報未設定(詳細ダイアログで設定してください)"
    elif any(s == ModelStatus.DOWNLOADING for s in vals):
        toggle = WidgetSpec("モデル DL 中…", enabled=False)
        status_text = "モデルダウンロード中…"
    elif capture_kind == CaptureKind.PROCESS and not has_input_source:
        toggle = WidgetSpec("プロセス未選択", enabled=False)
        status_text = "プロセスを選択してください(設定 → プロセス選択…)"
    else:
        toggle = WidgetSpec("▶ 開始", enabled=True)
        if any(s in (ModelStatus.INIT, ModelStatus.NOT_DOWNLOADED) for s in vals):
            status_text = "停止中(押下時にロードします)"
        elif any(s == ModelStatus.LOADING for s in vals):
            status_text = "停止中(ロード中)"
        else:
            status_text = "停止中"

    # --- 中央ロードボタン ---
    if all(s == ModelStatus.LOADED for s in vals):
        load = WidgetSpec("ロード済み", enabled=False)
    elif any(s == ModelStatus.LOADING for s in vals):
        load = WidgetSpec("ロード中…", enabled=False)
    else:
        load = WidgetSpec("↻ ロード", enabled=True)

    # --- 出力テストボタン ---
    if output_mode == "text_only":
        test = WidgetSpec("🔊 (TTS なし)", enabled=False)
    elif not has_output_device:
        test = WidgetSpec("🔊 出力未選択", enabled=False)
    else:
        test = WidgetSpec("🔊 出力テスト", enabled=True)

    return ReadyState(toggle=toggle, status_text=status_text, load=load, test=test)
