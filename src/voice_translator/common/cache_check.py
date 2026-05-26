"""モデルのキャッシュ有無を確認するヘルパ。

役割: GUI 起動時に「ローカルにモデルが揃っているか」を判定するために、
huggingface_hub の `try_to_load_from_cache` を使って軽量にチェックする。
ファイル読込はせず stat 程度のコストで返るため、UIブロックにならない。
"""

from __future__ import annotations

from .types import ModelStatus


def check_faster_whisper(model_size: str = "small") -> ModelStatus:
    """faster-whisper の指定サイズモデルがキャッシュ済みかを返す。

    Systran/faster-whisper-<size> の `model.bin` を見に行く。
    キャッシュ無 or 確認失敗時は NOT_DOWNLOADED。
    """
    try:
        from huggingface_hub import try_to_load_from_cache  # type: ignore
    except Exception:  # noqa: BLE001
        return ModelStatus.NOT_DOWNLOADED

    repo_id = f"Systran/faster-whisper-{model_size}"
    try:
        path = try_to_load_from_cache(repo_id, "model.bin")
    except Exception:  # noqa: BLE001
        return ModelStatus.NOT_DOWNLOADED
    return ModelStatus.LOADED if path else ModelStatus.NOT_DOWNLOADED


def check_nllb200(model_name: str = "facebook/nllb-200-distilled-600M") -> ModelStatus:
    """NLLB-200 モデルがキャッシュ済みかを返す。`config.json` を確認。"""
    try:
        from huggingface_hub import try_to_load_from_cache  # type: ignore
    except Exception:  # noqa: BLE001
        return ModelStatus.NOT_DOWNLOADED

    try:
        path = try_to_load_from_cache(model_name, "config.json")
    except Exception:  # noqa: BLE001
        return ModelStatus.NOT_DOWNLOADED
    return ModelStatus.LOADED if path else ModelStatus.NOT_DOWNLOADED


def check_silero() -> ModelStatus:
    """silero-vad は pip パッケージ同梱のため常に LOADED。"""
    return ModelStatus.LOADED


def check_sapi() -> ModelStatus:
    """SAPI(pyttsx3) は OS 側の機能、モデル DL は不要。常に LOADED 扱い。"""
    return ModelStatus.LOADED


def check_soundcard() -> ModelStatus:
    """soundcard はオーディオライブラリでモデル概念なし。常に LOADED。"""
    return ModelStatus.LOADED
