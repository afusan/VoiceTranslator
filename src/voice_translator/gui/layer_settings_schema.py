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
  でも bool 同等に扱う
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

from .i18n import tr

if TYPE_CHECKING:
    from voice_translator.common.app_controller import AppController


# サポートする型(入力欄の振る舞いを切り替える)
FieldType = str  # "int" | "float" | "str" | "bool" | "dropdown" | "toggle" | "button" | "label_readonly" | "password"


# Phase C1 / D2 で追加した型集合(ダイアログ側 dispatch の switch に使う)
_TEXT_TYPES = ("int", "float", "str", "bool")
_NEW_TYPES = ("dropdown", "toggle", "button", "label_readonly", "password")
ALL_FIELD_TYPES: tuple[str, ...] = _TEXT_TYPES + _NEW_TYPES


# Phase D: password 型の `keys` は ("__credential__", backend_name, key_name) 形式で
# 保存先を CredentialsStore に振り向ける。ConfigStore へは書かない。
CREDENTIAL_KEYS_MARKER: str = "__credential__"


@dataclass(frozen=True)
class SettingField:
    """1つの設定項目。`ConfigStore` のネストキー列で値を指す。

    例: keys=("pipeline", "captured_queue_max_bytes") → cfg["pipeline"]["captured_queue_max_bytes"]

    Phase C1 で `options_fn` / `action_fn` / `reactive_to` を追加。
    """

    keys: tuple[str, ...]              # ConfigStore の get/set に渡すキー列。button のみ () でも可
    label_key: str                     # ラベルの i18n キー(LayerSettingsDialog が tr() で解決)
    field_type: FieldType              # ALL_FIELD_TYPES のいずれか
    default: Any = None                # 値が config 未設定だったときに使う表示初期値
    help_key: str = ""                 # ラベル下のヘルプの i18n キー(空ならヘルプ無し)
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
    "password": lambda s: s,  # password も str。ダイアログ側で空欄=未編集として扱う
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
    """指定レイヤの backend をバックグラウンドで(再)ロードする(button.action_fn 用)。

    既にロード済みなら一度 evict してから作り直す(`reload_model_layer`)。これにより
    詳細ダイアログでモデル選択を変えた後、本ボタンで設定値を反映できる。
    UI をブロックしないようバックグラウンドスレッドで実行する。ロード状況は backend /
    AppController の `subscribe` 経由で UI へ通知される。
    """
    def _run() -> None:
        try:
            controller.reload_model_layer(layer)
        except Exception:  # noqa: BLE001
            # 失敗は backend の record_error + emit_status(NOT_DOWNLOADED) で吸収済み
            pass

    threading.Thread(
        target=_run,
        daemon=True,
        name=f"vt_dialog_load_{layer.value}",
    ).start()


def recent_durations_text(controller: "AppController", layer: LayerKind) -> str:
    """直近処理時間平均(ms)の整形済みテキスト(label_readonly 用)。

    データ無しなら「直近データなし」を返す。少数 1 桁で表示。
    """
    durations = controller.get_recent_durations(layer)
    if not durations:
        return tr("layer_settings.recent_durations.none")
    avg = sum(durations) / len(durations)
    return tr(
        "layer_settings.recent_durations.average",
        count=len(durations),
        avg=f"{avg:.1f}",
    )


# ============================================================
# 共通フィールド生成ヘルパ(全レイヤで共通の項目を量産する)
# ============================================================
def _load_model_button(layer: LayerKind) -> "SettingField":
    """指定レイヤを手動(再)ロードするボタン(Phase C2 / モデル切替時の反映に使う)。

    NOTE (2026-05-30): UI からは外した。代わりに ControlPanel の「↻ ロード」ボタンが
    全レイヤを一括 load する(設定変更時は dialog 保存時に自動 evict されるので、
    そのあと中央ボタン押下で反映される)。本ヘルパは将来の再導入に備えて残置。
    """
    return SettingField(
        keys=(),
        label_key="layer_settings.load_model.label",
        field_type="button",
        action_fn=load_model_action,
        help_key="layer_settings.load_model.help",
    )


def _faster_whisper_model_options(
    controller: "AppController", layer: LayerKind  # noqa: ARG001
) -> list[ModelInfo]:
    """faster-whisper の推奨モデル一覧を返す(dropdown の options_fn)。

    インスタンスを生成せずに class method 経由で取得する。これによりモデル選択肢が
    backend ロード前でも引ける(ダイアログ起動時に即時表示できる)。
    """
    from voice_translator.asr.faster_whisper_backend import FasterWhisperAsrBackend
    return FasterWhisperAsrBackend.recommended_models()


def _openai_whisper_model_options(
    controller: "AppController", layer: LayerKind  # noqa: ARG001
) -> list[ModelInfo]:
    """openai-whisper(公式)の推奨モデル一覧を返す。"""
    from voice_translator.asr.openai_whisper_backend import OpenAiWhisperAsrBackend
    return OpenAiWhisperAsrBackend.recommended_models()


def _nllb200_model_options(
    controller: "AppController", layer: LayerKind  # noqa: ARG001
) -> list[ModelInfo]:
    """NLLB-200 の推奨モデル一覧を返す(dropdown の options_fn)。

    既存 backend のインスタンスメソッド `list_recommended_models` をクラス側でも
    呼べるようにしているのは、UI が backend ロード前に問い合わせるため。
    """
    from voice_translator.translator.nllb200_backend import _RECOMMENDED_MODELS
    return list(_RECOMMENDED_MODELS)


def _piper_voice_options(
    controller: "AppController", layer: LayerKind  # noqa: ARG001
) -> list[ModelInfo]:
    """Piper TTS の推奨 voice 一覧(dropdown の options_fn)。

    voice 名そのものをモデル名として扱う(初回利用時に HF から DL される)。
    """
    from voice_translator.tts.piper_backend import RECOMMENDED_VOICES
    return [ModelInfo(name=v, display_name=v) for v in RECOMMENDED_VOICES]


def _elevenlabs_model_options(
    controller: "AppController", layer: LayerKind  # noqa: ARG001
) -> list[ModelInfo]:
    """ElevenLabs の TTS モデル一覧(dropdown の options_fn)。"""
    from voice_translator.tts.elevenlabs_backend import SUPPORTED_MODELS
    return [ModelInfo(name=m, display_name=m) for m in SUPPORTED_MODELS]


def _openai_tts_voice_options(
    controller: "AppController", layer: LayerKind  # noqa: ARG001
) -> list[ModelInfo]:
    """OpenAI TTS のプリメイド voice 一覧(dropdown の options_fn)。"""
    from voice_translator.tts.openai_tts_backend import SUPPORTED_VOICES
    return [ModelInfo(name=v, display_name=v) for v in SUPPORTED_VOICES]


def _openai_tts_model_options(
    controller: "AppController", layer: LayerKind  # noqa: ARG001
) -> list[ModelInfo]:
    """OpenAI TTS のモデル一覧(tts-1 / tts-1-hd)。"""
    from voice_translator.tts.openai_tts_backend import SUPPORTED_MODELS
    return [ModelInfo(name=m, display_name=m) for m in SUPPORTED_MODELS]


def _recent_durations_label(layer: LayerKind) -> "SettingField":
    """直近処理時間平均の表示ラベル(Phase C2)。layer の状態変化に反応して更新される。

    NOTE (2026-05-30): UI からは外した(ダイアログ内では確認しにくく、
    `logs/processtime.csv` でより精緻に追えるため)。`reactive_to` 機構と
    `recent_durations_text` ヘルパは将来別 UI で再利用できるよう残置。
    """
    return SettingField(
        keys=("_info", layer.value, "recent_durations"),  # 表示用のダミーキー
        label_key="layer_settings.recent_durations.label",
        field_type="label_readonly",
        reactive_to=(layer,),
        help_key="layer_settings.recent_durations.help",
    )


# ============================================================
# 各レイヤの設定項目一覧
# ============================================================
LAYER_SETTINGS: dict[LayerKind, list[SettingField]] = {
    LayerKind.CAPTURE: [
        SettingField(
            keys=("pipeline", "captured_queue_max_bytes"),
            label_key="layer_settings.pipeline.captured_queue_max_bytes.label",
            field_type="int",
            default=10_000_000,
            help_key="layer_settings.pipeline.captured_queue_max_bytes.help",
        ),
        # ProcTap(プロセス単位キャプチャ)の入力ゲイン。対象プロセスの再生音量が
        # 小さいと VAD が発話を拾えないため、内部で増幅できるようにする。
        SettingField(
            keys=("backends_config", "proctap", "input_gain"),
            label_key="layer_settings.backends_config.proctap.input_gain.label",
            field_type="float",
            default=1.0,
            applies_when_backend="proctap",
            help_key="layer_settings.backends_config.proctap.input_gain.help",
        ),
    ],
    LayerKind.VAD: [
        # Phase F1 で追加した代替 VAD 群。`applies_when_backend` でその backend が選ばれているときだけ
        # フィールドが出る。重複が見えるのは設計上の意図(ダイアログは選択中 backend で
        # フィルタする)。
        SettingField(
            keys=("backends_config", "webrtcvad", "aggressiveness"),
            label_key="layer_settings.backends_config.webrtcvad.aggressiveness.label",
            field_type="int",
            default=2,
            applies_when_backend="webrtcvad",
            help_key="layer_settings.backends_config.webrtcvad.aggressiveness.help",
        ),
        SettingField(
            keys=("backends_config", "webrtcvad", "frame_ms"),
            label_key="layer_settings.backends_config.webrtcvad.frame_ms.label",
            field_type="int",
            default=30,
            applies_when_backend="webrtcvad",
            help_key="layer_settings.backends_config.webrtcvad.frame_ms.help",
        ),
        SettingField(
            keys=("backends_config", "pyannote", "model_id"),
            label_key="layer_settings.backends_config.pyannote.model_id.label",
            field_type="str",
            default="pyannote/voice-activity-detection",
            applies_when_backend="pyannote",
            help_key="layer_settings.backends_config.pyannote.model_id.help",
        ),
        SettingField(
            keys=("backends_config", "pyannote", "device"),
            label_key="layer_settings.backends_config.pyannote.device.label",
            field_type="str",
            default="auto",
            applies_when_backend="pyannote",
            help_key="layer_settings.backends_config.pyannote.device.help",
        ),
        SettingField(
            keys=("backends_config", "pvcobra", "threshold"),
            label_key="layer_settings.backends_config.pvcobra.threshold.label",
            field_type="float",
            default=0.5,
            applies_when_backend="pvcobra",
            help_key="layer_settings.backends_config.pvcobra.threshold.help",
        ),
    ],
    LayerKind.ASR: [
        SettingField(
            keys=("pipeline", "recognized_queue_size"),
            label_key="layer_settings.pipeline.recognized_queue_size.label",
            field_type="int",
            default=10,
            help_key="layer_settings.pipeline.recognized_queue_size.help",
        ),
        SettingField(
            keys=("backends_config", "faster_whisper", "model_size"),
            label_key="layer_settings.backends_config.faster_whisper.model_size.label",
            field_type="dropdown",
            default="small",
            applies_when_backend="faster_whisper",
            options_fn=_faster_whisper_model_options,
            help_key="layer_settings.backends_config.faster_whisper.model_size.help",
        ),
        # openai-whisper(公式)用の同等項目。Whisper サイズ系は共通名だが、backend が
        # 別なので config キーも別系統(backends_config.openai_whisper.*)。
        SettingField(
            keys=("backends_config", "openai_whisper", "model_size"),
            label_key="layer_settings.backends_config.openai_whisper.model_size.label",
            field_type="dropdown",
            default="small",
            applies_when_backend="openai_whisper",
            options_fn=_openai_whisper_model_options,
            help_key="layer_settings.backends_config.openai_whisper.model_size.help",
        ),
        # OpenAI Whisper API(クラウド)用の項目。モデル名(現状 whisper-1 のみ)を露出。
        SettingField(
            keys=("backends_config", "openai_whisper_api", "model"),
            label_key="layer_settings.backends_config.openai_whisper_api.model.label",
            field_type="str",
            default="whisper-1",
            applies_when_backend="openai_whisper_api",
            help_key="layer_settings.backends_config.openai_whisper_api.model.help",
        ),
        # Google Cloud STT(クラウド)。auto 検出非対応のため、auto を選んだ時に
        # 何の言語で投げるかを「default_language」で指定する。
        SettingField(
            keys=("backends_config", "google_stt", "default_language"),
            label_key="layer_settings.backends_config.google_stt.default_language.label",
            field_type="str",
            default="eng",
            applies_when_backend="google_stt",
            help_key="layer_settings.backends_config.google_stt.default_language.help",
        ),
        # Deepgram(クラウド)。モデル名のみ露出(現状 nova-3)。
        SettingField(
            keys=("backends_config", "deepgram", "model"),
            label_key="layer_settings.backends_config.deepgram.model.label",
            field_type="str",
            default="nova-3",
            applies_when_backend="deepgram",
            help_key="layer_settings.backends_config.deepgram.model.help",
        ),
    ],
    LayerKind.TRANSLATOR: [
        SettingField(
            keys=("pipeline", "translated_queue_size"),
            label_key="layer_settings.pipeline.translated_queue_size.label",
            field_type="int",
            default=10,
            help_key="layer_settings.pipeline.translated_queue_size.help",
        ),
        SettingField(
            keys=("backends_config", "nllb200", "model_name"),
            label_key="layer_settings.backends_config.nllb200.model_name.label",
            field_type="dropdown",
            default="facebook/nllb-200-distilled-600M",
            applies_when_backend="nllb200",
            options_fn=_nllb200_model_options,
            help_key="layer_settings.backends_config.nllb200.model_name.help",
        ),
        # OpenAI GPT
        SettingField(
            keys=("backends_config", "openai_gpt", "model"),
            label_key="layer_settings.backends_config.openai_gpt.model.label",
            field_type="str",
            default="gpt-4o-mini",
            applies_when_backend="openai_gpt",
            help_key="layer_settings.backends_config.openai_gpt.model.help",
        ),
        # Anthropic Claude
        SettingField(
            keys=("backends_config", "anthropic_claude", "model"),
            label_key="layer_settings.backends_config.anthropic_claude.model.label",
            field_type="str",
            default="claude-haiku-4-5-20251001",
            applies_when_backend="anthropic_claude",
            help_key="layer_settings.backends_config.anthropic_claude.model.help",
        ),
    ],
    LayerKind.TTS: [
        SettingField(
            keys=("backends_config", "sapi", "rate"),
            label_key="layer_settings.backends_config.sapi.rate.label",
            field_type="int",
            default=180,
            applies_when_backend="sapi",
            help_key="layer_settings.backends_config.sapi.rate.help",
        ),
        # Piper TTS(ローカル、マルチ OS、ONNX)
        SettingField(
            keys=("backends_config", "piper", "voice_name"),
            label_key="layer_settings.backends_config.piper.voice_name.label",
            field_type="dropdown",
            default="en_US-amy-low",
            applies_when_backend="piper",
            options_fn=_piper_voice_options,
            help_key="layer_settings.backends_config.piper.voice_name.help",
        ),
        # ElevenLabs(クラウド、プリメイド voice)
        SettingField(
            keys=("backends_config", "elevenlabs", "voice_id"),
            label_key="layer_settings.backends_config.elevenlabs.voice_id.label",
            field_type="str",
            default="21m00Tcm4TlvDq8ikWAM",  # Rachel
            applies_when_backend="elevenlabs",
            help_key="layer_settings.backends_config.elevenlabs.voice_id.help",
        ),
        SettingField(
            keys=("backends_config", "elevenlabs", "model_id"),
            label_key="layer_settings.backends_config.elevenlabs.model_id.label",
            field_type="dropdown",
            default="eleven_multilingual_v2",
            applies_when_backend="elevenlabs",
            options_fn=_elevenlabs_model_options,
            help_key="layer_settings.backends_config.elevenlabs.model_id.help",
        ),
        # OpenAI TTS(クラウド、プリメイド 6 voice)
        SettingField(
            keys=("backends_config", "openai_tts", "voice"),
            label_key="layer_settings.backends_config.openai_tts.voice.label",
            field_type="dropdown",
            default="alloy",
            applies_when_backend="openai_tts",
            options_fn=_openai_tts_voice_options,
            help_key="layer_settings.backends_config.openai_tts.voice.help",
        ),
        SettingField(
            keys=("backends_config", "openai_tts", "model"),
            label_key="layer_settings.backends_config.openai_tts.model.label",
            field_type="dropdown",
            default="tts-1",
            applies_when_backend="openai_tts",
            options_fn=_openai_tts_model_options,
            help_key="layer_settings.backends_config.openai_tts.model.help",
        ),
        # Google Cloud TTS(クラウド、サービスアカウント JSON)
        SettingField(
            keys=("backends_config", "google_tts", "voice_name"),
            label_key="layer_settings.backends_config.google_tts.voice_name.label",
            field_type="str",
            default="",
            applies_when_backend="google_tts",
            help_key="layer_settings.backends_config.google_tts.voice_name.help",
        ),
        SettingField(
            keys=("backends_config", "google_tts", "default_language"),
            label_key="layer_settings.backends_config.google_tts.default_language.label",
            field_type="str",
            default="eng",
            applies_when_backend="google_tts",
            help_key="layer_settings.backends_config.google_tts.default_language.help",
        ),
    ],
    LayerKind.OUTPUT: [
        SettingField(
            keys=("pipeline", "synthesized_queue_max_bytes"),
            label_key="layer_settings.pipeline.synthesized_queue_max_bytes.label",
            field_type="int",
            default=5_000_000,
            help_key="layer_settings.pipeline.synthesized_queue_max_bytes.help",
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


