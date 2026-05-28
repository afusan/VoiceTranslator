"""AppController の単体テスト。

実バックエンドを使わず、BackendRegistry にモッククラスを登録して検証する。
R-3 で on_utterance_done(dict) / on_dropped(seq_ids, stage) の新シグネチャに更新。
"""

from __future__ import annotations

from pathlib import Path
from time import monotonic
from unittest.mock import MagicMock

import pytest

from voice_translator.common.app_controller import AppController
from voice_translator.common.backend_registry import BackendRegistry
from voice_translator.common.config_store import ConfigStore
from voice_translator.common.errors import FatalError
from voice_translator.common.types import (
    CaptureSource,
    LayerKind,
    OutputDevice,
)


# ============================================================
# 共通: モックバックエンドファクトリ群
# ============================================================
def _fake_capture_factory():
    inst = MagicMock(name="capture_inst")
    inst.list_sources = MagicMock(
        return_value=[CaptureSource("mic_a", "Mic A"), CaptureSource("spk_lb", "Speakers", is_loopback=True)]
    )
    inst.start = MagicMock()
    inst.stop = MagicMock()
    inst.read_chunk = MagicMock(return_value=None)
    return inst


def _fake_output_factory():
    inst = MagicMock(name="output_inst")
    inst.list_devices = MagicMock(
        return_value=[OutputDevice("hp", "Headphones"), OutputDevice("spk", "Speakers")]
    )
    inst.start = MagicMock()
    inst.stop = MagicMock()
    inst.play = MagicMock()
    return inst


def _fake_simple_backend():
    inst = MagicMock(name="simple_backend")
    inst.reset = MagicMock()
    inst.process = MagicMock(return_value=[])
    # 新 I/F: 戻り値はプリミティブ
    inst.transcribe = MagicMock(return_value=("hello", "en"))
    inst.translate = MagicMock(return_value="こんにちは")
    inst.synthesize = MagicMock(return_value=(b"audio", 16000))
    return inst


@pytest.fixture()
def populated_registry() -> BackendRegistry:
    reg = BackendRegistry()
    reg.register(LayerKind.CAPTURE, "soundcard", _fake_capture_factory)
    reg.register(LayerKind.VAD, "silero", _fake_simple_backend)
    reg.register(LayerKind.ASR, "faster_whisper", _fake_simple_backend)
    reg.register(LayerKind.TRANSLATOR, "nllb200", _fake_simple_backend)
    reg.register(LayerKind.TTS, "sapi", _fake_simple_backend)
    reg.register(LayerKind.OUTPUT, "soundcard", _fake_output_factory)
    return reg


@pytest.fixture()
def config(tmp_path: Path) -> ConfigStore:
    return ConfigStore(tmp_path / "cfg.yaml")


def _sample_record(seq_id: int = 1) -> dict:
    """テスト用 ledger record(handle_utterance_done に渡す)。"""
    t0 = monotonic()
    return {
        "seq_id": seq_id,
        "src_text": "hi",
        "src_lang": "en",
        "tgt_text": "やあ",
        "tgt_lang": "ja",
        "timeline": {"t_capture": t0, "t_playback": t0 + 0.1},
    }


# ============================================================
class TestListing:
    def test_list_capture_sources(self, populated_registry, config) -> None:
        ctrl = AppController(registry=populated_registry, config=config)
        sources = ctrl.list_capture_sources()
        assert [s.source_id for s in sources] == ["mic_a", "spk_lb"]

    def test_list_output_devices(self, populated_registry, config) -> None:
        ctrl = AppController(registry=populated_registry, config=config)
        devices = ctrl.list_output_devices()
        assert [d.device_id for d in devices] == ["hp", "spk"]

    def test_list_backends_per_layer(self, populated_registry, config) -> None:
        ctrl = AppController(registry=populated_registry, config=config)
        assert ctrl.list_backends(LayerKind.ASR) == ["faster_whisper"]


class TestSettings:
    def test_get_set_roundtrip(self, populated_registry, config) -> None:
        ctrl = AppController(registry=populated_registry, config=config)
        ctrl.set_setting("languages", "src", "en")
        assert ctrl.get_setting("languages", "src") == "en"

    def test_save_and_load(self, populated_registry, config, tmp_path) -> None:
        ctrl = AppController(registry=populated_registry, config=config)
        ctrl.set_setting("languages", "tgt", "fr")
        ctrl.save_settings()
        assert config.path.exists()
        # 再ロードで反映
        new_config = ConfigStore(config.path)
        ctrl2 = AppController(registry=populated_registry, config=new_config)
        ctrl2.load_settings()
        assert ctrl2.get_setting("languages", "tgt") == "fr"


class TestStartPipeline:
    def test_missing_device_raises_fatal(self, populated_registry, config) -> None:
        ctrl = AppController(registry=populated_registry, config=config)
        # 既定では devices.input/output は None
        with pytest.raises(FatalError):
            ctrl.start_pipeline()

    def test_same_input_output_raises_fatal(self, populated_registry, config) -> None:
        ctrl = AppController(registry=populated_registry, config=config)
        ctrl.set_setting("devices", "input", "same")
        ctrl.set_setting("devices", "output", "same")
        with pytest.raises(FatalError, match="同じ"):
            ctrl.start_pipeline()

    def test_start_creates_coordinator_and_is_running(
        self, populated_registry, config, tmp_path
    ) -> None:
        ctrl = AppController(registry=populated_registry, config=config)
        ctrl.set_setting("devices", "input", "mic_a")
        ctrl.set_setting("devices", "output", "hp")
        ctrl.set_setting("log", "directory", str(tmp_path / "logs"))

        ctrl.start_pipeline()
        try:
            assert ctrl.is_running
        finally:
            ctrl.stop_pipeline()

    def test_start_twice_is_noop(self, populated_registry, config, tmp_path) -> None:
        ctrl = AppController(registry=populated_registry, config=config)
        ctrl.set_setting("devices", "input", "mic_a")
        ctrl.set_setting("devices", "output", "hp")
        ctrl.set_setting("log", "directory", str(tmp_path / "logs"))
        ctrl.start_pipeline()
        try:
            ctrl.start_pipeline()  # 例外なしで戻ること(no-op)
            assert ctrl.is_running
        finally:
            ctrl.stop_pipeline()


class TestAsyncStart:
    def test_start_async_invokes_on_started(
        self, populated_registry, config, tmp_path
    ) -> None:
        import threading

        ctrl = AppController(registry=populated_registry, config=config)
        ctrl.set_setting("devices", "input", "mic_a")
        ctrl.set_setting("devices", "output", "hp")
        ctrl.set_setting("log", "directory", str(tmp_path / "logs"))

        started = threading.Event()
        ctrl.start_pipeline_async(on_started=lambda: started.set())
        try:
            assert started.wait(timeout=3.0), "on_started が呼ばれない"
            assert ctrl.is_running
        finally:
            ctrl.stop_pipeline()

    def test_start_async_invalid_device_raises_synchronously(
        self, populated_registry, config
    ) -> None:
        from voice_translator.common.errors import FatalError

        ctrl = AppController(registry=populated_registry, config=config)
        # devices は既定で None → 検証エラー
        with pytest.raises(FatalError):
            ctrl.start_pipeline_async()


class TestModelStatus:
    def test_initial_status_is_init_for_all_layers(
        self, populated_registry, config
    ) -> None:
        """アプリ起動直後はキャッシュ有無に関わらず全レイヤ INIT。

        in-memory のロードはまだ走っていないことを素直に表現する
        (キャッシュ由来の LOADED を出すと "Loaded→Loading→Loaded" の不自然な
        遷移になるため)。
        """
        from voice_translator.common.types import LayerKind, ModelStatus

        ctrl = AppController(registry=populated_registry, config=config)
        for layer in LayerKind:
            assert ctrl.get_model_status(layer) == ModelStatus.INIT

    def test_status_listener_invoked_during_load(
        self, populated_registry, config, tmp_path
    ) -> None:
        import threading
        from voice_translator.common.types import LayerKind, ModelStatus

        ctrl = AppController(registry=populated_registry, config=config)
        ctrl.set_setting("devices", "input", "mic_a")
        ctrl.set_setting("devices", "output", "hp")
        ctrl.set_setting("log", "directory", str(tmp_path / "logs"))

        events: list[tuple[LayerKind, ModelStatus]] = []
        ctrl.set_callbacks(on_status_change=lambda l, s: events.append((l, s)))

        started = threading.Event()
        ctrl.start_pipeline_async(on_started=lambda: started.set())
        try:
            assert started.wait(timeout=3.0)
        finally:
            ctrl.stop_pipeline()

        # 全レイヤが LOADING → LOADED の遷移を踏んだことを確認
        for layer in LayerKind:
            seen = [s for (l, s) in events if l == layer]
            assert ModelStatus.LOADING in seen, f"{layer}: LOADING 未通知"
            assert seen[-1] == ModelStatus.LOADED, f"{layer}: 最終状態が LOADED でない"


class TestCallbacks:
    def test_on_utterance_done_is_invoked_with_jsonl_write(
        self, populated_registry, config, tmp_path
    ) -> None:
        ctrl = AppController(registry=populated_registry, config=config)
        ctrl.set_setting("log", "directory", str(tmp_path / "logs"))

        seen: list[dict] = []
        ctrl.set_callbacks(on_utterance_done=lambda r: seen.append(r))

        # _translation_logger を手動で初期化(start せずに直接 _handle_utterance_done を叩く)
        from voice_translator.common.logger import TranslationLogger
        ctrl._translation_logger = TranslationLogger(
            tmp_path / "logs" / "translations.jsonl", enabled=True
        )

        record = _sample_record(seq_id=42)
        ctrl._handle_utterance_done(record)

        assert seen == [record]
        assert (tmp_path / "logs" / "translations.jsonl").exists()


class TestTextLoggerIntegration:
    """AppController と TextLogger の連携を検証。

    R-3: TextLogger は PipelineCoordinator に渡され、ASR/Translator 段で
    write_src/write_tgt が呼ばれる。AppController._handle_utterance_done からは呼ばれない。
    """

    def test_text_logger_created_after_start_with_settings(
        self, populated_registry, config, tmp_path
    ) -> None:
        import threading
        from voice_translator.common.logger import TextLogger

        ctrl = AppController(registry=populated_registry, config=config)
        ctrl.set_setting("devices", "input", "mic_a")
        ctrl.set_setting("devices", "output", "hp")
        ctrl.set_setting("log", "directory", str(tmp_path / "logs"))
        ctrl.set_setting("log", "src_text_enabled", True)
        ctrl.set_setting("log", "tgt_text_enabled", True)

        started = threading.Event()
        ctrl.start_pipeline_async(on_started=lambda: started.set())
        try:
            assert started.wait(timeout=3.0)
            assert isinstance(ctrl._text_logger, TextLogger)
            assert ctrl._text_logger.src_enabled is True
            assert ctrl._text_logger.tgt_enabled is True
        finally:
            ctrl.stop_pipeline()


class TestConfigDefaults:
    def test_text_log_defaults_off(self, populated_registry, config) -> None:
        ctrl = AppController(registry=populated_registry, config=config)
        assert ctrl.get_setting("log", "src_text_enabled") is False
        assert ctrl.get_setting("log", "tgt_text_enabled") is False

    def test_sapi_rate_default(self, populated_registry, config) -> None:
        ctrl = AppController(registry=populated_registry, config=config)
        assert ctrl.get_setting("backends_config", "sapi", "rate") == 180


class TestLoadModels:
    """ロード/開始/停止 分離 の挙動テスト。"""

    def test_load_models_populates_cache(
        self, populated_registry, config
    ) -> None:
        ctrl = AppController(registry=populated_registry, config=config)
        assert ctrl.is_loaded is False
        ctrl.load_models()
        assert ctrl.is_loaded is True
        # 各レイヤがキャッシュに居る
        for layer in LayerKind:
            assert layer in ctrl._backends

    def test_load_models_is_idempotent(
        self, populated_registry, config
    ) -> None:
        """二度呼んでも余計なインスタンス化が走らない(キャッシュが効く)。"""
        ctrl = AppController(registry=populated_registry, config=config)
        ctrl.load_models()
        before = {layer: ctrl._backends[layer] for layer in LayerKind}
        ctrl.load_models()
        after = {layer: ctrl._backends[layer] for layer in LayerKind}
        # 同一インスタンス(再生成されていない)
        for layer in LayerKind:
            assert before[layer] is after[layer]

    def test_stop_pipeline_keeps_backends(
        self, populated_registry, config, tmp_path
    ) -> None:
        """停止してもバックエンドは常駐し続ける(次回 Start でロード不要)。"""
        ctrl = AppController(registry=populated_registry, config=config)
        ctrl.set_setting("devices", "input", "mic_a")
        ctrl.set_setting("devices", "output", "hp")
        ctrl.set_setting("log", "directory", str(tmp_path / "logs"))

        ctrl.start_pipeline()
        try:
            assert ctrl.is_running
            assert ctrl.is_loaded
        finally:
            ctrl.stop_pipeline()

        # 停止後も is_loaded のまま
        assert ctrl.is_loaded
        # 同一インスタンスが残っている
        before = dict(ctrl._backends)
        ctrl.start_pipeline()
        try:
            after = dict(ctrl._backends)
            for layer in LayerKind:
                assert before[layer] is after[layer], (
                    f"{layer}: stop→start でバックエンドが作り直された"
                )
        finally:
            ctrl.stop_pipeline()

    def test_backend_change_evicts_only_that_layer(
        self, populated_registry, config
    ) -> None:
        """バックエンド名を変えると、当該レイヤだけがキャッシュから外れる。"""
        # ASR にもう1つ実装を追加して切り替えできるようにする
        populated_registry.register(
            LayerKind.ASR, "alt_asr", _fake_simple_backend
        )
        ctrl = AppController(registry=populated_registry, config=config)
        ctrl.load_models()

        kept_layers = {l: ctrl._backends[l] for l in LayerKind if l != LayerKind.ASR}
        old_asr = ctrl._backends[LayerKind.ASR]

        ctrl.set_setting("backends", "asr", "alt_asr")
        # ASR は破棄され、再ロードが起きる(別スレッドだが Mock の生成は瞬時)
        import time
        for _ in range(20):
            if LayerKind.ASR in ctrl._backends:
                break
            time.sleep(0.05)
        assert LayerKind.ASR in ctrl._backends, "ASR が再ロードされていない"
        new_asr = ctrl._backends[LayerKind.ASR]
        assert new_asr is not old_asr, "ASR インスタンスが置き換わっていない"
        # 他レイヤは触られていない
        for layer in LayerKind:
            if layer == LayerKind.ASR:
                continue
            assert ctrl._backends[layer] is kept_layers[layer], (
                f"{layer}: 設定変更で触らなくていいキャッシュが破棄された"
            )

    def test_load_models_async_invokes_on_done(
        self, populated_registry, config
    ) -> None:
        import threading
        ctrl = AppController(registry=populated_registry, config=config)
        done = threading.Event()
        ctrl.load_models_async(on_done=lambda: done.set())
        assert done.wait(timeout=3.0), "on_done が呼ばれない"
        assert ctrl.is_loaded

    def test_start_after_preload_does_not_recreate_backends(
        self, populated_registry, config, tmp_path
    ) -> None:
        """先に load_models しておけば、Start でバックエンドは作り直されない。"""
        ctrl = AppController(registry=populated_registry, config=config)
        ctrl.set_setting("devices", "input", "mic_a")
        ctrl.set_setting("devices", "output", "hp")
        ctrl.set_setting("log", "directory", str(tmp_path / "logs"))

        ctrl.load_models()
        snapshot = dict(ctrl._backends)

        ctrl.start_pipeline()
        try:
            for layer in LayerKind:
                assert ctrl._backends[layer] is snapshot[layer]
        finally:
            ctrl.stop_pipeline()


class TestHandleDropped:
    """AppController._handle_dropped(seq_ids, stage) のシグネチャ確認。

    R-3 で signature 変更: list[int] + str を受ける(以前は list[Utterance])。
    TextLogger には各段で既に書かれているので、AppController 側ではログのみ。
    """

    def test_seq_ids_logged(
        self, populated_registry, config, caplog
    ) -> None:
        import logging
        caplog.set_level(logging.INFO, logger="voice_translator")

        ctrl = AppController(registry=populated_registry, config=config)
        ctrl._handle_dropped([10, 11, 12], "captured_queue(Input→ASR)")

        info_logs = [r for r in caplog.records if "dropped seq_ids" in r.message]
        assert info_logs, "ドロップログが出ていない"

    def test_empty_seq_ids_is_noop(self, populated_registry, config) -> None:
        ctrl = AppController(registry=populated_registry, config=config)
        # 例外なく終わること
        ctrl._handle_dropped([], "captured_queue")
