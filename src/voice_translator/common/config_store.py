"""アプリ設定の永続化(YAML)。

役割: 選択中のバックエンド名・デバイス・言語ペア・ログ出力先などの設定値を
YAML ファイルとして保存・読込する。スキーマは緩く dict として扱う。
詳細は docs/design/Class.md を参照。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from .errors import FatalError


# MVP 既定値。GUI/各バックエンドが自身のキーを後で増やしていく。
DEFAULT_CONFIG: dict[str, Any] = {
    "languages": {
        "src": "auto",
        "tgt": "ja",
    },
    "devices": {
        "input": None,   # 未設定。起動時に GUI で選択させる。
        "output": None,
    },
    "backends": {
        "capture": "soundcard",
        "vad": "silero",
        "asr": "faster_whisper",
        "translator": "nllb200",
        "tts": "sapi",
        "output": "soundcard",
    },
    "log": {
        "directory": "./logs",        # ログ・jsonl・テキスト の出力先
        "level": "INFO",              # app.log のしきい値(DEBUG/INFO/WARNING/ERROR)。
                                       # SKIP は INFO レベルで出るため、ノイズを抑えたい場合は WARNING に上げる。
        "jsonl_enabled": True,        # 翻訳履歴 jsonl の出力 ON/OFF(機械処理向け)
        "src_text_enabled": False,    # 翻訳前テキスト soundsrc.txt の出力 ON/OFF(デバッグ用)
        "tgt_text_enabled": False,    # 翻訳後テキスト translated.txt の出力 ON/OFF(デバッグ用)
        "show_translation": True,     # GUI への翻訳テキスト表示 ON/OFF
        "process_time_enabled": False,  # 各レイヤ処理時間の CSV 出力 ON/OFF(プロファイル用)
    },
    "latency": {
        "warn_threshold_sec": 5.0,  # これを超えたら WARN
    },
    "notifications": {
        # 同じ (stage, 例外型) のエラー通知を集約・抑制する時間窓(秒)。
        # 例: 5.0 → 同種エラーは 5 秒に 1 度しか UI に通知しない。0 で無効化(全件通知)。
        # 抑制された件数は次の通知に suppressed=N として乗る(UI 側で「裏で N 件起きた」を表示可)。
        # ログ(app.log)には抑制せず全件記録される。
        "throttle_sec": 5.0,
    },
    "pipeline": {
        # ステージ間バッファ(キュー)の容量。あふれた場合は古いものから自動退避。
        # PCM 系(captured/synthesized)は **合計バイト数で制限**。1発話=PCM長×4byte。
        # 16kHz×float32 換算: 10MB ≒ 約 156 秒分、5MB ≒ 約 78 秒分。
        # 「設定値を超えるまでは積み、超えたら先頭を消す」方針なので、運用中に若干超えることがある。
        "captured_queue_max_bytes": 10_000_000,   # Input → ASR(VAD 出力 PCM)
        "synthesized_queue_max_bytes": 5_000_000,  # TTS → Output(合成 PCM)
        # テキスト系(recognized/translated)は **発話件数で制限**(中身が小さいためバイト換算は冗長)。
        "recognized_queue_size": 10,              # ASR → Translator(認識テキスト)
        "translated_queue_size": 10,              # Translator → TTS(翻訳テキスト)
        # Phase E: backend が RecoverableError を投げたときのリトライ機構。
        # max_retries=3 + base 0.5s/max 8.0s で「0.5s, 1.0s, 2.0s」の指数バックオフ。
        # 全失敗で復帰不能と判定しパイプライン停止。FatalError は初回で即停止。
        "max_retries": 3,
        "retry_base_sec": 0.5,
        "retry_max_sec": 8.0,
        # ステージ間データのダンプ機能(検証/再現用)。
        # 有効時、各ステージ出力を <directory>/<run_id>/seq_NNNN_<stage>.{wav,json} に書き出す。
        # 単体ランナー(voice_translator.dev.runner_*)の入力として使う。
        # 詳細は docs/design/feature-dev-runners-and-dump/Plan.md を参照。
        "dump": {
            "enabled": False,                                       # ON/OFF
            "directory": "./logs/dumps",                            # 出力ルート(run_id 配下に書く)
            "stages": ["vad", "asr", "translate", "tts"],          # 書き出す対象
            "max_runs": 20,                                         # 古い run の自動掃除上限(0で無効)
        },
    },
    # 各バックエンド固有の設定値(GUI公開はまだ。手動で config.yaml 編集)
    # 全 backend 共通の新キー(Phase B):
    #   `auto_load: bool`(既定 False) — 起動時にこの backend を選択中のレイヤを自動ロード。
    #     ユーザは詳細ダイアログから手動 ON にする。OFF のままなら起動ボタン押下時にロードする。
    "backends_config": {
        "soundcard": {
            "auto_load": False,
        },
        "sapi": {
            "auto_load": False,
            "rate": 180,  # 読み上げ速度(WPM相当)。早口にするなら 220 等。
        },
        "silero": {
            "auto_load": False,
            # 発話区切り検出の細部。長文連続発話(ニュース読み上げ等)で 1 発話が肥大化して
            # 翻訳/TTS が破綻するのを避けるため、特に max_speech_sec の上限が重要。
            "threshold": 0.5,         # speech probability の判定しきい値(0〜1)
            "min_silence_ms": 500,    # 発話終了とみなす無音期間(ms)。下げると早く区切れる
            "speech_pad_ms": 100,     # 発話前後の余白(ms)
            "max_speech_sec": 8.0,    # 1 発話の最大長(秒)。超えたら強制区切り。0 で無効化
        },
        "faster_whisper": {
            "auto_load": False,
            # device: "auto" / "cuda" / "cpu"。auto なら CUDA があれば自動で使う(MPS は未対応)。
            # compute_type: "auto" / "int8" / "float16" / "int8_float16" 等。auto なら device に応じて
            #   GPU=int8_float16, CPU=int8 を選ぶ。
            "device": "auto",
            "compute_type": "auto",
            # model_size: tiny / base / small / medium / large-v3 等。Whisper のサイズ別 repo に
            #   対応する。GUI の詳細ダイアログから dropdown で選択可。値変更後にレイヤを
            #   再ロードしないと新しいサイズが反映されない(詳細ダイアログの「(再)ロード」ボタン)。
            "model_size": "small",
        },
        "nllb200": {
            "auto_load": False,
            # device: "auto" / "cuda" / "mps" / "cpu"。auto なら cuda → mps → cpu の順で使う。
            "device": "auto",
        },
    },
    # Phase D で使うクラウド backend 同意の永続化。
    # 構造: { "<backend_name>": True/False, "suppress_dialogs": bool }
    "consents": {
        # 一括 OFF。ユーザが「今後表示しない」を選んだとき True。Phase D で読まれる。
        "suppress_dialogs": False,
    },
    # Phase D / R2-7: 認証情報の保管経路を強制切替するフラグ。
    # - False(既定): OS keychain を試し、失敗時に平文ファイルへ fallback
    # - True       : 平文ファイルだけを使う(開発者ローカルの実 API 検証用)
    "credentials": {
        "use_local_file": False,
    },
}


class ConfigStore:
    """設定値の保持と YAML 永続化を担うクラス。

    役割: in-memory の dict として設定を保持し、`save()`/`load()` で
    YAML ファイルと往復する。値アクセスは `get/set` で行う。
    """

    def __init__(self, path: Path | str, *, data: dict[str, Any] | None = None) -> None:
        self._path = Path(path)
        self._data: dict[str, Any] = data if data is not None else _deepcopy(DEFAULT_CONFIG)

    @property
    def path(self) -> Path:
        """設定ファイルのパス。"""
        return self._path

    @property
    def data(self) -> dict[str, Any]:
        """生の設定 dict(参照)。直接書き換えるよりは set() を推奨。"""
        return self._data

    def get(self, *keys: str, default: Any = None) -> Any:
        """ネストキーで値を取得。途中で見つからなければ default。"""
        node: Any = self._data
        for k in keys:
            if not isinstance(node, dict) or k not in node:
                return default
            node = node[k]
        return node

    def set(self, *keys_and_value: Any) -> None:
        """ネストキーで値を設定。最後の引数が値、それ以外はキー。

        例: store.set("languages", "src", "en")
        """
        if len(keys_and_value) < 2:
            raise ValueError("少なくとも1つのキーと値が必要です")
        *keys, value = keys_and_value
        node = self._data
        for k in keys[:-1]:
            if k not in node or not isinstance(node[k], dict):
                node[k] = {}
            node = node[k]
        node[keys[-1]] = value

    def save(self) -> None:
        """現在の設定を YAML ファイルに書き出す。"""
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with self._path.open("w", encoding="utf-8") as f:
                yaml.safe_dump(self._data, f, allow_unicode=True, sort_keys=False)
        except OSError as e:
            raise FatalError(f"設定ファイルを書き出せません: {self._path}", cause=e) from e

    def load(self) -> None:
        """YAML ファイルから読み込み、内部状態を置き換える。

        ファイル不存在の場合は既定値で初期化(=何もしない)。
        パース失敗は FATAL。
        """
        if not self._path.exists():
            return
        try:
            with self._path.open("r", encoding="utf-8") as f:
                loaded = yaml.safe_load(f) or {}
        except (OSError, yaml.YAMLError) as e:
            raise FatalError(f"設定ファイルの読込に失敗: {self._path}", cause=e) from e
        if not isinstance(loaded, dict):
            raise FatalError(f"設定ファイルの構造が不正(dictではない): {self._path}")
        self._data = _merge_defaults(DEFAULT_CONFIG, loaded)


def _deepcopy(d: dict[str, Any]) -> dict[str, Any]:
    """yaml.safe_dump → safe_load でディープコピー相当を作る(外部依存を避ける用途)。"""
    return yaml.safe_load(yaml.safe_dump(d))


def _merge_defaults(defaults: dict[str, Any], loaded: dict[str, Any]) -> dict[str, Any]:
    """既定値と読み込み結果をマージ。loaded を優先しつつ、未指定キーは defaults で補う。"""
    merged: dict[str, Any] = {}
    keys = set(defaults.keys()) | set(loaded.keys())
    for k in keys:
        d_val = defaults.get(k)
        l_val = loaded.get(k)
        if isinstance(d_val, dict) and isinstance(l_val, dict):
            merged[k] = _merge_defaults(d_val, l_val)
        elif k in loaded:
            merged[k] = l_val
        else:
            merged[k] = d_val
    return merged
