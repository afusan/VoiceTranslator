"""backend_display: backend 名の「表示文字列 ↔ 内部値」変換の純関数。

役割: プルダウンに出す表示形式(TTS の「(なし)」、CAPTURE の「<kind> (<name>)」)と
ConfigStore に保存する内部値の相互変換を一元化する。
CaptureKind の解決(controller への問い合わせ)は View 側の責務で、
ここには解決済みの `CaptureKind | None` を渡す。

移行元: settings_panel.py のモジュールレベル変換関数と
`_render_backend_choices` / `_backend_internal_to_display` / `_backend_display_to_internal` /
`_capture_internal_to_display`。
"""

from __future__ import annotations

from voice_translator.common.types import CaptureKind, LayerKind

from ..i18n import tr

# TTS プルダウンの「(なし)」内部値。
# 内部値 TTS_NONE_INTERNAL は AppController.TTS_NONE と一致させること。
# BackendRegistry にこの名前の backend は登録しない前提。
# 表示文字列は `tts_none_display()` 経由で取得する(言語切替に追従させるため定数化しない)。
TTS_NONE_INTERNAL = "none"


def tts_none_display() -> str:
    """TTS プルダウンの「(なし)」表示文字列。"""
    return tr("backend.tts_none")


def skipped_status_text() -> str:
    """編成に載らないレイヤ(text_only の TTS/Output 等)のステータス欄に出す文言。

    吸収されたレイヤのステータス欄は空表示(プルダウン無効化で伝わるため文言を出さない。
    代行 backend の明示は動作タブのステータス集約 `status_summary.py` の役割)。
    """
    return tr("backend.skipped_status")


def tts_display_to_internal(display: str) -> str:
    """TTS プルダウンの表示文字列を内部値に変換する。"""
    return TTS_NONE_INTERNAL if display == tts_none_display() else display


def tts_internal_to_display(internal: str) -> str:
    """TTS の内部値を表示文字列に変換する。"""
    return tts_none_display() if internal == TTS_NONE_INTERNAL else internal


def capture_display_to_internal(display: str) -> str:
    """「デバイス (soundcard)」のような表示文字列から backend 名を抽出する。

    形式 `<label> (<backend>)` の末尾カッコ内を取り出す。マッチしないものは
    そのまま返す(防衛: 未登録表示 `(未登録)` や旧式設定の互換)。
    """
    if display.endswith(")") and "(" in display:
        start = display.rindex("(") + 1
        return display[start:-1]
    return display


def capture_internal_to_display(internal: str, kind: CaptureKind | None) -> str:
    """CAPTURE backend 名を「<kind label> (<backend>)」形式に変換する。

    kind が None(未登録 / 解決失敗)や、internal が空 / "(未登録)" のときは
    backend 名そのままを返す(防衛挙動も移行元から維持)。
    """
    if not internal or internal == "(未登録)":
        return internal
    if kind == CaptureKind.DEVICE:
        label = tr("capture_kind.device")
    elif kind == CaptureKind.PROCESS:
        label = tr("capture_kind.process")
    else:
        return internal
    return f"{label} ({internal})"


def backend_internal_to_display(
    layer: LayerKind, internal: str, *, capture_kind: CaptureKind | None = None,
) -> str:
    """指定レイヤの内部 backend 名を表示文字列に変換する(レイヤ別 dispatch)。"""
    if layer == LayerKind.TTS:
        return tts_internal_to_display(internal)
    if layer == LayerKind.CAPTURE:
        return capture_internal_to_display(internal, capture_kind)
    return internal


def backend_display_to_internal(layer: LayerKind, display: str) -> str:
    """表示文字列を内部 backend 名に変換する(レイヤ別 dispatch)。"""
    if layer == LayerKind.TTS:
        return tts_display_to_internal(display)
    if layer == LayerKind.CAPTURE:
        return capture_display_to_internal(display)
    return display
