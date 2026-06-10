"""GoogleCloudTtsBackend の large テスト(実 GCP TTS 呼び出し)。

実行条件:
- `local.secrets` に `google_tts.credentials_path`(実 JSON ファイルパス)が保存されている
- `uv sync --extra tts-google` 済み
- ネットワーク接続あり、GCP プロジェクトで Text-to-Speech API 有効化済み
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import numpy as np
import pytest


# find_spec はサブモジュール指定時に親パッケージを import するため、
# `google.cloud` 自体が無い環境(extras 未同期)では ModuleNotFoundError になる。
# 本ファイルの方針は「extras 未インストールなら skip」なので False に縮退する。
try:
    _GOOGLE_TTS_AVAILABLE = (
        importlib.util.find_spec("google.cloud.texttospeech") is not None
    )
except ModuleNotFoundError:
    _GOOGLE_TTS_AVAILABLE = False


def _load_real_credentials_path() -> str | None:
    sec_path = Path(__file__).resolve().parents[1] / "local.secrets"
    if not sec_path.exists():
        return None
    try:
        data = json.loads(sec_path.read_text(encoding="utf-8"))
    except Exception:
        return None
    val = data.get("google_tts", {}).get("credentials_path", "")
    if not val or val == "xxxxx":
        return None
    if not Path(val).exists():
        return None
    return val


_CREDS_PATH = _load_real_credentials_path()


@pytest.mark.large
@pytest.mark.skipif(
    not _GOOGLE_TTS_AVAILABLE,
    reason="google-cloud-texttospeech 未インストール(`uv sync --extra tts-google`)",
)
@pytest.mark.skipif(
    _CREDS_PATH is None,
    reason="google_tts.credentials_path が local.secrets に未設定 / ファイル不在",
)
class TestGoogleCloudTtsReal:
    def test_verify_real_credentials_ok(self) -> None:
        from voice_translator.tts.google_cloud_tts_backend import GoogleCloudTtsBackend

        result = GoogleCloudTtsBackend.verify_credentials(
            {"credentials_path": _CREDS_PATH}
        )
        assert result.ok, f"verify 失敗: {result.message}"

    def test_synthesize_returns_pcm(self) -> None:
        from voice_translator.tts.google_cloud_tts_backend import GoogleCloudTtsBackend

        backend = GoogleCloudTtsBackend(credentials_path=_CREDS_PATH)
        pcm, sr = backend.synthesize("Hello from Google Cloud TTS.", "en")
        assert isinstance(pcm, np.ndarray)
        assert pcm.dtype == np.float32
        assert pcm.size > 0
        assert sr == 16000
