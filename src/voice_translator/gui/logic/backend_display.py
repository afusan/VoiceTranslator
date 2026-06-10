"""backend_display: backend 名の「表示文字列 ↔ 内部値」変換の純関数。

役割: プルダウンに出す表示形式(TTS の「(なし)」、CAPTURE の「<kind> (<name>)」)と
ConfigStore に保存する内部値の相互変換を一元化する。
CaptureKind の解決(controller への問い合わせ)は View 側の責務で、
ここには解決済みの `CaptureKind | None` を渡す。

移行元(P1 / refactor-ui-3move): settings_panel.py のモジュールレベル変換関数と
`_render_backend_choices` / `_backend_internal_to_display` / `_backend_display_to_internal` /
`_capture_internal_to_display`。
"""

from __future__ import annotations

from voice_translator.common.types import CaptureKind, LayerKind

# TTS プルダウンの「(なし)」表示と内部値。
# 内部値 TTS_NONE_INTERNAL は AppController.TTS_NONE と一致させること。
# BackendRegistry にこの名前の backend は登録しない前提。
TTS_NONE_DISPLAY = "(なし)"
TTS_NONE_INTERNAL = "none"

# 音声取得 backend の kind 表示ラベル。「<kind label> (<backend name>)」形式で表示する。
CAPTURE_KIND_LABELS: dict[CaptureKind, str] = {
    CaptureKind.DEVICE: "デバイス",
    CaptureKind.PROCESS: "プロセス",
}

# レイヤの短縮表示名(吸収済み表示などの文中で使う)
LAYER_SHORT_LABELS: dict[LayerKind, str] = {
    LayerKind.CAPTURE: "音声取得",
    LayerKind.VAD: "VAD",
    LayerKind.ASR: "ASR",
    LayerKind.TRANSLATOR: "翻訳",
    LayerKind.TTS: "TTS",
    LayerKind.OUTPUT: "音声出力",
}


def absorbed_status_text(lead: LayerKind) -> str:
    """複合 backend に吸収されたレイヤのステータス欄に出す文言。

    例: 翻訳ロールが ASR の複合に吸収 → 「(ASR に吸収済み)」。
    このレイヤの backend 選択は Start 時に無視されることを示す。
    """
    label = LAYER_SHORT_LABELS.get(lead, lead.value)
    return f"({label} に吸収済み)"


def tts_display_to_internal(display: str) -> str:
    """TTS プルダウンの表示文字列を内部値に変換する。"""
    return TTS_NONE_INTERNAL if display == TTS_NONE_DISPLAY else display


def tts_internal_to_display(internal: str) -> str:
    """TTS の内部値を表示文字列に変換する。"""
    return TTS_NONE_DISPLAY if internal == TTS_NONE_INTERNAL else internal


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
    if not isinstance(kind, CaptureKind):
        return internal
    label = CAPTURE_KIND_LABELS.get(kind, internal)
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
