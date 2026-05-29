"""レイヤ別設定ダイアログのスキーマ定義。

役割: 各レイヤ(Capture/VAD/ASR/Translator/TTS/Output)が編集可能な
設定項目を **宣言的に** 列挙する。`LayerSettingsDialog` がこれを読んで
ラベル+入力フィールドを動的に生成する。

新しい設定を追加するときは:
- 該当レイヤの `SettingField` をここに追加するだけで GUI に出現する
- バックエンド固有(SAPI rate 等)は `applies_when_backend` を指定すると、
  そのバックエンドが選ばれているときだけ表示される

サポートする field_type:
- **"int" / "float" / "str" / "bool"**: テキスト入力(従来からの基本型)
- **"dropdown"** (Phase C1): `options_fn(controller, layer) -> list[ModelInfo|str]` で
  選択肢を実行時に取得するプルダウン。モデル選択で利用する想定
- **"toggle"** (Phase C1): bool 値のスイッチ(GUI 上は ON/OFF トグル)。`parse_field_value`
  でも bool 同等に扱う。`auto_load` 等で利用
- **"button"** (Phase C1): クリック時に `action_fn(controller, layer) -> None` を呼ぶ
  アクション項目。`keys` は不要(設定値を持たない)。Load Model ボタン等で利用
- **"label_readonly"** (Phase C1): 値表示のみ(編集不可)。`reactive_to` で示したレイヤの
  状態変化に追随して再描画される。直近処理時間平均/目安時間 表示等で利用

callback (`options_fn` / `action_fn`) の規約(R2-4):
- backend 経由の取得/更新のみを行う(UI 内部状態は触らない)
- 副作用の起点は backend(状態更新は notify 経由)
- 過度な抽象化はしない
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any, Callable, TYPE_CHECKING

from voice_translator.common.hw_info import ModelFit, assess_model_fit, detect_hw
from voice_translator.common.types import LayerKind, ModelInfo

if TYPE_CHECKING:
    from voice_translator.common.app_controller import AppController


# サポートする型(入力欄の振る舞いを切り替える)
FieldType = str  # "int" | "float" | "str" | "bool" | "dropdown" | "toggle" | "button" | "label_readonly"


# Phase C1 で追加した型集合(ダイアログ側 dispatch の switch に使う)
_TEXT_TYPES = ("int", "float", "str", "bool")
_NEW_TYPES = ("dropdown", "toggle", "button", "label_readonly")
ALL_FIELD_TYPES: tuple[str, ...] = _TEXT_TYPES + _NEW_TYPES


@dataclass(frozen=True)
class SettingField:
    """1つの設定項目。`ConfigStore` のネストキー列で値を指す。

    例: keys=("pipeline", "captured_queue_max_bytes") → cfg["pipeline"]["captured_queue_max_bytes"]

    Phase C1 で `options_fn` / `action_fn` / `reactive_to` を追加。
    """

    keys: tuple[str, ...]              # ConfigStore の get/set に渡すキー列。button のみ () でも可
    label: str                         # 入力欄の左側に出すラベル(日本語OK)
    field_type: FieldType              # ALL_FIELD_TYPES のいずれか
    default: Any = None                # 値が config 未設定だったときに使う表示初期値
    help_text: str = ""                # ラベル下のヘルプ(1行)
    applies_when_backend: str | None = None  # 「この名前のバックエンドが選ばれているときだけ表示」

    # Phase C1 追加(callback 系)。SettingField は frozen なので、必要な引数は呼び出し側が渡す。
    # 規約: ダイアログ側が `field.options_fn(controller, layer)` のように layer 文脈を渡す。
    options_fn: Callable[..., list[Any]] | None = None  # dropdown の選択肢
    action_fn: Callable[..., None] | None = None        # button のクリックハンドラ
    reactive_to: tuple[LayerKind, ...] = field(default_factory=tuple)  # label_readonly が反応するレイヤ


# 値を Python 型に変換する関数群(入力欄文字列 → 値)
# "toggle" は bool と同じ意味で、ダイアログ側がスイッチ widget を出す。
_PARSERS: dict[FieldType, Callable[[str], Any]] = {
    "int": lambda s: int(s.strip()),
    "float": lambda s: float(s.strip()),
    "str": lambda s: s,
    "bool": lambda s: s.strip().lower() in ("1", "true", "yes", "on"),
    "toggle": lambda s: s.strip().lower() in ("1", "true", "yes", "on"),
    "dropdown": lambda s: s,  # dropdown は str 値で持つ(モデル名等)
}


def parse_field_value(field_type: FieldType, raw: str) -> Any:
    """入力欄の文字列を `field_type` に従って変換する。失敗時は ValueError。

    `button` / `label_readonly` は値を持たないので呼ばれない(ダイアログが分岐する)。
    """
    parser = _PARSERS.get(field_type)
    if parser is None:
        raise ValueError(f"未対応の field_type: {field_type}")
    return parser(raw)


# ============================================================
# callback ヘルパ群(R2-4 規約: backend 経由の取得/更新のみ、UI 内部状態は触らない)
# Phase C1/C2 で利用。テスト容易性のためにモジュールトップに置く。
# 後段の `LAYER_SETTINGS` 構築時にエントリの action_fn 等から参照されるため、
# `LAYER_SETTINGS` より先に定義する必要がある。
# ============================================================
_FIT_ICONS: dict[ModelFit, str] = {
    ModelFit.OK: "✓",
    ModelFit.HEAVY: "⚠",
    ModelFit.INFEASIBLE: "✗",
    ModelFit.UNKNOWN: "?",
}


def format_model_option(m: ModelInfo, hw=None) -> str:
    """`ModelInfo` を「display_name (RAM/VRAM) + fit アイコン」の 1 行表示に整形する。

    `hw` 省略時は `detect_hw()` で取得。テストでは固定 `HwInfo` を渡してアイコンを検証する。
    """
    if hw is None:
        hw = detect_hw()
    parts = [m.display_name]
    res_parts: list[str] = []
    if m.ram_gb is not None:
        res_parts.append(f"RAM {m.ram_gb:.1f}GB")
    if m.vram_gb_if_gpu is not None:
        res_parts.append(f"VRAM {m.vram_gb_if_gpu:.1f}GB")
    if res_parts:
        parts.append(f"({' / '.join(res_parts)})")
    fit = assess_model_fit(
        model_ram_gb=m.ram_gb, model_vram_gb=m.vram_gb_if_gpu, hw=hw
    )
    parts.append(_FIT_ICONS[fit])
    return " ".join(parts)


def load_model_action(controller: "AppController", layer: LayerKind) -> None:
    """指定レイヤの backend をバックグラウンドでロードする(button.action_fn 用)。

    UI をブロックしないようバックグラウンドスレッドで `load_model_layer` を呼ぶ。
    ロード状況は backend / AppController の `subscribe` 経由で UI へ通知される。
    """
    threading.Thread(
        target=lambda: controller._safe_load_layer(layer),
        daemon=True,
        name=f"vt_dialog_load_{layer.value}",
    ).start()


def recent_durations_text(controller: "AppController", layer: LayerKind) -> str:
    """直近処理時間平均(ms)の整形済みテキスト(label_readonly 用)。

    データ無しなら「直近データなし」を返す。少数 1 桁で表示。
    """
    durations = controller.get_recent_durations(layer)
    if not durations:
        return "直近データなし"
    avg = sum(durations) / len(durations)
    return f"直近 {len(durations)} 件平均: {avg:.1f} ms"


# ============================================================
# 共通フィールド生成ヘルパ(全レイヤで共通の項目を量産する)
# ============================================================
def _auto_load_toggle(backend_name: str) -> "SettingField":
    """指定 backend が選ばれているときだけ表示される auto_load トグル(Phase C2)。"""
    return SettingField(
        keys=("backends_config", backend_name, "auto_load"),
        label="起動時に自動ロード",
        field_type="toggle",
        default=False,
        applies_when_backend=backend_name,
        help_text=(
            "ON にすると、アプリ起動時にこの backend を自動でロードする(既定 OFF)。"
            "OFF のままなら「▶ 開始」を押したときにロードする。"
        ),
    )


def _load_model_button(layer: LayerKind) -> "SettingField":
    """指定レイヤを手動ロードするボタン(Phase C2)。"""
    return SettingField(
        keys=(),
        label="モデルをロード",
        field_type="button",
        action_fn=load_model_action,
        help_text=(
            "今すぐこのレイヤの backend をバックグラウンドでロードする。"
            "ロード状況はステータスラベルに反映される。"
        ),
    )


def _recent_durations_label(layer: LayerKind) -> "SettingField":
    """直近処理時間平均の表示ラベル(Phase C2)。layer の状態変化に反応して更新される。"""
    return SettingField(
        keys=("_info", layer.value, "recent_durations"),  # 表示用のダミーキー
        label="直近処理時間",
        field_type="label_readonly",
        reactive_to=(layer,),
        help_text="完了した発話の直近 5 件の平均処理時間。",
    )


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
        _auto_load_toggle("soundcard"),
        _load_model_button(LayerKind.CAPTURE),
    ],
    LayerKind.VAD: [
        _auto_load_toggle("silero"),
        _load_model_button(LayerKind.VAD),
        _recent_durations_label(LayerKind.VAD),
    ],
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
        _auto_load_toggle("faster_whisper"),
        _load_model_button(LayerKind.ASR),
        _recent_durations_label(LayerKind.ASR),
    ],
    LayerKind.TRANSLATOR: [
        SettingField(
            keys=("pipeline", "translated_queue_size"),
            label="翻訳結果バッファ件数",
            field_type="int",
            default=10,
            help_text="翻訳済みテキストを TTS に渡すキューの上限件数。",
        ),
        _auto_load_toggle("nllb200"),
        _load_model_button(LayerKind.TRANSLATOR),
        _recent_durations_label(LayerKind.TRANSLATOR),
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
        _auto_load_toggle("sapi"),
        _load_model_button(LayerKind.TTS),
        _recent_durations_label(LayerKind.TTS),
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
        _auto_load_toggle("soundcard"),
        _load_model_button(LayerKind.OUTPUT),
        _recent_durations_label(LayerKind.OUTPUT),
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


