"""認証フロー(Phase E-2)の単体テスト + 各 cloud backend の契約スケルトン。

このファイルは 2 段構成:

**Part 1 — 契約テスト(契約 = どの cloud backend でも満たすべき挙動)**
  汎用 `FakeCloudBackend` を使い、AppController + CredentialsStore + ConfigStore の連携を
  検証する。UI(Tk)は一切起動しない — `verify_and_save_credentials` / `invalidate_verification`
  等の API を直接呼び、副作用(store / verified 状態 / Start gate)を assert する。

**Part 2 — 実 cloud backend のスケルトン**(全テスト @pytest.mark.skip)
  Phase F で実 backend クラスを追加したときに skip を外して使う雛形。それぞれの backend が
  満たすべき挙動の列挙(spec の形 / 401 失敗の扱い / ネットワーク失敗 / クォータ超過 等)を
  そのまま書いてあるので、実装側を引っ張る要件定義としても機能する。

「UI を伴わない」という制約により、CredentialDialog 自体(Tk widget)はここではテストしない。
Dialog の責務(空欄=未編集として既存値で verify する等)も AppController API レベルで
近い挙動を確認する。
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import keyring
import pytest

from tests._fixtures import InMemoryKeyring
from voice_translator.common.app_controller import AppController
from voice_translator.common.backend_registry import BackendRegistry
from voice_translator.common.config_store import ConfigStore
from voice_translator.common.errors import FatalError
from voice_translator.common.types import (
    BackendCapabilities,
    CaptureSource,
    CredentialField,
    LayerKind,
    OutputDevice,
    VerifyResult,
)


# ============================================================
# テスト用 fake backend(汎用 + AWS-like の 2 種類)
# ============================================================
class _SingleKeyCloudBackend:
    """API key 1 つだけの cloud backend(OpenAI / DeepL / Anthropic 的)。

    クラス変数で verify の振る舞いを切り替えられる。
    """

    last_called_with: dict | None = None
    next_result: VerifyResult = VerifyResult(ok=True, message="OK")
    raises_in_verify: BaseException | None = None

    @classmethod
    def credential_spec(cls) -> list[CredentialField]:
        return [CredentialField("api_key", "API Key", secret=True)]

    @classmethod
    def verify_credentials(cls, values: dict[str, str]) -> VerifyResult:
        cls.last_called_with = dict(values)
        if cls.raises_in_verify is not None:
            raise cls.raises_in_verify
        return cls.next_result


class _MultiFieldCloudBackend:
    """AWS-style: access_key + secret_key + region(region は非 secret)。"""

    last_called_with: dict | None = None
    next_result: VerifyResult = VerifyResult(ok=True, message="OK")

    @classmethod
    def credential_spec(cls) -> list[CredentialField]:
        return [
            CredentialField("access_key", "Access Key", secret=True),
            CredentialField("secret_key", "Secret Key", secret=True),
            CredentialField("region", "Region", secret=False),
        ]

    @classmethod
    def verify_credentials(cls, values: dict[str, str]) -> VerifyResult:
        cls.last_called_with = dict(values)
        return cls.next_result


# ============================================================
# 共通 fixture
# ============================================================
def _fake_capture():
    inst = MagicMock(name="capture_inst")
    inst.list_sources = MagicMock(
        return_value=[CaptureSource("mic_a", "Mic A")]
    )
    inst.start = MagicMock(); inst.stop = MagicMock()
    inst.read_chunk = MagicMock(return_value=None)
    inst.get_status = MagicMock()
    sub = MagicMock(); sub.unsubscribe = MagicMock()
    inst.subscribe = MagicMock(return_value=sub)
    return inst


def _fake_output():
    inst = MagicMock(name="output_inst")
    inst.list_devices = MagicMock(return_value=[OutputDevice("hp", "Headphones")])
    inst.start = MagicMock(); inst.stop = MagicMock(); inst.play = MagicMock()
    inst.get_status = MagicMock()
    sub = MagicMock(); sub.unsubscribe = MagicMock()
    inst.subscribe = MagicMock(return_value=sub)
    return inst


def _fake_simple():
    inst = MagicMock(name="simple_backend")
    inst.process = MagicMock(return_value=[])
    inst.transcribe = MagicMock(return_value=("", ""))
    inst.translate = MagicMock(return_value="")
    inst.synthesize = MagicMock(return_value=(b"", 16000))
    inst.reset = MagicMock()
    inst.get_status = MagicMock()
    sub = MagicMock(); sub.unsubscribe = MagicMock()
    inst.subscribe = MagicMock(return_value=sub)
    return inst


@pytest.fixture(autouse=True)
def _isolated_keyring():
    """全テストで InMemoryKeyring に隔離(実 keyring を絶対に触らない)。"""
    keyring.set_keyring(InMemoryKeyring())
    yield


@pytest.fixture()
def _reset_backend_state():
    """fake backend のクラス変数を毎テスト前後でリセット。"""
    _SingleKeyCloudBackend.last_called_with = None
    _SingleKeyCloudBackend.next_result = VerifyResult(ok=True, message="OK")
    _SingleKeyCloudBackend.raises_in_verify = None
    _MultiFieldCloudBackend.last_called_with = None
    _MultiFieldCloudBackend.next_result = VerifyResult(ok=True, message="OK")
    yield


def _make_controller(tmp_path: Path, monkeypatch) -> AppController:
    """全レイヤに mock backend を仕込んだ AppController を返す。"""
    monkeypatch.chdir(tmp_path)
    reg = BackendRegistry()
    reg.register(LayerKind.CAPTURE, "soundcard", _fake_capture)
    reg.register(LayerKind.VAD, "silero", _fake_simple)
    reg.register(LayerKind.ASR, "faster_whisper", _fake_simple)
    reg.register(LayerKind.TRANSLATOR, "nllb200", _fake_simple)
    reg.register(LayerKind.TTS, "sapi", _fake_simple)
    reg.register(LayerKind.OUTPUT, "soundcard", _fake_output)
    cfg = ConfigStore(tmp_path / "cfg.yaml")
    ctrl = AppController(registry=reg, config=cfg)
    return ctrl


def _register_cloud_asr(
    ctrl: AppController,
    *,
    backend_cls=_SingleKeyCloudBackend,
    name: str = "fake_cloud_asr",
    service_name: str = "Fake Cloud ASR",
) -> None:
    """cloud backend を ASR レイヤに登録 + 選択する。"""
    ctrl._registry.register(
        LayerKind.ASR, name,
        lambda: _fake_simple(),
        backend_cls=backend_cls,
        capabilities=BackendCapabilities(
            is_cloud=True, requires_credentials=True, service_name=service_name,
        ),
    )
    ctrl.set_setting("backends", "asr", name)


def _setup_runnable_pipeline_devices(ctrl: AppController, tmp_path: Path) -> None:
    """start_pipeline が device validation で落ちないように devices/log を仕込む。"""
    ctrl.set_setting("devices", "input", "mic_a")
    ctrl.set_setting("devices", "output", "hp")
    ctrl.set_setting("log", "directory", str(tmp_path / "logs"))


# ============================================================
# Part 1 — 契約テスト
# ============================================================
class TestCredentialSpec:
    """spec 周りの基本契約。"""

    def test_spec_returned_for_registered_cloud_backend(
        self, tmp_path, monkeypatch, _reset_backend_state
    ):
        ctrl = _make_controller(tmp_path, monkeypatch)
        _register_cloud_asr(ctrl)
        spec = ctrl.get_credential_spec(LayerKind.ASR, "fake_cloud_asr")
        assert [f.key_name for f in spec] == ["api_key"]

    def test_spec_supports_multiple_fields(
        self, tmp_path, monkeypatch, _reset_backend_state
    ):
        """AWS-like backend は 3 フィールド(うち region は非 secret)。"""
        ctrl = _make_controller(tmp_path, monkeypatch)
        _register_cloud_asr(
            ctrl, backend_cls=_MultiFieldCloudBackend,
            name="fake_aws_asr", service_name="Fake AWS",
        )
        spec = ctrl.get_credential_spec(LayerKind.ASR, "fake_aws_asr")
        assert [f.key_name for f in spec] == ["access_key", "secret_key", "region"]
        assert [f.secret for f in spec] == [True, True, False]

    def test_spec_for_local_backend_is_empty(self, tmp_path, monkeypatch):
        """capability hint も backend_cls も無いローカル backend は空。"""
        ctrl = _make_controller(tmp_path, monkeypatch)
        # faster_whisper は登録時に backend_cls を渡していない
        assert ctrl.get_credential_spec(LayerKind.ASR, "faster_whisper") == []


class TestVerifyAndSave:
    """認証情報の入力 → verify → 保存 の正常/異常パス。"""

    def test_success_saves_keys_and_sets_verified(
        self, tmp_path, monkeypatch, _reset_backend_state
    ):
        ctrl = _make_controller(tmp_path, monkeypatch)
        _register_cloud_asr(ctrl)
        result = ctrl.verify_and_save_credentials(
            LayerKind.ASR, "fake_cloud_asr", {"api_key": "sk-good"}
        )
        assert result.ok is True
        assert ctrl.get_credential("fake_cloud_asr", "api_key") == "sk-good"
        assert ctrl.is_backend_verified("fake_cloud_asr") is True

    def test_failure_does_not_save_or_verify(
        self, tmp_path, monkeypatch, _reset_backend_state
    ):
        ctrl = _make_controller(tmp_path, monkeypatch)
        _register_cloud_asr(ctrl)
        _SingleKeyCloudBackend.next_result = VerifyResult(
            ok=False, message="401 Unauthorized"
        )
        result = ctrl.verify_and_save_credentials(
            LayerKind.ASR, "fake_cloud_asr", {"api_key": "bad"}
        )
        assert result.ok is False
        assert result.message == "401 Unauthorized"
        assert ctrl.get_credential("fake_cloud_asr", "api_key") is None
        assert ctrl.is_backend_verified("fake_cloud_asr") is False

    def test_exception_in_verify_is_caught(
        self, tmp_path, monkeypatch, _reset_backend_state
    ):
        """backend.verify_credentials が例外を投げても、VerifyResult(ok=False) で返る。

        ユーザが何度かリトライできるよう、ダイアログが落ちない契約。
        """
        ctrl = _make_controller(tmp_path, monkeypatch)
        _register_cloud_asr(ctrl)
        _SingleKeyCloudBackend.raises_in_verify = RuntimeError("DNS failure")
        result = ctrl.verify_and_save_credentials(
            LayerKind.ASR, "fake_cloud_asr", {"api_key": "x"}
        )
        assert result.ok is False
        assert "DNS failure" in result.message
        assert ctrl.is_backend_verified("fake_cloud_asr") is False

    def test_multifield_saves_all_keys(
        self, tmp_path, monkeypatch, _reset_backend_state
    ):
        ctrl = _make_controller(tmp_path, monkeypatch)
        _register_cloud_asr(
            ctrl, backend_cls=_MultiFieldCloudBackend,
            name="fake_aws_asr", service_name="Fake AWS",
        )
        values = {"access_key": "AKIA...", "secret_key": "secret", "region": "us-east-1"}
        result = ctrl.verify_and_save_credentials(LayerKind.ASR, "fake_aws_asr", values)
        assert result.ok is True
        for k, v in values.items():
            assert ctrl.get_credential("fake_aws_asr", k) == v

    def test_empty_value_skips_store_but_passes_to_verify(
        self, tmp_path, monkeypatch, _reset_backend_state
    ):
        """空欄(=未編集)は保存しない。backend には渡る(既存値での再検証は呼び側で対応)。

        AppController の API 単体では「空欄=保存しない」だけ保証。「既存値で埋め直す」
        ロジックはダイアログ側(CredentialDialog._on_test)の責任で、本テストの対象外。
        """
        ctrl = _make_controller(tmp_path, monkeypatch)
        _register_cloud_asr(ctrl)
        # 既存値を入れてから空欄で verify
        ctrl.set_credential("fake_cloud_asr", "api_key", "preserved")
        ctrl.verify_and_save_credentials(
            LayerKind.ASR, "fake_cloud_asr", {"api_key": ""}
        )
        # 既存値が消えていない
        assert ctrl.get_credential("fake_cloud_asr", "api_key") == "preserved"
        # backend には空文字が渡る(ダイアログ側が事前置換するべき)
        assert _SingleKeyCloudBackend.last_called_with == {"api_key": ""}

    def test_unregistered_backend_class_returns_failure(
        self, tmp_path, monkeypatch
    ):
        """backend_cls 無しの cloud backend は verify できず failure(運用ミス時の保険)。"""
        ctrl = _make_controller(tmp_path, monkeypatch)
        # backend_cls 渡さずに登録
        ctrl._registry.register(
            LayerKind.ASR, "bare_cloud",
            lambda: _fake_simple(),
            capabilities=BackendCapabilities(
                is_cloud=True, requires_credentials=True, service_name="Bare",
            ),
        )
        result = ctrl.verify_and_save_credentials(
            LayerKind.ASR, "bare_cloud", {"api_key": "x"}
        )
        assert result.ok is False
        assert "未登録" in result.message


class TestVerifiedLifecycle:
    """verified フラグのライフサイクル(set / invalidate / 再認証)。"""

    def test_set_credential_resets_verified(
        self, tmp_path, monkeypatch, _reset_backend_state
    ):
        """キー再入力で verified が False に戻る(誤って古い verified を引きずらない)。"""
        ctrl = _make_controller(tmp_path, monkeypatch)
        _register_cloud_asr(ctrl)
        ctrl.verify_and_save_credentials(
            LayerKind.ASR, "fake_cloud_asr", {"api_key": "v1"}
        )
        assert ctrl.is_backend_verified("fake_cloud_asr") is True
        ctrl.set_credential("fake_cloud_asr", "api_key", "v2")
        assert ctrl.is_backend_verified("fake_cloud_asr") is False

    def test_invalidate_explicit(
        self, tmp_path, monkeypatch, _reset_backend_state
    ):
        ctrl = _make_controller(tmp_path, monkeypatch)
        _register_cloud_asr(ctrl)
        ctrl.verify_and_save_credentials(
            LayerKind.ASR, "fake_cloud_asr", {"api_key": "v1"}
        )
        ctrl.invalidate_verification("fake_cloud_asr")
        assert ctrl.is_backend_verified("fake_cloud_asr") is False

    def test_reverify_restores_verified(
        self, tmp_path, monkeypatch, _reset_backend_state
    ):
        """invalidate 後に正しいキーで再認証すれば verified が戻る(リカバリ動線)。"""
        ctrl = _make_controller(tmp_path, monkeypatch)
        _register_cloud_asr(ctrl)
        ctrl.verify_and_save_credentials(
            LayerKind.ASR, "fake_cloud_asr", {"api_key": "v1"}
        )
        ctrl.invalidate_verification("fake_cloud_asr")
        ctrl.verify_and_save_credentials(
            LayerKind.ASR, "fake_cloud_asr", {"api_key": "v1"}
        )
        assert ctrl.is_backend_verified("fake_cloud_asr") is True

    def test_verified_state_persists_to_config(
        self, tmp_path, monkeypatch, _reset_backend_state
    ):
        """ConfigStore に永続化される(B 案の前提 — 再起動でも保つ)。"""
        ctrl = _make_controller(tmp_path, monkeypatch)
        _register_cloud_asr(ctrl)
        ctrl.verify_and_save_credentials(
            LayerKind.ASR, "fake_cloud_asr", {"api_key": "v1"}
        )
        # ConfigStore を直接見て永続化を確認
        assert ctrl._config.get(
            "credentials", "verified", "fake_cloud_asr"
        ) is True

    def test_independent_per_backend(
        self, tmp_path, monkeypatch, _reset_backend_state
    ):
        """別の cloud backend は独立した verified 状態を持つ。"""
        ctrl = _make_controller(tmp_path, monkeypatch)
        _register_cloud_asr(ctrl, name="cloud_asr_a", service_name="A")
        _register_cloud_asr(ctrl, name="cloud_asr_b", service_name="B")
        ctrl.verify_and_save_credentials(
            LayerKind.ASR, "cloud_asr_a", {"api_key": "k"}
        )
        assert ctrl.is_backend_verified("cloud_asr_a") is True
        assert ctrl.is_backend_verified("cloud_asr_b") is False


class TestStartGate:
    """動作開始ボタン直前の gate(認証完了が前提条件)。"""

    def test_blocks_when_keys_missing(
        self, tmp_path, monkeypatch, _reset_backend_state
    ):
        ctrl = _make_controller(tmp_path, monkeypatch)
        _register_cloud_asr(ctrl)
        _setup_runnable_pipeline_devices(ctrl, tmp_path)
        with pytest.raises(FatalError, match="認証情報未入力"):
            ctrl.start_pipeline()

    def test_blocks_when_unverified(
        self, tmp_path, monkeypatch, _reset_backend_state
    ):
        """キーは保存済みだが verify を通していないと gate される。"""
        ctrl = _make_controller(tmp_path, monkeypatch)
        _register_cloud_asr(ctrl)
        _setup_runnable_pipeline_devices(ctrl, tmp_path)
        ctrl.set_credential("fake_cloud_asr", "api_key", "stored-but-unverified")
        with pytest.raises(FatalError, match="未検証"):
            ctrl.start_pipeline()

    def test_passes_after_verification(
        self, tmp_path, monkeypatch, _reset_backend_state
    ):
        ctrl = _make_controller(tmp_path, monkeypatch)
        _register_cloud_asr(ctrl)
        _setup_runnable_pipeline_devices(ctrl, tmp_path)
        ctrl.verify_and_save_credentials(
            LayerKind.ASR, "fake_cloud_asr", {"api_key": "good"}
        )
        ctrl.start_pipeline()
        try:
            assert ctrl.is_running
        finally:
            ctrl.stop_pipeline()

    def test_local_backends_unaffected(self, tmp_path, monkeypatch):
        """ローカル backend だけのときは gate に引っかからない(従来通り動く)。"""
        ctrl = _make_controller(tmp_path, monkeypatch)
        _setup_runnable_pipeline_devices(ctrl, tmp_path)
        ctrl.start_pipeline()
        try:
            assert ctrl.is_running
        finally:
            ctrl.stop_pipeline()

    def test_mixed_local_and_cloud_gate_only_cloud(
        self, tmp_path, monkeypatch, _reset_backend_state
    ):
        """別レイヤ(Translator)は local、ASR だけ cloud で未認証 → ASR のみ gate される。"""
        ctrl = _make_controller(tmp_path, monkeypatch)
        _register_cloud_asr(ctrl)  # ASR を cloud に
        _setup_runnable_pipeline_devices(ctrl, tmp_path)
        # NLLB(Translator)は local のままなので、ASR の認証だけが gate 要因
        with pytest.raises(FatalError, match=r"\basr\b"):
            ctrl.start_pipeline()


class TestSubscriptionExpirySimulation:
    """サブスク切れ / API 失効を模した、動作中 → 停止 → 再認証要求のシナリオ。"""

    def test_invalidate_during_operation_blocks_next_start(
        self, tmp_path, monkeypatch, _reset_backend_state
    ):
        """初回 OK → 動作中 → サブスク切れ観測 → invalidate → 次の Start は gate される。"""
        ctrl = _make_controller(tmp_path, monkeypatch)
        _register_cloud_asr(ctrl)
        _setup_runnable_pipeline_devices(ctrl, tmp_path)

        # 1) 初回認証 OK
        ctrl.verify_and_save_credentials(
            LayerKind.ASR, "fake_cloud_asr", {"api_key": "good"}
        )
        # 2) 動作開始 → 成功
        ctrl.start_pipeline()
        try:
            assert ctrl.is_running
        finally:
            ctrl.stop_pipeline()
        # 3) 動作中 / 停止時に backend が 401 等を観測したと仮定 → invalidate
        ctrl.invalidate_verification("fake_cloud_asr")
        # 4) 次の Start は再認証要求で gate
        with pytest.raises(FatalError, match="未検証"):
            ctrl.start_pipeline()

    def test_recovery_after_subscription_renewal(
        self, tmp_path, monkeypatch, _reset_backend_state
    ):
        """サブスク再開後にユーザが「テスト」を通せば、また Start が押せる。"""
        ctrl = _make_controller(tmp_path, monkeypatch)
        _register_cloud_asr(ctrl)
        _setup_runnable_pipeline_devices(ctrl, tmp_path)
        ctrl.verify_and_save_credentials(
            LayerKind.ASR, "fake_cloud_asr", {"api_key": "k"}
        )
        ctrl.invalidate_verification("fake_cloud_asr")
        # サブスク復活した想定で再認証
        ctrl.verify_and_save_credentials(
            LayerKind.ASR, "fake_cloud_asr", {"api_key": "k"}
        )
        ctrl.start_pipeline()
        try:
            assert ctrl.is_running
        finally:
            ctrl.stop_pipeline()


class TestBackendSwitch:
    """選択中 cloud backend が変わったときの挙動。"""

    def test_switch_to_different_cloud_requires_its_own_verify(
        self, tmp_path, monkeypatch, _reset_backend_state
    ):
        """A を認証済みでも、B に切り替えたら B の認証が要る。"""
        ctrl = _make_controller(tmp_path, monkeypatch)
        _register_cloud_asr(ctrl, name="cloud_asr_a", service_name="A")
        _register_cloud_asr(ctrl, name="cloud_asr_b", service_name="B")
        _setup_runnable_pipeline_devices(ctrl, tmp_path)

        ctrl.set_setting("backends", "asr", "cloud_asr_a")
        ctrl.verify_and_save_credentials(
            LayerKind.ASR, "cloud_asr_a", {"api_key": "ka"}
        )
        # B に切替
        ctrl.set_setting("backends", "asr", "cloud_asr_b")
        with pytest.raises(FatalError, match="認証情報未入力"):
            ctrl.start_pipeline()


# ============================================================
# Part 2 — 実 cloud backend のスケルトン(Phase F で有効化)
#
# 各クラスは「その backend が満たすべき認証契約」を列挙したテストの雛形。
# Phase F で実 backend クラスを書いたら、skip を外して実装に対して検証する。
# 実 API key は環境変数 / pytest 引数 / モック HTTP のいずれかで注入する。
# ============================================================
class TestOpenAIWhisperApiCredentials:
    """OpenAI Whisper API backend の認証契約。

    Phase E-2 の認証フロー(spec → verify → CredentialsStore → MISSING_CREDENTIALS
    ゲート → invalidate)を契約として表明する。実 API は呼ばず httpx をモック。
    """

    def _backend_cls(self):
        from voice_translator.asr.openai_whisper_api_backend import (
            OpenAiWhisperApiAsrBackend,
        )
        return OpenAiWhisperApiAsrBackend

    def test_credential_spec_has_api_key(self) -> None:
        spec = self._backend_cls().credential_spec()
        assert any(f.key_name == "api_key" and f.secret for f in spec)

    def test_verify_with_valid_key_returns_ok(self, monkeypatch) -> None:
        import sys
        fake = MagicMock()
        resp = MagicMock(status_code=200)
        fake.get = MagicMock(return_value=resp)
        monkeypatch.setitem(sys.modules, "httpx", fake)

        result = self._backend_cls().verify_credentials({"api_key": "sk-valid"})
        assert result.ok is True

    def test_verify_with_invalid_key_returns_failure_401(self, monkeypatch) -> None:
        import sys
        fake = MagicMock()
        fake.get = MagicMock(return_value=MagicMock(status_code=401))
        monkeypatch.setitem(sys.modules, "httpx", fake)

        result = self._backend_cls().verify_credentials({"api_key": "sk-bad"})
        assert result.ok is False

    def test_verify_with_network_error_returns_failure_no_exception(self, monkeypatch) -> None:
        import sys
        fake = MagicMock()
        fake.get = MagicMock(side_effect=RuntimeError("connection refused"))
        monkeypatch.setitem(sys.modules, "httpx", fake)

        # 例外は呼び出し元に伝播せず VerifyResult で表現される
        result = self._backend_cls().verify_credentials({"api_key": "sk-test"})
        assert result.ok is False
        assert result.message  # 何らかの message が入る

    def test_verify_with_quota_exceeded_returns_failure(self, monkeypatch) -> None:
        import sys
        fake = MagicMock()
        fake.get = MagicMock(return_value=MagicMock(status_code=429))
        monkeypatch.setitem(sys.modules, "httpx", fake)

        result = self._backend_cls().verify_credentials({"api_key": "sk-test"})
        assert result.ok is False

    def test_runtime_401_triggers_invalidate_verification(self, monkeypatch) -> None:
        """transcribe 中の 401 は FatalError(=AppController が invalidate_verification を呼ぶ契約)。

        ここでは backend が FatalError を投げるところまでを検証する
        (invalidate_verification の呼び出し自体は AppController/Coordinator の責務)。
        """
        import sys

        import numpy as np

        from voice_translator.common.errors import FatalError

        fake = MagicMock()
        fake_client = MagicMock()
        fake_client.post = MagicMock(return_value=MagicMock(status_code=401, text="bad key"))
        fake.Client = MagicMock(return_value=fake_client)
        monkeypatch.setitem(sys.modules, "httpx", fake)

        backend = self._backend_cls()(api_key="sk-bad")
        with pytest.raises(FatalError):
            backend.transcribe(np.zeros(16000, dtype=np.float32))


class TestDeepLApiCredentials:
    """DeepL API backend の認証契約。Free / Pro エンドポイント切替の検証も含める。"""

    def _backend_cls(self):
        from voice_translator.translator.deepl_backend import DeepLTranslatorBackend
        return DeepLTranslatorBackend

    def test_credential_spec_has_api_key(self) -> None:
        spec = self._backend_cls().credential_spec()
        assert any(f.key_name == "api_key" and f.secret for f in spec)

    def test_verify_with_valid_key_returns_ok(self, monkeypatch) -> None:
        import sys
        fake = MagicMock()
        fake.get = MagicMock(return_value=MagicMock(status_code=200))
        monkeypatch.setitem(sys.modules, "httpx", fake)
        r = self._backend_cls().verify_credentials({"api_key": "key:fx"})
        assert r.ok is True

    def test_verify_with_invalid_key_returns_failure_403(self, monkeypatch) -> None:
        import sys
        fake = MagicMock()
        fake.get = MagicMock(return_value=MagicMock(status_code=403))
        monkeypatch.setitem(sys.modules, "httpx", fake)
        r = self._backend_cls().verify_credentials({"api_key": "bad"})
        assert r.ok is False

    def test_verify_with_quota_exceeded_456_returns_failure(self, monkeypatch) -> None:
        import sys
        fake = MagicMock()
        fake.get = MagicMock(return_value=MagicMock(status_code=456))
        monkeypatch.setitem(sys.modules, "httpx", fake)
        r = self._backend_cls().verify_credentials({"api_key": "key:fx"})
        assert r.ok is False

    def test_runtime_456_triggers_invalidate_verification(self, monkeypatch) -> None:
        """transcribe 中の 456 は FatalError(=AppController が invalidate を呼ぶ契約)。"""
        import sys
        from voice_translator.common.errors import FatalError
        fake = MagicMock()
        client = MagicMock()
        client.post = MagicMock(return_value=MagicMock(status_code=456, text="quota"))
        fake.Client = MagicMock(return_value=client)
        monkeypatch.setitem(sys.modules, "httpx", fake)
        backend = self._backend_cls()(api_key="key:fx")
        with pytest.raises(FatalError):
            backend.translate("hi", "en", "ja")


@pytest.mark.skip(reason="Phase F: OpenAI TTS API backend 実装後に有効化")
class TestOpenAITtsApiCredentials:
    """OpenAI TTS API backend の認証契約(Whisper と同じ API key を使う想定)。"""

    def test_credential_spec_has_api_key(self) -> None: ...
    def test_verify_with_valid_key_returns_ok(self) -> None: ...
    def test_verify_with_invalid_key_returns_failure(self) -> None: ...
    def test_runtime_401_triggers_invalidate_verification(self) -> None: ...


class TestAnthropicClaudeApiCredentials:
    """Anthropic Claude API backend(翻訳用途)の認証契約。"""

    def _backend_cls(self):
        from voice_translator.translator.anthropic_claude_backend import (
            AnthropicClaudeTranslatorBackend,
        )
        return AnthropicClaudeTranslatorBackend

    def test_credential_spec_has_api_key(self) -> None:
        spec = self._backend_cls().credential_spec()
        assert any(f.key_name == "api_key" and f.secret for f in spec)

    def test_verify_with_valid_key_returns_ok(self, monkeypatch) -> None:
        import sys
        fake = MagicMock()
        fake.post = MagicMock(return_value=MagicMock(status_code=200))
        monkeypatch.setitem(sys.modules, "httpx", fake)
        r = self._backend_cls().verify_credentials({"api_key": "sk-ant-x"})
        assert r.ok is True

    def test_verify_with_invalid_key_returns_failure(self, monkeypatch) -> None:
        import sys
        fake = MagicMock()
        fake.post = MagicMock(return_value=MagicMock(status_code=401))
        monkeypatch.setitem(sys.modules, "httpx", fake)
        r = self._backend_cls().verify_credentials({"api_key": "bad"})
        assert r.ok is False

    def test_runtime_401_triggers_invalidate_verification(self, monkeypatch) -> None:
        import sys
        from voice_translator.common.errors import FatalError
        fake = MagicMock()
        client = MagicMock()
        client.post = MagicMock(return_value=MagicMock(status_code=401, text="bad"))
        fake.Client = MagicMock(return_value=client)
        monkeypatch.setitem(sys.modules, "httpx", fake)
        backend = self._backend_cls()(api_key="bad")
        with pytest.raises(FatalError):
            backend.translate("hi", "en", "ja")


class TestOpenAIGptTranslatorCredentials:
    """OpenAI GPT(翻訳用途)backend の認証契約。"""

    def _backend_cls(self):
        from voice_translator.translator.openai_gpt_backend import (
            OpenAiGptTranslatorBackend,
        )
        return OpenAiGptTranslatorBackend

    def test_credential_spec_has_api_key(self) -> None:
        spec = self._backend_cls().credential_spec()
        assert any(f.key_name == "api_key" and f.secret for f in spec)

    def test_verify_with_valid_key_returns_ok(self, monkeypatch) -> None:
        import sys
        fake = MagicMock()
        fake.get = MagicMock(return_value=MagicMock(status_code=200))
        monkeypatch.setitem(sys.modules, "httpx", fake)
        r = self._backend_cls().verify_credentials({"api_key": "sk-x"})
        assert r.ok is True

    def test_verify_with_invalid_key_returns_failure(self, monkeypatch) -> None:
        import sys
        fake = MagicMock()
        fake.get = MagicMock(return_value=MagicMock(status_code=401))
        monkeypatch.setitem(sys.modules, "httpx", fake)
        r = self._backend_cls().verify_credentials({"api_key": "bad"})
        assert r.ok is False

    def test_runtime_401_triggers_invalidate_verification(self, monkeypatch) -> None:
        import sys
        from voice_translator.common.errors import FatalError
        fake = MagicMock()
        client = MagicMock()
        client.post = MagicMock(return_value=MagicMock(status_code=401, text="bad"))
        fake.Client = MagicMock(return_value=client)
        monkeypatch.setitem(sys.modules, "httpx", fake)
        backend = self._backend_cls()(api_key="bad")
        with pytest.raises(FatalError):
            backend.translate("hi", "en", "ja")


@pytest.mark.skip(reason="Phase F: AWS Transcribe backend 実装後に有効化")
class TestAwsTranscribeCredentials:
    """AWS Transcribe backend の認証契約(複数フィールド + region)。"""

    def test_credential_spec_has_access_secret_region(self) -> None: ...
    def test_verify_with_valid_credentials_returns_ok(self) -> None: ...
    def test_verify_with_invalid_credentials_returns_failure(self) -> None: ...
    def test_verify_with_unknown_region_returns_failure(self) -> None: ...
    def test_runtime_credential_error_triggers_invalidate_verification(self) -> None: ...


class TestGoogleCloudSttCredentials:
    """Google Cloud STT backend の認証契約(サービスアカウント JSON ファイル方式)。

    feature/asr-picks で `CredentialField.field_type="file"` を追加し、JSON ファイル
    パスを CredentialsStore に保存する形で対応した。`verify_credentials` は
    `google.oauth2.service_account.Credentials.from_service_account_file` の
    成否で判定する。
    """

    def _backend_cls(self):
        from voice_translator.asr.google_stt_backend import GoogleSttAsrBackend
        return GoogleSttAsrBackend

    def _setup_google_modules(self, monkeypatch, tmp_path, *, file_loader_side_effect=None):
        """google.cloud.speech と google.oauth2.service_account をモック差し替え、
        ダミー JSON ファイルパスを返す。"""
        import sys

        json_path = tmp_path / "fake_sa.json"
        json_path.write_text('{"type": "service_account"}', encoding="utf-8")

        fake_oauth_module = MagicMock()
        fake_oauth_sa = MagicMock()
        cred_class = MagicMock()
        if file_loader_side_effect is not None:
            cred_class.from_service_account_file = MagicMock(side_effect=file_loader_side_effect)
        else:
            cred_class.from_service_account_file = MagicMock(return_value=MagicMock(name="creds"))
        fake_oauth_sa.Credentials = cred_class
        fake_oauth_module.service_account = fake_oauth_sa

        fake_speech = MagicMock()
        fake_speech.SpeechClient = MagicMock(return_value=MagicMock(name="speech_client"))

        fake_google_cloud = MagicMock()
        fake_google_cloud.speech = fake_speech

        monkeypatch.setitem(sys.modules, "google", MagicMock())
        monkeypatch.setitem(sys.modules, "google.cloud", fake_google_cloud)
        monkeypatch.setitem(sys.modules, "google.cloud.speech", fake_speech)
        monkeypatch.setitem(sys.modules, "google.oauth2", fake_oauth_module)
        monkeypatch.setitem(sys.modules, "google.oauth2.service_account", fake_oauth_sa)
        return str(json_path)

    def test_credential_spec_requires_service_account_json(self) -> None:
        spec = self._backend_cls().credential_spec()
        assert any(
            f.key_name == "credentials_path" and f.field_type == "file"
            for f in spec
        )

    def test_verify_with_valid_json_returns_ok(self, monkeypatch, tmp_path) -> None:
        json_path = self._setup_google_modules(monkeypatch, tmp_path)
        result = self._backend_cls().verify_credentials({"credentials_path": json_path})
        assert result.ok is True

    def test_verify_with_invalid_json_returns_failure(self, monkeypatch, tmp_path) -> None:
        json_path = self._setup_google_modules(
            monkeypatch, tmp_path,
            file_loader_side_effect=ValueError("missing field 'private_key'"),
        )
        result = self._backend_cls().verify_credentials({"credentials_path": json_path})
        assert result.ok is False

    def test_verify_with_expired_credentials_returns_failure(self, monkeypatch, tmp_path) -> None:
        """期限切れ/失効した鍵 → クライアント初期化は通っても認証エラーで弾かれる想定。
        本ブランチではモック上で「初期化失敗」として一般化(具体的な期限切れ判定は
        実 API でしか起きないため契約レベルではここまで)。"""
        json_path = self._setup_google_modules(
            monkeypatch, tmp_path,
            file_loader_side_effect=RuntimeError("token expired"),
        )
        result = self._backend_cls().verify_credentials({"credentials_path": json_path})
        assert result.ok is False
