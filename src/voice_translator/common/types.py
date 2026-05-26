"""パイプライン全体で共有する型定義。

役割: 音声データ/デバイス情報/バックエンドのケイパビリティ等、
複数レイヤで参照される型エイリアス・データクラスを集約する。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

import numpy as np

# ============================================================
# 音声データ型
# ============================================================
# 内部標準フォーマット: 16kHz / mono / float32 [-1.0, 1.0]
# numpy.ndarray の dtype=float32, shape=(n_samples,) を想定。
PcmChunk = np.ndarray  # 型エイリアス(numpy がジェネリクス未対応のため alias のみ)

INTERNAL_SAMPLE_RATE: int = 16_000
INTERNAL_CHANNELS: int = 1
INTERNAL_DTYPE = np.float32


# ============================================================
# レイヤ識別
# ============================================================
class LayerKind(str, Enum):
    """バックエンドが属するパイプラインレイヤ。BackendRegistry のキーにも使う。"""

    CAPTURE = "capture"
    VAD = "vad"
    ASR = "asr"
    TRANSLATOR = "translator"
    TTS = "tts"
    OUTPUT = "output"


# ============================================================
# デバイス/ソース情報
# ============================================================
@dataclass(frozen=True)
class CaptureSource:
    """取得元(マイク/スピーカLB/特定アプリ等)を表す識別情報。

    役割: AudioCaptureBackend.list_sources() の戻り値。GUI のプルダウン項目に使われる。
    """

    source_id: str             # バックエンド内で一意な識別子(デバイス名や process id 等)
    display_name: str          # GUI 表示用ラベル
    is_loopback: bool = False  # スピーカ等の出力デバイスのループバックなら True


@dataclass(frozen=True)
class OutputDevice:
    """再生先デバイスを表す識別情報。

    役割: AudioOutputBackend.list_devices() の戻り値。
    """

    device_id: str
    display_name: str


# ============================================================
# バックエンド メタ情報
# ============================================================
class ModelStatus(str, Enum):
    """バックエンドモデルの状態(UI表示用)。表示文字列は英語固定。

    役割: GUI でレイヤ別の準備状況を表すラベル。
    - NOT_DOWNLOADED: ローカルキャッシュに無い(初回起動でDLが必要)。
    - LOADING:        DL中 or メモリへロード中。
    - LOADED:         メモリに読み込み済み or キャッシュ済みで即ロード可能。
    """

    NOT_DOWNLOADED = "Not Downloaded"
    LOADING = "Loading..."
    LOADED = "Loaded"


@dataclass(frozen=True)
class BackendCapabilities:
    """バックエンドの性能/対応特性を表すメタ情報。

    役割: GUI が選択時の表示や、PipelineCoordinator/ConfigStore がチェックに使う。
    必要に応じてフィールドを追加していく(現状は最低限)。
    """

    supported_languages: tuple[str, ...] = ()  # ISO 言語コード。空タプルは「すべて/不明」を意味する。
    requires_gpu: bool = False
    notes: str = ""
    extra: dict[str, Any] = field(default_factory=dict)
