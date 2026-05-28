"""レイヤ別設定ダイアログのスキーマ定義。

役割: 各レイヤ(Capture/VAD/ASR/Translator/TTS/Output)が編集可能な
設定項目を **宣言的に** 列挙する。`LayerSettingsDialog` がこれを読んで
ラベル+入力フィールドを動的に生成する。

新しい設定を追加するときは:
- 該当レイヤの `SettingField` をここに追加するだけで GUI に出現する
- バックエンド固有(SAPI rate 等)は `applies_when_backend` を指定すると、
  そのバックエンドが選ばれているときだけ表示される
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from voice_translator.common.types import LayerKind


# サポートする型(入力欄の振る舞いを切り替える)
FieldType = str  # "int" | "float" | "str" | "bool" のいずれか


@dataclass(frozen=True)
class SettingField:
    """1つの設定項目。`ConfigStore` のネストキー列で値を指す。

    例: keys=("pipeline", "captured_queue_max_bytes") → cfg["pipeline"]["captured_queue_max_bytes"]
    """

    keys: tuple[str, ...]              # ConfigStore の get/set に渡すキー列
    label: str                         # 入力欄の左側に出すラベル(日本語OK)
    field_type: FieldType              # "int" / "float" / "str" / "bool"
    default: Any = None                # 値が config 未設定だったときに使う表示初期値
    help_text: str = ""                # ラベル下のヘルプ(1行)
    applies_when_backend: str | None = None  # 「この名前のバックエンドが選ばれているときだけ表示」


# 値を Python 型に変換する関数群(入力欄文字列 → 値)
_PARSERS: dict[FieldType, Callable[[str], Any]] = {
    "int": lambda s: int(s.strip()),
    "float": lambda s: float(s.strip()),
    "str": lambda s: s,
    "bool": lambda s: s.strip().lower() in ("1", "true", "yes", "on"),
}


def parse_field_value(field_type: FieldType, raw: str) -> Any:
    """入力欄の文字列を `field_type` に従って変換する。失敗時は ValueError。"""
    parser = _PARSERS.get(field_type)
    if parser is None:
        raise ValueError(f"未対応の field_type: {field_type}")
    return parser(raw)


# ============================================================
# 各レイヤの設定項目一覧
# ============================================================
LAYER_SETTINGS: dict[LayerKind, list[SettingField]] = {
    LayerKind.CAPTURE: [
        SettingField(
            keys=("pipeline", "captured_queue_max_bytes"),
            label="入力バッファ容量 (bytes)",
            field_type="int",
            default=10_000_000,
            help_text=(
                "VAD出力PCMを次段(ASR)に渡すバッファのバイト上限。"
                "16kHz×float32 で 10MB ≒ 約 156 秒分。"
                "「▶ 開始」を押した時に反映される。"
            ),
        ),
    ],
    LayerKind.VAD: [],
    LayerKind.ASR: [
        SettingField(
            keys=("pipeline", "recognized_queue_size"),
            label="認識結果バッファ件数",
            field_type="int",
            default=10,
            help_text=(
                "ASR が出力した認識テキストを翻訳段に渡すキューの上限件数。"
                "テキストは1発話で数百バイトと小さいため件数で管理する。"
            ),
        ),
    ],
    LayerKind.TRANSLATOR: [
        SettingField(
            keys=("pipeline", "translated_queue_size"),
            label="翻訳結果バッファ件数",
            field_type="int",
            default=10,
            help_text="翻訳済みテキストを TTS に渡すキューの上限件数。",
        ),
    ],
    LayerKind.TTS: [
        SettingField(
            keys=("backends_config", "sapi", "rate"),
            label="読み上げ速度 (rate)",
            field_type="int",
            default=180,
            applies_when_backend="sapi",
            help_text=(
                "SAPI(pyttsx3)の rate。既定 180(普通)。早口にすると再生時間が短くなる。"
                "GUI 反映には「設定を再読込」または再起動が必要。"
            ),
        ),
    ],
    LayerKind.OUTPUT: [
        SettingField(
            keys=("pipeline", "synthesized_queue_max_bytes"),
            label="出力バッファ容量 (bytes)",
            field_type="int",
            default=5_000_000,
            help_text=(
                "TTS 合成済み PCM を再生段に渡すバッファのバイト上限。"
                "16kHz×float32 で 5MB ≒ 約 78 秒分。"
                "「▶ 開始」を押した時に反映される。"
            ),
        ),
    ],
}


def visible_fields(
    layer: LayerKind, current_backend: str
) -> list[SettingField]:
    """レイヤ + 現在のバックエンド名から、表示すべきフィールドを返す。

    `applies_when_backend` が指定されているフィールドは、その名前が現在の選択と
    一致するときだけ表示される。
    """
    items = LAYER_SETTINGS.get(layer, [])
    result: list[SettingField] = []
    for f in items:
        if f.applies_when_backend is not None and f.applies_when_backend != current_backend:
            continue
        result.append(f)
    return result
