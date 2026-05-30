"""共通型 (types.py) の単体テスト。"""

from __future__ import annotations

import numpy as np

from voice_translator.common.types import (
    INTERNAL_CHANNELS,
    INTERNAL_DTYPE,
    INTERNAL_SAMPLE_RATE,
    BackendCapabilities,
    CaptureSource,
    CredentialField,
    ErrorRecord,
    LayerKind,
    ModelInfo,
    ModelStatus,
    OutputDevice,
    VerifyResult,
)


class TestConstants:
    def test_internal_format(self) -> None:
        assert INTERNAL_SAMPLE_RATE == 16_000
        assert INTERNAL_CHANNELS == 1
        assert INTERNAL_DTYPE == np.float32

    def test_layer_kind_values(self) -> None:
        # 想定するレイヤがすべて列挙されていること
        expected = {"capture", "vad", "asr", "translator", "tts", "output"}
        assert {layer.value for layer in LayerKind} == expected


class TestCaptureSource:
    def test_construct_minimal(self) -> None:
        src = CaptureSource(source_id="abc", display_name="Mic")
        assert src.is_loopback is False

    def test_loopback_flag(self) -> None:
        src = CaptureSource(source_id="spk", display_name="Speakers", is_loopback=True)
        assert src.is_loopback is True


class TestOutputDevice:
    def test_construct(self) -> None:
        d = OutputDevice(device_id="hp", display_name="Headphones")
        assert d.device_id == "hp"


class TestBackendCapabilities:
    def test_defaults(self) -> None:
        cap = BackendCapabilities()
        assert cap.supported_languages == ()
        assert cap.requires_gpu is False
        assert cap.notes == ""
        assert cap.extra == {}

    def test_cloud_defaults_false(self) -> None:
        """新フィールドの既定値: クラウド系は False / None。"""
        cap = BackendCapabilities()
        assert cap.is_cloud is False
        assert cap.requires_credentials is False
        assert cap.service_name is None
        assert cap.terms_url is None

    def test_cloud_fields_settable(self) -> None:
        cap = BackendCapabilities(
            is_cloud=True,
            requires_credentials=True,
            service_name="OpenAI Whisper API",
            terms_url="https://example.com/terms",
        )
        assert cap.is_cloud is True
        assert cap.requires_credentials is True
        assert cap.service_name == "OpenAI Whisper API"
        assert cap.terms_url == "https://example.com/terms"


class TestModelStatus:
    def test_new_statuses_exist(self) -> None:
        """Phase A1 で追加した状態が enum 値として存在すること。"""
        assert ModelStatus.MISSING_CREDENTIALS.value == "Missing Credentials"
        assert ModelStatus.DOWNLOADING.value == "Downloading..."

    def test_init_is_default(self) -> None:
        """INIT が初期状態の代表値であること(BackendBase の既定)。"""
        assert ModelStatus.INIT.value == "Init"


class TestModelInfo:
    def test_construct_minimal(self) -> None:
        m = ModelInfo(name="small", display_name="Small Model")
        assert m.name == "small"
        assert m.ram_gb is None
        assert m.vram_gb_if_gpu is None
        assert m.download_size_gb is None
        assert m.target_proc_ms_per_sec_audio is None

    def test_construct_full(self) -> None:
        m = ModelInfo(
            name="medium",
            display_name="Medium",
            ram_gb=3.0,
            vram_gb_if_gpu=2.0,
            download_size_gb=1.5,
            target_proc_ms_per_sec_audio=300.0,
        )
        assert m.ram_gb == 3.0
        assert m.download_size_gb == 1.5


class TestErrorRecord:
    def test_construct(self) -> None:
        r = ErrorRecord(
            timestamp=123.456, message="boom", exc_type="RuntimeError", context="load"
        )
        assert r.message == "boom"
        assert r.context == "load"


class TestCredentialField:
    def test_defaults(self) -> None:
        f = CredentialField(key_name="api_key", label="API Key")
        assert f.secret is True
        assert f.help_text == ""

    def test_plain_field(self) -> None:
        f = CredentialField(
            key_name="region", label="Region", secret=False,
            help_text="AWS リージョン",
        )
        assert f.secret is False
        assert f.help_text == "AWS リージョン"


class TestVerifyResult:
    def test_ok(self) -> None:
        r = VerifyResult(ok=True, message="OK")
        assert r.ok is True
        assert r.message == "OK"

    def test_failure(self) -> None:
        r = VerifyResult(ok=False, message="401 Unauthorized")
        assert r.ok is False
