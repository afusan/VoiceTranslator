"""ready_state: ControlPanel のボタン/ラベル表示を決める純関数。

役割: レイヤ状態・出力モード・入力ソース状況から「各ボタンの表示文言と有効/無効」を
計算して返す。widget には触らない(適用は View 側)。

移行元: control_panel.py の `_sync_ready_state` /
`_sync_load_button_state` / `_sync_test_button_state` / `_capture_source_required_but_empty` /
`_active_layer_statuses` の判断部。表示文言・優先順位は移行元と同一に保つこと。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Collection, Mapping

from voice_translator.common.types import (
    AuthState,
    CaptureKind,
    LayerKind,
    ModelStatus,
)

from ..i18n import tr


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


def _excluded_layers(
    output_mode: str, absorbed: Collection[LayerKind],
) -> set[LayerKind]:
    """起動対象外(text_only の TTS/Output + 吸収されたロール)のレイヤ集合。"""
    excluded = set(absorbed)
    if output_mode == "text_only":
        excluded.update((LayerKind.TTS, LayerKind.OUTPUT))
    return excluded


def filter_active_statuses(
    statuses: Mapping[LayerKind, ModelStatus],
    output_mode: str,
    absorbed: Collection[LayerKind] = (),
) -> dict[LayerKind, ModelStatus]:
    """起動対象外のレイヤを除いた状態 dict を返す。

    - text_only モードなら TTS / OUTPUT を除外。
    - 複合 backend に吸収されたロール(`absorbed`)も除外(ロードされないため、
      残すと「永遠に Init のレイヤ」が ready 判定を濁す)。
    """
    excluded = _excluded_layers(output_mode, absorbed)
    return {
        layer: status
        for layer, status in statuses.items()
        if layer not in excluded
    }


def compute_ready_state(
    statuses: Mapping[LayerKind, ModelStatus],
    *,
    output_mode: str,
    capture_kind: CaptureKind,
    has_input_source: bool,
    has_output_device: bool,
    absorbed: Collection[LayerKind] = (),
    auth_states: Mapping[LayerKind, AuthState] | None = None,
) -> ReadyState | None:
    """idle 状態のときの 3 ボタン + ラベルの表示を一括計算する。

    statuses は全レイヤ分(フィルタ前)を渡す。対象レイヤが空のときは None を返し、
    View 側は何もしない。`absorbed` は複合 backend に吸収されたロール(判定対象外)。
    `auth_states` は選択中 backend の認証準備状態(静的判定)。未ロードのクラウド
    backend(Init のまま)や「鍵あり・未検証」でも開始ボタンを止められる —
    押下時の認証 gate(FatalError)は最後の防波堤で、ここで先に伝えるのが本線。

    トグルの優先順位:
    認証未設定(static MISSING or instance MISSING_CREDENTIALS)> 認証未検証 >
    DOWNLOADING > (PROCESS kind かつ入力未選択) > 通常
    """
    active = filter_active_statuses(statuses, output_mode, absorbed)
    vals = list(active.values())
    if not vals:
        return None
    excluded = _excluded_layers(output_mode, absorbed)
    auth_vals = [
        a for layer, a in (auth_states or {}).items() if layer not in excluded
    ]

    # --- トグルボタン + ステータスラベル ---
    if (
        any(a == AuthState.MISSING for a in auth_vals)
        or any(s == ModelStatus.MISSING_CREDENTIALS for s in vals)
    ):
        toggle = WidgetSpec(tr("ready.toggle.auth_missing"), enabled=False)
        status_text = tr("ready.status.auth_missing")
    elif any(a == AuthState.UNVERIFIED for a in auth_vals):
        toggle = WidgetSpec(tr("ready.toggle.auth_unverified"), enabled=False)
        status_text = tr("ready.status.auth_unverified")
    elif any(s == ModelStatus.DOWNLOADING for s in vals):
        toggle = WidgetSpec(tr("ready.toggle.downloading"), enabled=False)
        status_text = tr("ready.status.downloading")
    elif capture_kind == CaptureKind.PROCESS and not has_input_source:
        toggle = WidgetSpec(tr("ready.toggle.no_process"), enabled=False)
        status_text = tr("ready.status.no_process")
    else:
        toggle = WidgetSpec(tr("ready.toggle.start"), enabled=True)
        if any(s in (ModelStatus.INIT, ModelStatus.NOT_DOWNLOADED) for s in vals):
            status_text = tr("ready.status.idle_will_load")
        elif any(s == ModelStatus.LOADING for s in vals):
            status_text = tr("ready.status.idle_loading")
        else:
            status_text = tr("ready.status.idle")

    # --- 中央ロードボタン ---
    if all(s == ModelStatus.LOADED for s in vals):
        load = WidgetSpec(tr("ready.load.loaded"), enabled=False)
    elif any(s == ModelStatus.LOADING for s in vals):
        load = WidgetSpec(tr("ready.load.loading"), enabled=False)
    else:
        load = WidgetSpec(tr("ready.load.load"), enabled=True)

    # --- 出力テストボタン ---
    if output_mode == "text_only":
        test = WidgetSpec(tr("ready.test.tts_none"), enabled=False)
    elif not has_output_device:
        test = WidgetSpec(tr("ready.test.no_output"), enabled=False)
    else:
        test = WidgetSpec(tr("ready.test.run"), enabled=True)

    return ReadyState(toggle=toggle, status_text=status_text, load=load, test=test)
