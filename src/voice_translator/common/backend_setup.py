"""デフォルトのバックエンドを BackendRegistry に登録するヘルパ。

役割: アプリ起動時に MVP の標準バックエンド(soundcard/silero/faster-whisper/
NLLB-200/SAPI/soundcard)をレイヤごとに登録する。`config` を渡すと、各バックエンド
固有のパラメータ(SAPI rate 等)を `config.yaml` から読み込んで反映する。
後で別実装を追加するときはここを拡張する。
"""

from __future__ import annotations

from typing import Any

from .backend_registry import BackendRegistry
from .config_store import ConfigStore
from .types import BackendCapabilities, LayerKind


def register_default_backends(
    registry: BackendRegistry,
    config: ConfigStore | None = None,
) -> None:
    """MVP の標準バックエンドを一括登録する。

    `config` を渡すと、SAPI rate などのバックエンド固有設定を反映する。
    渡さない場合は各バックエンドのコンストラクタ既定値が使われる。
    """
    from voice_translator.asr.faster_whisper_backend import FasterWhisperAsrBackend
    from voice_translator.capture.soundcard_backend import SoundcardCaptureBackend
    from voice_translator.output.soundcard_backend import SoundcardOutputBackend
    from voice_translator.translator.nllb200_backend import Nllb200TranslatorBackend
    from voice_translator.tts.sapi_backend import SapiTtsBackend
    from voice_translator.vad.silero_backend import SileroVadBackend
    # Phase F1 で追加した VAD 群。依存は `[project.optional-dependencies].vad-extra`(opt-in)。
    # backend クラス自体の import は実依存(webrtcvad/pyannote.audio/pvcobra)を引かないので、
    # ここで import しても vad-extra 未インストール環境を壊さない。実際の依存読込はインスタンス
    # 生成時に各 backend の `__init__` 内で行う。
    from voice_translator.vad.webrtc_backend import WebRtcVadBackend
    from voice_translator.vad.pyannote_backend import PyannoteVadBackend
    from voice_translator.vad.pvcobra_backend import PvcobraVadBackend

    registry.register(LayerKind.CAPTURE, "soundcard", SoundcardCaptureBackend)

    # Silero VAD は config から発話区切り関連パラメータを読み込む
    vad_threshold = _read_float(
        config, ("backends_config", "silero", "threshold"), default=0.5
    )
    vad_min_silence_ms = _read_int(
        config, ("backends_config", "silero", "min_silence_ms"), default=500
    )
    vad_speech_pad_ms = _read_int(
        config, ("backends_config", "silero", "speech_pad_ms"), default=100
    )
    vad_max_speech_sec = _read_float(
        config, ("backends_config", "silero", "max_speech_sec"), default=8.0
    )
    registry.register(
        LayerKind.VAD,
        "silero",
        lambda: SileroVadBackend(
            threshold=vad_threshold,
            min_silence_ms=vad_min_silence_ms,
            speech_pad_ms=vad_speech_pad_ms,
            max_speech_sec=vad_max_speech_sec,
        ),
        backend_cls=SileroVadBackend,
    )

    # ----------------------------------------------------------
    # Phase F1: 追加 VAD backend(WebRTC / pyannote / Picovoice Cobra)
    # ----------------------------------------------------------
    # WebRTC VAD: 無認証ローカル軽量。silero フォールバック用。
    wv_aggressiveness = _read_int(
        config, ("backends_config", "webrtcvad", "aggressiveness"), default=2
    )
    wv_frame_ms = _read_int(
        config, ("backends_config", "webrtcvad", "frame_ms"), default=30
    )
    wv_min_speech_ms = _read_int(
        config, ("backends_config", "webrtcvad", "min_speech_ms"), default=60
    )
    wv_min_silence_ms = _read_int(
        config, ("backends_config", "webrtcvad", "min_silence_ms"), default=500
    )
    wv_speech_pad_ms = _read_int(
        config, ("backends_config", "webrtcvad", "speech_pad_ms"), default=100
    )
    wv_max_speech_sec = _read_float(
        config, ("backends_config", "webrtcvad", "max_speech_sec"), default=8.0
    )
    registry.register(
        LayerKind.VAD,
        "webrtcvad",
        lambda: WebRtcVadBackend(
            aggressiveness=wv_aggressiveness,
            frame_ms=wv_frame_ms,
            min_speech_ms=wv_min_speech_ms,
            min_silence_ms=wv_min_silence_ms,
            speech_pad_ms=wv_speech_pad_ms,
            max_speech_sec=wv_max_speech_sec,
        ),
        backend_cls=WebRtcVadBackend,
        capabilities=BackendCapabilities(
            notes="webrtcvad (C 実装)。極軽量、ルールベース。"
        ),
    )

    # pyannote.audio: HF gated model + neural。重いが精度が出る。
    py_model_id = _read_str(
        config,
        ("backends_config", "pyannote", "model_id"),
        default="pyannote/segmentation-3.0",
    )
    py_device = _read_str(
        config, ("backends_config", "pyannote", "device"), default="auto"
    )
    py_min_speech_ms = _read_int(
        config, ("backends_config", "pyannote", "min_speech_ms"), default=200
    )
    py_min_silence_ms = _read_int(
        config, ("backends_config", "pyannote", "min_silence_ms"), default=500
    )
    py_max_speech_sec = _read_float(
        config, ("backends_config", "pyannote", "max_speech_sec"), default=8.0
    )
    # HF token は CredentialsStore から読む(平文 config に書かせない)。未設定なら None。
    registry.register(
        LayerKind.VAD,
        "pyannote",
        lambda: PyannoteVadBackend(
            hf_token=_get_credential(config, "pyannote", "hf_token"),
            model_id=py_model_id,
            device=py_device,
            min_speech_ms=py_min_speech_ms,
            min_silence_ms=py_min_silence_ms,
            max_speech_sec=py_max_speech_sec,
        ),
        backend_cls=PyannoteVadBackend,
        capabilities=BackendCapabilities(
            requires_gpu=False,
            requires_credentials=True,
            service_name="pyannote.audio (HuggingFace)",
            terms_url="https://huggingface.co/pyannote/segmentation-3.0",
            notes="pyannote.audio 4.x VAD pipeline。segmentation-3.0 を基底に構築。HF token + 利用同意が必要。",
        ),
    )

    # Picovoice Cobra: ローカル + アクセスキー認証(クラウドとは別パターン)。
    pc_threshold = _read_float(
        config, ("backends_config", "pvcobra", "threshold"), default=0.5
    )
    pc_min_speech_ms = _read_int(
        config, ("backends_config", "pvcobra", "min_speech_ms"), default=64
    )
    pc_min_silence_ms = _read_int(
        config, ("backends_config", "pvcobra", "min_silence_ms"), default=500
    )
    pc_speech_pad_ms = _read_int(
        config, ("backends_config", "pvcobra", "speech_pad_ms"), default=100
    )
    pc_max_speech_sec = _read_float(
        config, ("backends_config", "pvcobra", "max_speech_sec"), default=8.0
    )
    registry.register(
        LayerKind.VAD,
        "pvcobra",
        lambda: PvcobraVadBackend(
            access_key=_get_credential(config, "pvcobra", "access_key"),
            threshold=pc_threshold,
            min_speech_ms=pc_min_speech_ms,
            min_silence_ms=pc_min_silence_ms,
            speech_pad_ms=pc_speech_pad_ms,
            max_speech_sec=pc_max_speech_sec,
        ),
        backend_cls=PvcobraVadBackend,
        capabilities=BackendCapabilities(
            is_cloud=False,
            requires_credentials=True,
            service_name="Picovoice Cobra",
            terms_url="https://picovoice.ai/docs/cobra/",
            notes="pvcobra (C 実装)。ローカル動作だがアクセスキーが必要。",
        ),
    )

    # faster-whisper の model_size / device / compute_type を config から取る。
    # model_size は GUI 詳細ダイアログの dropdown から切り替え可能(Phase C 拡張)。
    fw_model_size = _read_str(
        config, ("backends_config", "faster_whisper", "model_size"), default="small"
    )
    fw_device = _read_str(
        config, ("backends_config", "faster_whisper", "device"), default="auto"
    )
    fw_compute_type = _read_str(
        config, ("backends_config", "faster_whisper", "compute_type"), default="auto"
    )
    registry.register(
        LayerKind.ASR,
        "faster_whisper",
        lambda: FasterWhisperAsrBackend(
            model_size=fw_model_size,
            device=fw_device,
            compute_type=fw_compute_type,
        ),
    )

    # NLLB-200 の device を config から取る
    nllb_device = _read_str(
        config, ("backends_config", "nllb200", "device"), default="auto"
    )
    registry.register(
        LayerKind.TRANSLATOR,
        "nllb200",
        lambda: Nllb200TranslatorBackend(device=nllb_device),
    )

    # SAPI は config から rate を取って渡す(設定なしなら既定 180)
    sapi_rate = _read_int(config, ("backends_config", "sapi", "rate"), default=180)
    registry.register(
        LayerKind.TTS, "sapi", lambda: SapiTtsBackend(rate=sapi_rate)
    )

    registry.register(LayerKind.OUTPUT, "soundcard", SoundcardOutputBackend)


def _read_int(config: ConfigStore | None, keys: tuple[str, ...], *, default: int) -> int:
    """config から int 値を取り出す。失敗時は default。"""
    if config is None:
        return default
    value: Any = config.get(*keys, default=default)
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _read_float(config: ConfigStore | None, keys: tuple[str, ...], *, default: float) -> float:
    """config から float 値を取り出す。失敗時は default。"""
    if config is None:
        return default
    value: Any = config.get(*keys, default=default)
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _read_str(config: ConfigStore | None, keys: tuple[str, ...], *, default: str) -> str:
    """config から str 値を取り出す。空文字や None は default に置換。"""
    if config is None:
        return default
    value: Any = config.get(*keys, default=default)
    if value is None:
        return default
    try:
        s = str(value).strip()
    except Exception:  # noqa: BLE001
        return default
    return s or default


def _get_credential(
    config: ConfigStore | None, backend: str, key: str
) -> str | None:
    """CredentialsStore から指定 backend / key の値を取得する。

    factory 内で呼ぶことで、backend インスタンス生成時の最新値を毎回反映できる
    (詳細ダイアログから認証情報を更新したあと、`reload_model_layer` で新値で
    作り直される)。
    """
    from .credentials import CredentialsStore

    use_local = bool(
        (config.get("credentials", "use_local_file", default=False) if config else False)
    )
    try:
        store = CredentialsStore(use_local_file=use_local)
        return store.get(backend, key)
    except Exception:  # noqa: BLE001
        return None
