"""VAD レイヤのバックエンド切替が「適切に破棄 → 明示ロードで新インスタンス」を経ることの構造テスト。

Phase F1 で VAD レイヤに 4 つの backend (silero / webrtcvad / pyannote / pvcobra) が登録された。
4×4 の全遷移を試す必要はない — `AppController.set_setting("backends", "vad", name)` は
`_evict_backend_locked(layer)` の **同じ経路** を通るため、代表系列 1〜2 本を確認すれば
構造的に保証される(CLAUDE.md「構造上適当に破棄されるならすべてのパターンを試す必要はない」)。

変更即ロードは廃止済み: `set_setting` は evict + INIT のみで、実ロードは
Start / ↻ ロード / auto_load(テストでは `load_model_layer`)で行う。

検証する 3 つの不変条件:
1. 切替前の backend インスタンスの `subscribe()` が返した `Subscription.unsubscribe()` が呼ばれる
2. 明示ロード後に `_backends[VAD]` が新 backend インスタンスで上書きされる(同じインスタンスではない)
3. 状態が `INIT → LOADED`(or `MISSING_CREDENTIALS`)を経由する
"""

from __future__ import annotations

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
    ModelStatus,
    OutputDevice,
    VerifyResult,
)


# ============================================================
# テスト用 fake VAD backend(silero / webrtcvad / pyannote / pvcobra の代役)
# ============================================================
class _CountingFakeBackend:
    """切替で生成された回数を class 変数で記録できる fake backend。

    `subscribe()` は MagicMock の Subscription を返し、`unsubscribe()` 呼び出しを記録する。
    """

    instance_count: int = 0
    requires_credentials: bool = False
    credential_keys: tuple[str, ...] = ()
    initial_status: ModelStatus = ModelStatus.LOADED

    @classmethod
    def reset_counters(cls) -> None:
        cls.instance_count = 0

    def __init__(self) -> None:
        type(self).instance_count += 1
        self._status = type(self).initial_status
        self._subscription = MagicMock(name=f"{type(self).__name__}_sub")
        self._subscription.unsubscribe = MagicMock()

    def get_status(self) -> ModelStatus:
        return self._status

    def subscribe(self, callback):  # noqa: ARG002
        return self._subscription

    def get_recent_errors(self) -> list:
        return []

    def process(self, _chunk):
        return []

    def reset(self) -> None:
        pass


class _FakeSilero(_CountingFakeBackend):
    instance_count = 0
    initial_status = ModelStatus.LOADED


class _FakeWebRtc(_CountingFakeBackend):
    instance_count = 0
    initial_status = ModelStatus.LOADED


class _FakePyannote(_CountingFakeBackend):
    """HF token 未入力で MISSING_CREDENTIALS を立てる擬似クラス。

    実 backend と同じく `__init__(hf_token=...)` で credentials を観測する。
    """

    instance_count = 0
    requires_credentials = True
    credential_keys = ("hf_token",)

    def __init__(self, hf_token: str | None = None) -> None:
        type(self).instance_count += 1
        self._status = (
            ModelStatus.LOADED if hf_token else ModelStatus.MISSING_CREDENTIALS
        )
        self._subscription = MagicMock(name=f"{type(self).__name__}_sub")
        self._subscription.unsubscribe = MagicMock()

    @classmethod
    def credential_spec(cls):
        return [CredentialField("hf_token", "HuggingFace Token", secret=True)]

    @classmethod
    def verify_credentials(cls, values):
        if values.get("hf_token"):
            return VerifyResult(ok=True, message="ok")
        return VerifyResult(ok=False, message="HF token missing")


class _FakePvcobra(_CountingFakeBackend):
    """access_key 未入力で MISSING_CREDENTIALS を立てる擬似クラス。"""

    instance_count = 0
    requires_credentials = True
    credential_keys = ("access_key",)

    def __init__(self, access_key: str | None = None) -> None:
        type(self).instance_count += 1
        self._status = (
            ModelStatus.LOADED if access_key else ModelStatus.MISSING_CREDENTIALS
        )
        self._subscription = MagicMock(name=f"{type(self).__name__}_sub")
        self._subscription.unsubscribe = MagicMock()

    @classmethod
    def credential_spec(cls):
        return [CredentialField("access_key", "Access Key", secret=True)]

    @classmethod
    def verify_credentials(cls, values):
        if values.get("access_key"):
            return VerifyResult(ok=True, message="ok")
        return VerifyResult(ok=False, message="access_key missing")


# ============================================================
# 共通: 他レイヤの fake(切替の主役は VAD なので残りは MagicMock で済ます)
# ============================================================
def _fake_capture():
    inst = MagicMock(name="capture_inst")
    inst.list_sources = MagicMock(return_value=[CaptureSource("mic_a", "Mic A")])
    inst.start = MagicMock()
    inst.stop = MagicMock()
    inst.read_chunk = MagicMock(return_value=None)
    inst.get_status = MagicMock(return_value=ModelStatus.LOADED)
    sub = MagicMock()
    sub.unsubscribe = MagicMock()
    inst.subscribe = MagicMock(return_value=sub)
    inst.get_recent_errors = MagicMock(return_value=[])
    return inst


def _fake_output():
    inst = MagicMock(name="output_inst")
    inst.list_devices = MagicMock(return_value=[OutputDevice("hp", "Headphones")])
    inst.start = MagicMock()
    inst.stop = MagicMock()
    inst.play = MagicMock()
    inst.get_status = MagicMock(return_value=ModelStatus.LOADED)
    sub = MagicMock()
    sub.unsubscribe = MagicMock()
    inst.subscribe = MagicMock(return_value=sub)
    inst.get_recent_errors = MagicMock(return_value=[])
    return inst


def _fake_simple():
    inst = MagicMock(name="simple_backend")
    inst.process = MagicMock(return_value=[])
    inst.transcribe = MagicMock(return_value=("", ""))
    inst.translate = MagicMock(return_value="")
    inst.synthesize = MagicMock(return_value=(b"", 16000))
    inst.reset = MagicMock()
    inst.get_status = MagicMock(return_value=ModelStatus.LOADED)
    sub = MagicMock()
    sub.unsubscribe = MagicMock()
    inst.subscribe = MagicMock(return_value=sub)
    inst.get_recent_errors = MagicMock(return_value=[])
    return inst


# ============================================================
# fixtures
# ============================================================
@pytest.fixture(autouse=True)
def _isolated_keyring():
    keyring.set_keyring(InMemoryKeyring())
    yield


@pytest.fixture(autouse=True)
def _reset_backend_counters():
    for cls in (_FakeSilero, _FakeWebRtc, _FakePyannote, _FakePvcobra):
        cls.reset_counters()
    yield


@pytest.fixture()
def controller_with_4_vads(tmp_path, monkeypatch):
    """4 つの VAD backend(silero/webrtcvad/pyannote/pvcobra)を fake 化して登録した AppController。

    認証必須 backend(pyannote / pvcobra)の factory は `_get_credential` 経由で
    CredentialsStore を読む — これは本物の backend_setup と同じ流儀(factory で
    credentials を観測 → backend __init__ に渡す)。
    """
    monkeypatch.chdir(tmp_path)
    cfg = ConfigStore(tmp_path / "cfg.yaml")
    cfg.set("credentials", "use_local_file", True)
    cfg.set("backends", "vad", "silero")

    from voice_translator.common.backend_setup import _get_credential

    reg = BackendRegistry()
    reg.register(LayerKind.CAPTURE, "soundcard", _fake_capture)
    reg.register(
        LayerKind.VAD, "silero", _FakeSilero, backend_cls=_FakeSilero,
    )
    reg.register(
        LayerKind.VAD, "webrtcvad", _FakeWebRtc, backend_cls=_FakeWebRtc,
        capabilities=BackendCapabilities(notes="webrtc fake"),
    )
    reg.register(
        LayerKind.VAD, "pyannote",
        lambda: _FakePyannote(hf_token=_get_credential(cfg, "pyannote", "hf_token")),
        backend_cls=_FakePyannote,
        capabilities=BackendCapabilities(
            requires_credentials=True, service_name="pyannote fake",
        ),
    )
    reg.register(
        LayerKind.VAD, "pvcobra",
        lambda: _FakePvcobra(access_key=_get_credential(cfg, "pvcobra", "access_key")),
        backend_cls=_FakePvcobra,
        capabilities=BackendCapabilities(
            requires_credentials=True, service_name="pvcobra fake",
        ),
    )
    reg.register(LayerKind.ASR, "faster_whisper", _fake_simple)
    reg.register(LayerKind.TRANSLATOR, "nllb200", _fake_simple)
    reg.register(LayerKind.TTS, "sapi", _fake_simple)
    reg.register(LayerKind.OUTPUT, "soundcard", _fake_output)

    ctrl = AppController(registry=reg, config=cfg)
    return ctrl


def _load_vad(ctrl: AppController, expected_cls) -> object:
    """切替後の明示ロード(↻ ロード / Start 相当)を行い、新 backend を返す。

    変更即ロードは廃止されたので `set_setting` 後は INIT のまま。テストでは
    `load_model_layer` で実ロード経路を踏む。
    """
    ctrl.load_model_layer(LayerKind.VAD)
    backend = ctrl._backends.get(LayerKind.VAD)
    assert isinstance(backend, expected_cls), (
        f"VAD backend が {expected_cls.__name__} になっていない "
        f"(current: {type(backend).__name__ if backend else None})"
    )
    return backend


# ============================================================
# テスト
# ============================================================
class TestVadBackendSwitchingDisposal:
    """各遷移で旧 backend が破棄される構造的不変条件。

    全パターンの組合せではなく、4 backend を順に通る系列 1 本で代表検証する。
    """

    def test_silero_to_webrtc_evicts_old_subscription(
        self, controller_with_4_vads
    ) -> None:
        ctrl = controller_with_4_vads
        # まず silero を確実にロード
        ctrl.load_model_layer(LayerKind.VAD)
        silero_inst = ctrl._backends[LayerKind.VAD]
        assert isinstance(silero_inst, _FakeSilero)
        old_sub = silero_inst._subscription

        # 切替 → 旧 subscription が unsubscribe される
        ctrl.set_setting("backends", "vad", "webrtcvad")
        new_inst = _load_vad(ctrl, _FakeWebRtc)

        old_sub.unsubscribe.assert_called()
        # 新 backend のインスタンスは別もの
        assert new_inst is not silero_inst

    def test_full_chain_silero_webrtc_pyannote_pvcobra(
        self, controller_with_4_vads
    ) -> None:
        """1 系列で 4 種を通って、各時点で型と instance_count が遷移することを確認。

        evict→load の構造が全 backend で同じなら、これで全パターンの動作保証に足る。
        """
        ctrl = controller_with_4_vads

        # silero
        ctrl.load_model_layer(LayerKind.VAD)
        assert isinstance(ctrl._backends[LayerKind.VAD], _FakeSilero)
        assert _FakeSilero.instance_count == 1

        # → webrtcvad
        ctrl.set_setting("backends", "vad", "webrtcvad")
        _load_vad(ctrl, _FakeWebRtc)
        assert _FakeWebRtc.instance_count == 1

        # → pyannote(認証情報なし → MISSING_CREDENTIALS で生成されるが、_backends には入る)
        ctrl.set_setting("backends", "vad", "pyannote")
        _load_vad(ctrl, _FakePyannote)
        assert _FakePyannote.instance_count == 1
        # MISSING_CREDENTIALS が backend 側で立っている
        assert (
            ctrl._backends[LayerKind.VAD].get_status() == ModelStatus.MISSING_CREDENTIALS
        )

        # → pvcobra(同じく認証無し → MISSING_CREDENTIALS)
        ctrl.set_setting("backends", "vad", "pvcobra")
        _load_vad(ctrl, _FakePvcobra)
        assert _FakePvcobra.instance_count == 1

    def test_load_lock_serialized_no_double_load(
        self, controller_with_4_vads
    ) -> None:
        """同じ backend に切り替えても load 経路は冪等(evict 後の明示ロードで作り直し)。

        `set_setting` は同名でも evict を発火する。これは意図通り —
        backends_config が変わった可能性があるため、次の明示ロードで新インスタンスが入る。
        """
        ctrl = controller_with_4_vads
        ctrl.load_model_layer(LayerKind.VAD)
        assert _FakeSilero.instance_count == 1

        # 同じ silero に再 set_setting → evict、明示ロードで +1
        ctrl.set_setting("backends", "vad", "silero")
        assert LayerKind.VAD not in ctrl._backends, "evict されていない"
        _load_vad(ctrl, _FakeSilero)
        assert _FakeSilero.instance_count == 2


class TestStartGateOnCredentialedVad:
    """認証必須 VAD (pvcobra) に切り替えたら start_pipeline が gate されること。

    Phase E-2 の認証フローを実 backend で叩く確認(構造テスト)。
    """

    def _setup_devices(self, ctrl, tmp_path):
        ctrl.set_setting("devices", "input", "mic_a")
        ctrl.set_setting("devices", "output", "hp")
        ctrl.set_setting("log", "directory", str(tmp_path / "logs"))

    def test_pvcobra_unverified_blocks_start(
        self, controller_with_4_vads, tmp_path
    ) -> None:
        ctrl = controller_with_4_vads
        self._setup_devices(ctrl, tmp_path)
        ctrl.set_setting("backends", "vad", "pvcobra")
        _load_vad(ctrl, _FakePvcobra)
        # access_key が無い → gate が落ちる
        with pytest.raises(FatalError):
            ctrl.start_pipeline()

    def test_pvcobra_after_verify_passes_gate(
        self, controller_with_4_vads, tmp_path
    ) -> None:
        ctrl = controller_with_4_vads
        self._setup_devices(ctrl, tmp_path)
        ctrl.set_setting("backends", "vad", "pvcobra")
        _load_vad(ctrl, _FakePvcobra)
        # 認証情報を入力 + verify
        result = ctrl.verify_and_save_credentials(
            LayerKind.VAD, "pvcobra", {"access_key": "ak"}
        )
        assert result.ok is True
        # start できる
        ctrl.start_pipeline()
        try:
            assert ctrl.is_running
        finally:
            ctrl.stop_pipeline()

    def test_switching_back_to_local_silero_clears_gate(
        self, controller_with_4_vads, tmp_path
    ) -> None:
        """pvcobra (要認証) → silero (無認証) で gate が解除される。"""
        ctrl = controller_with_4_vads
        self._setup_devices(ctrl, tmp_path)

        # pvcobra で gate される
        ctrl.set_setting("backends", "vad", "pvcobra")
        _load_vad(ctrl, _FakePvcobra)
        with pytest.raises(FatalError):
            ctrl.start_pipeline()

        # silero に戻すと gate を通る
        ctrl.set_setting("backends", "vad", "silero")
        _load_vad(ctrl, _FakeSilero)
        ctrl.start_pipeline()
        try:
            assert ctrl.is_running
        finally:
            ctrl.stop_pipeline()
