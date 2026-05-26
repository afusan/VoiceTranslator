"""AppController の単体テスト。

実バックエンドを使わず、BackendRegistry にモッククラスを登録して検証する。
"""

from __future__ import annotations

from pathlib import Path
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
from voice_translator.common.utterance import Utterance


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
    inst.transcribe = MagicMock(side_effect=lambda u, lang: u)
    inst.translate = MagicMock(side_effect=lambda u, lang: u)
    inst.synthesize = MagicMock(side_effect=lambda u: u)
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
    def test_initial_status_for_known_backends(
        self, populated_registry, config, monkeypatch
    ) -> None:
        # cache_check 系を全部 LOADED に固定
        from voice_translator.common import cache_check
        monkeypatch.setattr(cache_check, "check_faster_whisper", lambda *a, **k: cache_check.ModelStatus.LOADED)
        monkeypatch.setattr(cache_check, "check_nllb200", lambda *a, **k: cache_check.ModelStatus.LOADED)

        ctrl = AppController(registry=populated_registry, config=config)
        from voice_translator.common.types import LayerKind, ModelStatus

        for layer in LayerKind:
            assert ctrl.get_model_status(layer) == ModelStatus.LOADED

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
        # AppController を直接叩いて jsonl 出力 + コールバック呼び出しを検証
        ctrl = AppController(registry=populated_registry, config=config)
        ctrl.set_setting("log", "directory", str(tmp_path / "logs"))

        seen: list[Utterance] = []
        ctrl.set_callbacks(on_utterance_done=lambda u: seen.append(u))

        # start_pipeline せず _translation_logger を強制初期化したいので、
        # 直接プライベートを差し替え(テスト都合)
        from voice_translator.common.logger import TranslationLogger

        ctrl._translation_logger = TranslationLogger(
            tmp_path / "logs" / "translations.jsonl", enabled=True
        )

        u = Utterance(src_text="hi", tgt_text="やあ", src_lang="en", tgt_lang="ja")
        u.timeline.mark("t_capture")
        u.timeline.mark("t_playback")
        ctrl._handle_utterance_done(u)

        assert seen == [u]
        assert (tmp_path / "logs" / "translations.jsonl").exists()


class TestTextLoggerIntegration:
    """AppController と TextLogger の連携を検証。"""

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

    def test_handle_utterance_done_writes_text_files(
        self, populated_registry, config, tmp_path
    ) -> None:
        from voice_translator.common.logger import TextLogger, TranslationLogger

        ctrl = AppController(registry=populated_registry, config=config)
        ctrl.set_setting("log", "directory", str(tmp_path / "logs"))

        # ロガーを直接差し込んで _handle_utterance_done を叩く
        ctrl._translation_logger = TranslationLogger(
            tmp_path / "logs" / "translations.jsonl", enabled=False
        )
        ctrl._text_logger = TextLogger(
            src_path=tmp_path / "logs" / "soundsrc.txt",
            tgt_path=tmp_path / "logs" / "translated.txt",
            src_enabled=True,
            tgt_enabled=True,
        )

        u = Utterance(src_text="hi", tgt_text="やあ", src_lang="en", tgt_lang="ja")
        ctrl._handle_utterance_done(u)

        assert (tmp_path / "logs" / "soundsrc.txt").exists()
        assert (tmp_path / "logs" / "translated.txt").exists()

    def test_text_logger_failure_does_not_block_callback(
        self, populated_registry, config, tmp_path
    ) -> None:
        """TextLogger.write が例外を出しても UI コールバックは呼ばれる。"""
        from unittest.mock import MagicMock

        ctrl = AppController(registry=populated_registry, config=config)
        seen: list[Utterance] = []
        ctrl.set_callbacks(on_utterance_done=lambda u: seen.append(u))

        ctrl._text_logger = MagicMock()
        ctrl._text_logger.write = MagicMock(side_effect=OSError("disk"))

        u = Utterance(src_text="hi", tgt_text="やあ")
        ctrl._handle_utterance_done(u)
        assert seen == [u]


class TestConfigDefaults:
    def test_text_log_defaults_off(self, populated_registry, config) -> None:
        ctrl = AppController(registry=populated_registry, config=config)
        assert ctrl.get_setting("log", "src_text_enabled") is False
        assert ctrl.get_setting("log", "tgt_text_enabled") is False
