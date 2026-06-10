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
class CaptureKind(str, Enum):
    """音声取得の単位。`AudioCaptureBackend` の宣言と `CaptureSource` のメタ情報で使う。

    - DEVICE  : 物理デバイス単位(マイク / スピーカ ループバック等)。soundcard backend。
    - PROCESS : プロセス単位(per-process キャプチャ)。ProcTap 等の段階 2 で追加予定。
    """

    DEVICE = "device"
    PROCESS = "process"


@dataclass(frozen=True)
class CaptureSource:
    """取得元(マイク/スピーカLB/特定アプリ等)を表す識別情報。

    役割: AudioCaptureBackend.list_sources() の戻り値。GUI のプルダウン項目に使われる。
    `kind` は当該ソースがデバイス単位 / プロセス単位のどちらかを示す(GUI で kind 別の
    プルダウン構築や、表示の出し分けに使う)。既定は `DEVICE`(従来 backend の互換)。
    """

    source_id: str                          # バックエンド内で一意な識別子(デバイス名や process id 等)
    display_name: str                       # GUI 表示用ラベル
    is_loopback: bool = False               # スピーカ等の出力デバイスのループバックなら True
    kind: CaptureKind = CaptureKind.DEVICE  # 取得単位(段階 1 / 2026-06-05 で追加)


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
    想定遷移: INIT → (MISSING_CREDENTIALS or DOWNLOADING or LOADING) → LOADING → LOADED
    失敗系: NOT_DOWNLOADED(キャッシュ無 + DL失敗等)。

    - INIT:                 初期状態(まだロード処理を起動していない)。アプリ起動直後や
                            バックエンド差し替え直後に置かれる。
    - MISSING_CREDENTIALS:  クラウド backend で認証情報が未設定。Phase D で利用。
    - NOT_DOWNLOADED:       ロード試行に失敗(ローカルキャッシュ無 + DL失敗等)。
    - DOWNLOADING:          モデル DL 中(R-3)。初回起動・キャッシュ無の局面。
    - LOADING:              メモリへロード中。
    - LOADED:               メモリに読み込み済み(即使用可能)。
    """

    INIT = "Init"
    MISSING_CREDENTIALS = "Missing Credentials"
    NOT_DOWNLOADED = "Not Downloaded"
    DOWNLOADING = "Downloading..."
    LOADING = "Loading..."
    LOADED = "Loaded"


@dataclass(frozen=True)
class BackendCapabilities:
    """バックエンドの性能/対応特性を表すメタ情報。

    役割: GUI が選択時の表示や、PipelineCoordinator/ConfigStore がチェックに使う。
    クラウド対応の項目は Phase D(認証/同意 UX)と Phase C(UI バッジ)で参照される。
    """

    supported_languages: tuple[str, ...] = ()  # ISO 言語コード。空タプルは「すべて/不明」を意味する。
    requires_gpu: bool = False
    is_cloud: bool = False                     # クラウド(外部 API)backend か。GUI で ☁ バッジ表示。
    requires_credentials: bool = False         # API key 等の認証情報が必要か。Phase D で利用。
    service_name: str | None = None            # 同意ダイアログ等での表示名(例: "OpenAI Whisper API")
    terms_url: str | None = None               # 利用規約 URL。同意ダイアログで参照リンクを出す。
    notes: str = ""
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ModelInfo:
    """モデル選択肢を表すメタ情報。

    役割: backend の `list_recommended_models()` の戻り値。GUI のドロップダウンで
    リソース目安/推奨判定/DL 中のサイズ表示に使う。値が不明なら None。
    """

    name: str                                          # backend 内部での識別子(HF repo id 等)
    display_name: str                                  # GUI 表示名
    ram_gb: float | None = None                        # 概算 RAM 使用量(GB)
    vram_gb_if_gpu: float | None = None                # GPU 利用時の概算 VRAM(GB)
    download_size_gb: float | None = None              # 初回 DL サイズ(GB)。R-3 の DL 中表示に利用
    target_proc_ms_per_sec_audio: float | None = None  # 音声 1 秒あたりの目安処理時間(ms)


@dataclass(frozen=True)
class PipelineRestartEvent:
    """動作中デバイス変更に伴う自動 restart のライフサイクルイベント(P2)。

    役割: `AppController.add_restart_listener` で UI に届く通知。バナー表示
    (再開中… / 完了 / 失敗)の材料になる。
    """

    phase: str        # "started" | "completed" | "failed"
    device_key: str   # 契機となった devices キー("input" | "output")
    message: str = ""  # failed 時の理由(それ以外は空)


@dataclass(frozen=True)
class LayerStatusLine:
    """レイヤ 1 行分のステータス表示データ(整形前)。

    役割: `AppController.get_status_snapshot()` の戻り値要素。文字列への整形は
    UI 側(`gui/logic/status_summary.py`)の責務で、ここはデータのみを運ぶ。
    `dl_size_hint` は表示にそのまま連結する末尾文字列(例: `" (~2.9GB)"`。
    **先頭スペースを含む**)で、DOWNLOADING 以外のときは空文字。

    `disposition` は現在の編成でこのレイヤがどう扱われるか:
    - `"active"`   : 通常(このレイヤの backend が動く)
    - `"absorbed"` : 複合 backend に吸収(`absorbed_into` のレイヤの
                     `absorbed_backend` が代行。自レイヤの backend は使われない)
    - `"skipped"`  : 編成に載らない(text_only の TTS/Output 等。何も動かない)
    """

    layer: LayerKind
    backend_name: str
    status: ModelStatus
    dl_size_hint: str = ""
    disposition: str = "active"
    absorbed_into: str = ""      # 吸収先レイヤの value(例: "asr")
    absorbed_backend: str = ""   # 吸収先で実際に動く backend 名


@dataclass(frozen=True)
class ErrorRecord:
    """backend で発生したエラーの記録。

    役割: backend の `record_error()` が `get_recent_errors()` で返すリングバッファに積む。
    GUI のステータステキストボックス(Phase C/E)で表示する。
    """

    timestamp: float            # time.time() 値
    message: str                # 例外メッセージ
    exc_type: str               # 例外型名(例: "ConnectionError")
    context: str | None = None  # 任意の補足情報(例: "model load" / "transcribe")


# ============================================================
# 認証情報フロー(Phase E-2)
# ============================================================
@dataclass(frozen=True)
class CredentialField:
    """認証情報の入力欄スペック(backend が `credential_spec()` で宣言する)。

    1 行 = 1 入力欄。`secret=True` ならマスク表示(API key 等)、False なら平文
    (region コード等)。汎用 `CredentialDialog` がこの spec からフィールドを動的生成する。

    `field_type`:
    - `"text"` (既定): 通常の 1 行テキスト入力。`secret=True` でマスク表示。
    - `"file"`: ファイル選択ボタン付き入力(サービスアカウント JSON 等)。
      ファイル選択ダイアログで選んだ絶対パス文字列を CredentialsStore に保存する。
      `secret` は False が想定だが、True なら入力値もマスクされる。
    """

    key_name: str               # 内部 ID(`CredentialsStore` の key 名と直結)
    label: str                  # UI ラベル(日本語 OK)
    secret: bool = True         # True=マスク入力 / False=平文表示
    help_text: str = ""         # 入力欄下のヘルプ(1 行)
    field_type: str = "text"    # "text" / "file"
    file_extensions: tuple[tuple[str, str], ...] = ()  # field_type="file" 用: [(label, "*.json"), ...]


@dataclass(frozen=True)
class VerifyResult:
    """認証情報の疎通確認結果(`backend.verify_credentials()` の戻り値)。

    `ok=True` のときのみ ConfigStore に「verified=True」が永続化される。
    `message` はユーザが読めるメッセージ(成功時の voice 名表示や、失敗時の原因など)。
    """

    ok: bool
    message: str = ""
