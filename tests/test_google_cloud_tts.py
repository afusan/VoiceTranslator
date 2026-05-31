"""GoogleCloudTtsBackend の単体テスト(small)。

google.cloud.texttospeech と google.oauth2.service_account を sys.modules で
差し替えて検証。verify_credentials は test_credential_flow.py 側でカバー、
こちらは synthesize の挙動と language code マッピングを確認。
"""

from __future__ import annotations

import sys
from unittest.mock import MagicMock

import numpy as np
import pytest

from voice_translator.common.errors import FatalError, RecoverableError, SkipError


@pytest.fixture()
def fake_google(monkeypatch, tmp_path):
    """google.cloud.texttospeech + google.oauth2.service_account をモック差し替え、
    ダミー JSON ファイルパスを返す。"""
    json_path = tmp_path / "fake_sa.json"
    json_path.write_text('{"type": "service_account"}', encoding="utf-8")

    fake_tts = MagicMock()
    # AudioEncoding / SynthesisInput / VoiceSelectionParams / AudioConfig は MagicMock として
    # 呼び出し可能(中身は assert しない)
    fake_tts.AudioEncoding = MagicMock()
    fake_tts.AudioEncoding.LINEAR16 = "LINEAR16"

    fake_client = MagicMock(name="tts_client")
    # 既定: 200 OK 相当(audio_content に PCM)
    pcm_bytes = np.zeros(800, dtype=np.int16).tobytes()
    fake_response = MagicMock()
    fake_response.audio_content = pcm_bytes
    fake_client.synthesize_speech = MagicMock(return_value=fake_response)
    fake_tts.TextToSpeechClient = MagicMock(return_value=fake_client)

    fake_oauth_sa = MagicMock()
    fake_oauth_sa.Credentials = MagicMock()
    fake_oauth_sa.Credentials.from_service_account_file = MagicMock(
        return_value=MagicMock(name="creds")
    )

    fake_oauth_module = MagicMock()
    fake_oauth_module.service_account = fake_oauth_sa

    fake_cloud = MagicMock()
    fake_cloud.texttospeech = fake_tts

    monkeypatch.setitem(sys.modules, "google", MagicMock())
    monkeypatch.setitem(sys.modules, "google.cloud", fake_cloud)
    monkeypatch.setitem(sys.modules, "google.cloud.texttospeech", fake_tts)
    monkeypatch.setitem(sys.modules, "google.oauth2", fake_oauth_module)
    monkeypatch.setitem(sys.modules, "google.oauth2.service_account", fake_oauth_sa)
    return fake_tts, fake_client, str(json_path)


class TestSupportedOutputLanguages:
    def test_includes_major_languages(self) -> None:
        from voice_translator.tts.google_cloud_tts_backend import GoogleCloudTtsBackend
        langs = GoogleCloudTtsBackend.supported_output_languages()
        for code in ("en", "ja", "fr", "de", "es", "zh", "ko"):
            assert code in langs


class TestInitialization:
    def test_no_credentials_sets_missing(self, fake_google) -> None:
        from voice_translator.common.types import ModelStatus
        from voice_translator.tts.google_cloud_tts_backend import GoogleCloudTtsBackend
        backend = GoogleCloudTtsBackend(credentials_path=None)
        assert backend.get_status() == ModelStatus.MISSING_CREDENTIALS

    def test_with_credentials_loaded(self, fake_google) -> None:
        from voice_translator.common.types import ModelStatus
        from voice_translator.tts.google_cloud_tts_backend import GoogleCloudTtsBackend
        _, _, json_path = fake_google
        backend = GoogleCloudTtsBackend(credentials_path=json_path)
        assert backend.get_status() == ModelStatus.LOADED


class TestSynthesize:
    def test_empty_text_raises_skip(self, fake_google) -> None:
        from voice_translator.tts.google_cloud_tts_backend import GoogleCloudTtsBackend
        _, _, json_path = fake_google
        backend = GoogleCloudTtsBackend(credentials_path=json_path)
        with pytest.raises(SkipError):
            backend.synthesize("", "en")

    def test_returns_float32_pcm_16khz(self, fake_google) -> None:
        from voice_translator.tts.google_cloud_tts_backend import GoogleCloudTtsBackend
        _, _, json_path = fake_google
        backend = GoogleCloudTtsBackend(credentials_path=json_path)
        pcm, sr = backend.synthesize("hello", "en")
        assert isinstance(pcm, np.ndarray)
        assert pcm.dtype == np.float32
        assert sr == 16000

    def test_no_credentials_raises_fatal(self, fake_google) -> None:
        from voice_translator.tts.google_cloud_tts_backend import GoogleCloudTtsBackend
        backend = GoogleCloudTtsBackend(credentials_path=None)
        with pytest.raises(FatalError, match="認証情報"):
            backend.synthesize("hi", "en")

    def test_permission_denied_raises_fatal(self, fake_google) -> None:
        """PERMISSION_DENIED / UNAUTHENTICATED は Fatal(再試行しても意味なし)。"""
        _, fake_client, json_path = fake_google
        fake_client.synthesize_speech.side_effect = RuntimeError(
            "PERMISSION_DENIED: API not enabled"
        )
        from voice_translator.tts.google_cloud_tts_backend import GoogleCloudTtsBackend
        backend = GoogleCloudTtsBackend(credentials_path=json_path)
        with pytest.raises(FatalError, match="認証"):
            backend.synthesize("hi", "en")

    def test_other_error_raises_recoverable(self, fake_google) -> None:
        """通信障害など一時的エラーは Recoverable(リトライ対象)。"""
        _, fake_client, json_path = fake_google
        fake_client.synthesize_speech.side_effect = RuntimeError("DEADLINE_EXCEEDED")
        from voice_translator.tts.google_cloud_tts_backend import GoogleCloudTtsBackend
        backend = GoogleCloudTtsBackend(credentials_path=json_path)
        with pytest.raises(RecoverableError):
            backend.synthesize("hi", "en")
