"""settings_button: 設定ボタンの enabled/disabled 判定を行う純関数。

役割: 指定レイヤ × backend に表示すべき設定項目があるかを判定する。
判定は layer_settings_schema.visible_fields() に委譲し、空リストなら False を返す。
widget には触らない(適用は SettingsPanel 側)。
"""

from __future__ import annotations

from voice_translator.common.types import LayerKind


def has_settings(layer: LayerKind, backend: str) -> bool:
    """指定レイヤ × backend に表示すべき設定項目があれば True を返す。

    判定は layer_settings_schema.visible_fields() に委譲する。
    空リストなら設定対象なし → False。
    """
    from voice_translator.gui.layer_settings_schema import visible_fields

    return len(visible_fields(layer, backend)) > 0
