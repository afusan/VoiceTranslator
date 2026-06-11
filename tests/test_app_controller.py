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
    ModelStatus,
    OutputDevice,
)


def _attach_backend_base_protocol(inst: MagicMock) -> None:
    """Phase A2 で AppController が backend に求める I/F をモックに生やす。

    - `get_status()` は LOADED を返す(load 完了直後の正常状態)
    - `subscribe(callback)` は unsubscribe を持つ MagicMock を返す
    既存テストの mock factory に注入することで「初期化手順の追加はテスト側で吸収」する
    (CLAUDE.md テスト変更時の方針)。
    """
    inst.get_status = MagicMock(return_value=ModelStatus.LOADED)
    sub = MagicMock(name="subscription")
    sub.unsubscribe = MagicMock()
    inst.subscribe = MagicMock(return_value=sub)


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
    _attach_backend_base_protocol(inst)
    return inst


def _fake_output_factory():
    inst = MagicMock(name="output_inst")
    inst.list_devices = MagicMock(
        return_value=[OutputDevice("hp", "Headphones"), OutputDevice("spk", "Speakers")]
    )
    inst.start = MagicMock()
    inst.stop = MagicMock()
    inst.play = MagicMock()
    _attach_backend_base_protocol(inst)
    return inst


def _fake_simple_backend():
    inst = MagicMock(name="simple_backend")
    inst.reset = MagicMock()
    inst.process = MagicMock(return_value=[])
    # 新 I/F: 戻り値はプリミティブ
    inst.transcribe = MagicMock(return_value=("hello", "en"))
    inst.translate = MagicMock(return_value="こんにちは")
    inst.synthesize = MagicMock(return_value=(b"audio", 16000))
    _attach_backend_base_protocol(inst)
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


class TestTestOutputPlayback:
    """`AppController.test_output_playback` のガード条件と再生フロー検証。"""

    def _make_ctrl_with_devices(self, populated_registry, config, tmp_path):
        ctrl = AppController(registry=populated_registry, config=config)
        ctrl.set_setting("backends", "tts", "sapi")
        ctrl.set_setting("backends", "output", "soundcard")
        ctrl.set_setting("devices", "output", "hp")
        ctrl.set_setting("languages", "tgt", "ja")
        return ctrl

    def test_plays_via_tts_and_output(
        self, populated_registry, config, tmp_path,
    ) -> None:
        """正常系: TTS で合成 → Output.start/play/stop が順に呼ばれる。"""
        ctrl = self._make_ctrl_with_devices(populated_registry, config, tmp_path)
        # numpy 互換の `size` を持つ非空配列を返すようにモック調整
        import numpy as np
        ctrl.load_model_layer(LayerKind.TTS)
        ctrl.load_model_layer(LayerKind.OUTPUT)
        tts = ctrl._backends[LayerKind.TTS]
        output = ctrl._backends[LayerKind.OUTPUT]
        pcm = np.array([0.1, 0.2, 0.3], dtype=np.float32)
        tts.synthesize = MagicMock(return_value=(pcm, 22050))

        ctrl.test_output_playback("テスト音声")

        tts.synthesize.assert_called_once_with("テスト音声", "ja")
        output.start.assert_called_once_with("hp")
        # play の引数を ndarray 含めて確認
        assert output.play.call_count == 1
        called_pcm, called_sr = output.play.call_args.args
        assert called_sr == 22050
        # stop は finally で必ず呼ばれる
        output.stop.assert_called_once()

    def test_rejects_when_pipeline_running(
        self, populated_registry, config, tmp_path,
    ) -> None:
        """動作中は RuntimeError(Output backend を本体が掴んでいるため)。"""
        ctrl = self._make_ctrl_with_devices(populated_registry, config, tmp_path)
        ctrl.set_setting("devices", "input", "mic_a")
        ctrl.set_setting("log", "directory", str(tmp_path / "logs"))
        ctrl.start_pipeline()
        try:
            with pytest.raises(RuntimeError, match="動作中"):
                ctrl.test_output_playback()
        finally:
            ctrl.stop_pipeline()

    def test_rejects_in_text_only_mode(
        self, populated_registry, config, tmp_path,
    ) -> None:
        """text_only(TTS=「(なし)」)では合成手段がないので拒否。"""
        ctrl = AppController(registry=populated_registry, config=config)
        ctrl.set_setting("backends", "tts", AppController.TTS_NONE)
        ctrl.set_setting("backends", "output", "soundcard")
        ctrl.set_setting("devices", "output", "hp")
        with pytest.raises(RuntimeError, match="TTS"):
            ctrl.test_output_playback()

    def test_rejects_when_output_device_empty(
        self, populated_registry, config, tmp_path,
    ) -> None:
        """`devices.output` が未設定なら再生先が決まらないので拒否。"""
        ctrl = AppController(registry=populated_registry, config=config)
        ctrl.set_setting("backends", "tts", "sapi")
        ctrl.set_setting("backends", "output", "soundcard")
        # devices.output は未設定
        with pytest.raises(RuntimeError, match="出力デバイス"):
            ctrl.test_output_playback()

    def test_rejects_when_tts_returns_empty_pcm(
        self, populated_registry, config, tmp_path,
    ) -> None:
        """TTS が空 PCM を返したら start/play を呼ばずに失敗扱い。"""
        import numpy as np
        ctrl = self._make_ctrl_with_devices(populated_registry, config, tmp_path)
        ctrl.load_model_layer(LayerKind.TTS)
        ctrl.load_model_layer(LayerKind.OUTPUT)
        tts = ctrl._backends[LayerKind.TTS]
        output = ctrl._backends[LayerKind.OUTPUT]
        tts.synthesize = MagicMock(return_value=(np.zeros(0, dtype=np.float32), 22050))

        with pytest.raises(RuntimeError, match="空"):
            ctrl.test_output_playback()
        # 合成のあとは Output に触らない(start も呼ばない)
        output.start.assert_not_called()

    def test_output_stop_called_even_if_play_raises(
        self, populated_registry, config, tmp_path,
    ) -> None:
        """play が例外でも stop は finally で呼ばれる(リソース解放保証)。"""
        import numpy as np
        ctrl = self._make_ctrl_with_devices(populated_registry, config, tmp_path)
        ctrl.load_model_layer(LayerKind.TTS)
        ctrl.load_model_layer(LayerKind.OUTPUT)
        tts = ctrl._backends[LayerKind.TTS]
        output = ctrl._backends[LayerKind.OUTPUT]
        pcm = np.array([0.1, 0.2], dtype=np.float32)
        tts.synthesize = MagicMock(return_value=(pcm, 22050))
        output.play = MagicMock(side_effect=RuntimeError("simulated"))

        with pytest.raises(RuntimeError, match="simulated"):
            ctrl.test_output_playback()
        output.stop.assert_called_once()


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
        ctrl.add_status_listener(lambda l, s: events.append((l, s)))

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


class TestGetLayerDevice:
    """get_layer_device(layer) の動作確認(UI が GPU/CPU 表示に使う API)。"""

    def test_returns_none_when_not_loaded(self, populated_registry, config) -> None:
        ctrl = AppController(registry=populated_registry, config=config)
        # ロード前は None
        assert ctrl.get_layer_device(LayerKind.ASR) is None
        assert ctrl.get_layer_device(LayerKind.TRANSLATOR) is None

    def test_returns_device_when_backend_has_attribute(
        self, populated_registry, config
    ) -> None:
        """device 属性を持つバックエンドはその値を返す。"""
        ctrl = AppController(registry=populated_registry, config=config)
        # 仮想バックエンドを差し込んで device 属性を持たせる
        fake_asr = MagicMock(name="asr_backend")
        fake_asr.device = "cuda"
        ctrl._backends[LayerKind.ASR] = fake_asr

        assert ctrl.get_layer_device(LayerKind.ASR) == "cuda"

    def test_returns_none_when_backend_has_no_device_attr(
        self, populated_registry, config
    ) -> None:
        """device 概念のないバックエンド(Capture/VAD/TTS/Output)は None を返す。"""
        ctrl = AppController(registry=populated_registry, config=config)
        # MagicMock は何でも返してしまうので、device 属性を持たない素オブジェクトを使う
        class _PlainBackend:
            pass

        ctrl._backends[LayerKind.TTS] = _PlainBackend()
        assert ctrl.get_layer_device(LayerKind.TTS) is None

    def test_empty_or_whitespace_device_returns_none(
        self, populated_registry, config
    ) -> None:
        ctrl = AppController(registry=populated_registry, config=config)
        fake = MagicMock()
        fake.device = "   "
        ctrl._backends[LayerKind.ASR] = fake
        assert ctrl.get_layer_device(LayerKind.ASR) is None


class TestCallbacks:
    def test_on_utterance_done_is_invoked_with_jsonl_write(
        self, populated_registry, config, tmp_path
    ) -> None:
        ctrl = AppController(registry=populated_registry, config=config)
        ctrl.set_setting("log", "directory", str(tmp_path / "logs"))

        seen: list[dict] = []
        ctrl.add_utterance_done_listener(lambda r: seen.append(r))

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

    def test_process_time_default_off(self, populated_registry, config) -> None:
        ctrl = AppController(registry=populated_registry, config=config)
        assert ctrl.get_setting("log", "process_time_enabled") is False


class TestProcessTimeLoggerWiring:
    """AppController から ProcessTimeLogger への配線確認。"""

    def test_logger_enabled_when_config_true(
        self, populated_registry, config, tmp_path
    ) -> None:
        import threading
        from voice_translator.common.process_time_logger import ProcessTimeLogger

        ctrl = AppController(registry=populated_registry, config=config)
        ctrl.set_setting("devices", "input", "mic_a")
        ctrl.set_setting("devices", "output", "hp")
        ctrl.set_setting("log", "directory", str(tmp_path / "logs"))
        ctrl.set_setting("log", "process_time_enabled", True)

        started = threading.Event()
        ctrl.start_pipeline_async(on_started=lambda: started.set())
        try:
            assert started.wait(timeout=3.0)
            assert isinstance(ctrl._process_time_logger, ProcessTimeLogger)
            assert ctrl._process_time_logger.enabled is True
        finally:
            ctrl.stop_pipeline()

    def test_logger_disabled_when_config_false(
        self, populated_registry, config, tmp_path
    ) -> None:
        import threading

        ctrl = AppController(registry=populated_registry, config=config)
        ctrl.set_setting("devices", "input", "mic_a")
        ctrl.set_setting("devices", "output", "hp")
        ctrl.set_setting("log", "directory", str(tmp_path / "logs"))
        # process_time_enabled は既定 False のままにする

        started = threading.Event()
        ctrl.start_pipeline_async(on_started=lambda: started.set())
        try:
            assert started.wait(timeout=3.0)
            assert ctrl._process_time_logger.enabled is False
        finally:
            ctrl.stop_pipeline()

    def test_handle_utterance_done_invokes_logger(
        self, populated_registry, config, tmp_path
    ) -> None:
        """完了通知時に CSV へ追記される(モック record で動作確認)。"""
        from voice_translator.common.process_time_logger import ProcessTimeLogger

        csv_path = tmp_path / "processtime.csv"
        ctrl = AppController(registry=populated_registry, config=config)
        ctrl._process_time_logger = ProcessTimeLogger(csv_path, enabled=True)

        record = _sample_record(seq_id=99)
        ctrl._handle_utterance_done(record)

        assert csv_path.exists(), "CSV が作成されていない"
        with csv_path.open("r", encoding="utf-8") as f:
            content = f.read()
        # ヘッダ + データ 1 行
        assert "seq_id" in content
        assert "99" in content


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
        """バックエンド名の変更は当該レイヤの evict + INIT のみ(自動再ロードはしない)。

        変更即ロードは廃止済み: 実ロードは Start / ↻ ロード / auto_load に寄せる
        (押し間違いで重いロードを走らせない・ロード中の再変更で UI を固めない)。
        """
        import threading

        # ASR にもう1つ実装を追加して切り替えできるようにする
        populated_registry.register(
            LayerKind.ASR, "alt_asr", _fake_simple_backend
        )
        ctrl = AppController(registry=populated_registry, config=config)
        ctrl.load_models()

        kept_layers = {l: ctrl._backends[l] for l in LayerKind if l != LayerKind.ASR}
        old_asr = ctrl._backends[LayerKind.ASR]

        ctrl.set_setting("backends", "asr", "alt_asr")
        # 変更は「選択」のみ: キャッシュから外れ、ロードスレッドは起動しない
        assert LayerKind.ASR not in ctrl._backends, "変更直後に自動ロードが走っている"
        assert ctrl.get_model_status(LayerKind.ASR) == ModelStatus.INIT
        assert not [
            t for t in threading.enumerate() if t.name.startswith("vt_reload")
        ], "廃止したはずの自動再ロードスレッドが起動している"
        # 他レイヤは触られていない
        for layer in LayerKind:
            if layer == LayerKind.ASR:
                continue
            assert ctrl._backends[layer] is kept_layers[layer], (
                f"{layer}: 設定変更で触らなくていいキャッシュが破棄された"
            )
        # 次の明示ロード(↻ ロード / Start 相当)で新しい選択が入る
        ctrl.load_model_layer(LayerKind.ASR)
        assert ctrl._backends[LayerKind.ASR] is not old_asr

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


class TestPipelineQueueConfig:
    """config.yaml の pipeline セクションがコーディネータに反映されることを確認。"""

    def test_default_queue_config_values(
        self, populated_registry, config
    ) -> None:
        ctrl = AppController(registry=populated_registry, config=config)
        assert ctrl.get_setting("pipeline", "captured_queue_max_bytes") == 10_000_000
        assert ctrl.get_setting("pipeline", "synthesized_queue_max_bytes") == 5_000_000
        assert ctrl.get_setting("pipeline", "recognized_queue_size") == 10
        assert ctrl.get_setting("pipeline", "translated_queue_size") == 10

    def test_queue_config_propagates_to_coordinator(
        self, populated_registry, config, tmp_path
    ) -> None:
        from voice_translator.common.bounded_queue import ByteBoundedQueue

        ctrl = AppController(registry=populated_registry, config=config)
        ctrl.set_setting("devices", "input", "mic_a")
        ctrl.set_setting("devices", "output", "hp")
        ctrl.set_setting("log", "directory", str(tmp_path / "logs"))
        # 既定値を上書き
        ctrl.set_setting("pipeline", "captured_queue_max_bytes", 12_345)
        ctrl.set_setting("pipeline", "synthesized_queue_max_bytes", 67_890)
        ctrl.set_setting("pipeline", "recognized_queue_size", 7)
        ctrl.set_setting("pipeline", "translated_queue_size", 3)

        ctrl.start_pipeline()
        try:
            assert isinstance(ctrl._coord._captured_queue, ByteBoundedQueue)
            assert ctrl._coord._captured_queue.max_bytes == 12_345
            assert isinstance(ctrl._coord._synthesized_queue, ByteBoundedQueue)
            assert ctrl._coord._synthesized_queue.max_bytes == 67_890
            # テキスト系は queue.Queue で maxsize 反映
            assert ctrl._coord._recognized_queue.maxsize == 7
            assert ctrl._coord._translated_queue.maxsize == 3
        finally:
            ctrl.stop_pipeline()


class TestPhaseA2StatusDelegation:
    """Phase A2: AppController._model_status は廃止、状態の真実は backend 側にある。"""

    def test_get_model_status_delegates_to_backend(
        self, populated_registry, config
    ) -> None:
        ctrl = AppController(registry=populated_registry, config=config)
        ctrl.load_models()
        # mock backend は LOADED を返すよう仕込んである
        assert ctrl.get_model_status(LayerKind.ASR) == ModelStatus.LOADED

    def test_get_model_status_returns_init_when_not_loaded(
        self, populated_registry, config
    ) -> None:
        ctrl = AppController(registry=populated_registry, config=config)
        # ロード前は backend 不在 → INIT
        for layer in LayerKind:
            assert ctrl.get_model_status(layer) == ModelStatus.INIT

    def test_subscribe_called_on_load(
        self, populated_registry, config
    ) -> None:
        """ロード時に AppController が各 backend の subscribe を呼ぶ。"""
        ctrl = AppController(registry=populated_registry, config=config)
        ctrl.load_models()
        for layer in LayerKind:
            backend = ctrl._backends[layer]
            assert backend.subscribe.called, f"{layer}: subscribe 未呼び出し"

    def test_eviction_unsubscribes(
        self, populated_registry, config
    ) -> None:
        """backend 差し替え時に旧 backend の subscription が解除される。"""
        populated_registry.register(LayerKind.ASR, "alt_asr", _fake_simple_backend)
        ctrl = AppController(registry=populated_registry, config=config)
        ctrl.load_models()
        old_sub = ctrl._backend_subscriptions[LayerKind.ASR]

        ctrl.set_setting("backends", "asr", "alt_asr")
        # evict は同期で完了する(自動再ロードはしないので待ち合わせ不要)
        assert old_sub.unsubscribe.called, "旧 backend の subscription が解除されていない"
        assert LayerKind.ASR not in ctrl._backend_subscriptions


class TestPhaseA2MultiListener:
    """`add_status_listener` で複数 UI listener を扱える(R2-6)。"""

    def test_listener_invoked_on_load(self, populated_registry, config) -> None:
        ctrl = AppController(registry=populated_registry, config=config)
        events: list[tuple[LayerKind, ModelStatus]] = []
        sub = ctrl.add_status_listener(lambda l, s: events.append((l, s)))
        try:
            ctrl.load_models()
        finally:
            sub.unsubscribe()
        # 各レイヤで LOADING と LOADED が観測される
        for layer in LayerKind:
            seen = [s for (l, s) in events if l == layer]
            assert ModelStatus.LOADING in seen
            assert seen[-1] == ModelStatus.LOADED

    def test_unsubscribe_stops_notifications(
        self, populated_registry, config
    ) -> None:
        ctrl = AppController(registry=populated_registry, config=config)
        events: list[tuple[LayerKind, ModelStatus]] = []
        sub = ctrl.add_status_listener(lambda l, s: events.append((l, s)))
        sub.unsubscribe()
        ctrl.load_models()
        assert events == []

    def test_multiple_listeners_all_notified(
        self, populated_registry, config
    ) -> None:
        ctrl = AppController(registry=populated_registry, config=config)
        seen_a: list[tuple[LayerKind, ModelStatus]] = []
        seen_b: list[tuple[LayerKind, ModelStatus]] = []
        ctrl.add_status_listener(lambda l, s: seen_a.append((l, s)))
        ctrl.add_status_listener(lambda l, s: seen_b.append((l, s)))
        ctrl.load_models()
        assert seen_a == seen_b
        assert len(seen_a) > 0

    def test_set_callbacks_is_removed(self, populated_registry, config) -> None:
        """旧 set_callbacks(single callback 互換層)は P2 で撤去済み。

        旧テスト test_old_single_callback_still_works が守っていた「状態変化が
        UI に届く」契約は add_status_listener 系(本クラスの他テスト)で温存。
        """
        ctrl = AppController(registry=populated_registry, config=config)
        assert not hasattr(ctrl, "set_callbacks")

    def test_listener_exception_does_not_break_others(
        self, populated_registry, config
    ) -> None:
        ctrl = AppController(registry=populated_registry, config=config)
        seen: list[tuple[LayerKind, ModelStatus]] = []

        def bad(_l, _s):
            raise RuntimeError("listener bug")

        ctrl.add_status_listener(bad)
        ctrl.add_status_listener(lambda l, s: seen.append((l, s)))
        ctrl.load_models()
        assert len(seen) > 0  # 後の listener が呼ばれている


class TestPhaseA2LoadModelLayer:
    """`load_model_layer(layer)` の単体ロード。"""

    def test_single_layer_loaded(self, populated_registry, config) -> None:
        ctrl = AppController(registry=populated_registry, config=config)
        ctrl.load_model_layer(LayerKind.ASR)
        assert LayerKind.ASR in ctrl._backends
        # 他レイヤは未ロード
        for layer in LayerKind:
            if layer == LayerKind.ASR:
                continue
            assert layer not in ctrl._backends

    def test_idempotent(self, populated_registry, config) -> None:
        ctrl = AppController(registry=populated_registry, config=config)
        ctrl.load_model_layer(LayerKind.ASR)
        before = ctrl._backends[LayerKind.ASR]
        ctrl.load_model_layer(LayerKind.ASR)
        after = ctrl._backends[LayerKind.ASR]
        assert before is after

    def test_failure_propagates_and_emits_not_downloaded(
        self, populated_registry, config
    ) -> None:
        """ロード失敗時は例外伝播 + status=NOT_DOWNLOADED 通知。"""
        def _failing_factory():
            raise RuntimeError("model not found")

        populated_registry.register(LayerKind.ASR, "broken", _failing_factory)
        ctrl = AppController(registry=populated_registry, config=config)
        ctrl.set_setting("backends", "asr", "broken")  # 変更は選択のみ(自動ロードなし)
        assert ctrl.get_model_status(LayerKind.ASR) == ModelStatus.INIT

        statuses: list[ModelStatus] = []
        ctrl.add_status_listener(
            lambda l, s: statuses.append(s) if l == LayerKind.ASR else None
        )
        with pytest.raises(RuntimeError):
            ctrl.load_model_layer(LayerKind.ASR)
        # 失敗ロードなので backend は不在、最終通知は NOT_DOWNLOADED
        assert LayerKind.ASR not in ctrl._backends
        assert statuses[-1] == ModelStatus.NOT_DOWNLOADED


class TestPhaseA2RecentDurations:
    """`get_recent_durations(layer)` のリングバッファ動作。"""

    def test_initial_empty(self, populated_registry, config) -> None:
        ctrl = AppController(registry=populated_registry, config=config)
        for layer in LayerKind:
            assert ctrl.get_recent_durations(layer) == []

    def test_handle_utterance_done_pushes_durations(
        self, populated_registry, config
    ) -> None:
        ctrl = AppController(registry=populated_registry, config=config)
        t0 = monotonic()
        record = {
            "seq_id": 1,
            "src_text": "x",
            "src_lang": "en",
            "tgt_text": "y",
            "tgt_lang": "ja",
            "timeline": {
                "t_capture": t0,
                "t_vad_end": t0 + 0.1,
                "t_asr_start": t0 + 0.1,
                "t_asr": t0 + 0.3,
                "t_translate_start": t0 + 0.3,
                "t_translate": t0 + 0.5,
                "t_tts_start": t0 + 0.5,
                "t_tts": t0 + 0.7,
                "t_playback_start": t0 + 0.7,
                "t_playback": t0 + 0.8,
            },
        }
        ctrl._handle_utterance_done(record)
        # VAD: 100ms, ASR: 200ms, Translator: 200ms, TTS: 200ms, Output: 100ms
        assert ctrl.get_recent_durations(LayerKind.VAD) == pytest.approx([100.0], rel=0.01)
        assert ctrl.get_recent_durations(LayerKind.ASR) == pytest.approx([200.0], rel=0.01)
        assert ctrl.get_recent_durations(LayerKind.TRANSLATOR) == pytest.approx([200.0], rel=0.01)
        assert ctrl.get_recent_durations(LayerKind.TTS) == pytest.approx([200.0], rel=0.01)
        assert ctrl.get_recent_durations(LayerKind.OUTPUT) == pytest.approx([100.0], rel=0.01)

    def test_missing_timeline_marker_is_skipped(
        self, populated_registry, config
    ) -> None:
        """timeline に欠落があれば該当レイヤだけスキップ。"""
        ctrl = AppController(registry=populated_registry, config=config)
        t0 = monotonic()
        record = {
            "seq_id": 1,
            "timeline": {
                "t_asr_start": t0,
                "t_asr": t0 + 0.1,
                # 他のマーカーは欠落
            },
        }
        ctrl._handle_utterance_done(record)
        assert len(ctrl.get_recent_durations(LayerKind.ASR)) == 1
        # 他レイヤは何も積まれていない
        assert ctrl.get_recent_durations(LayerKind.VAD) == []
        assert ctrl.get_recent_durations(LayerKind.TRANSLATOR) == []

    def test_ring_buffer_keeps_only_recent(
        self, populated_registry, config
    ) -> None:
        ctrl = AppController(registry=populated_registry, config=config)
        for i in range(8):
            t0 = monotonic()
            record = {
                "seq_id": i,
                "timeline": {
                    "t_asr_start": t0,
                    "t_asr": t0 + (i + 1) * 0.01,  # 10, 20, 30,...ms
                },
            }
            ctrl._handle_utterance_done(record)
        durations = ctrl.get_recent_durations(LayerKind.ASR)
        assert len(durations) == 5
        # 直近 5 件(seq_id=3..7、つまり 40..80 ms 近辺)
        assert durations[0] == pytest.approx(40.0, rel=0.01)
        assert durations[-1] == pytest.approx(80.0, rel=0.01)


class TestPhaseBAutoLoad:
    """Phase B: auto_load 既定 OFF / 起動時は対象レイヤだけロード。"""

    def test_default_no_auto_load_layers(self, populated_registry, config) -> None:
        """既定では全 backend が auto_load=False なので対象レイヤは無い。"""
        ctrl = AppController(registry=populated_registry, config=config)
        assert ctrl.get_auto_load_layers() == []

    def test_auto_load_layer_picked_up(self, populated_registry, config) -> None:
        """選択中 backend の auto_load を True にするとそのレイヤが対象になる。"""
        ctrl = AppController(registry=populated_registry, config=config)
        ctrl.set_setting("backends_config", "faster_whisper", "auto_load", True)
        layers = ctrl.get_auto_load_layers()
        assert layers == [LayerKind.ASR]

    def test_auto_load_layer_changes_with_backend_switch(
        self, populated_registry, config
    ) -> None:
        """別 backend に切り替えると、その backend の auto_load 設定が効く。"""
        populated_registry.register(LayerKind.ASR, "alt_asr", _fake_simple_backend)
        ctrl = AppController(registry=populated_registry, config=config)
        # faster_whisper.auto_load = True
        ctrl.set_setting("backends_config", "faster_whisper", "auto_load", True)
        assert ctrl.get_auto_load_layers() == [LayerKind.ASR]
        # alt_asr へ切替(設定では auto_load 未指定 = False)
        ctrl.set_setting("backends", "asr", "alt_asr")
        assert ctrl.get_auto_load_layers() == []

    def test_load_auto_load_layers_async_no_target_fires_on_done(
        self, populated_registry, config
    ) -> None:
        """対象レイヤなしなら即時 on_done。"""
        import threading
        ctrl = AppController(registry=populated_registry, config=config)
        done = threading.Event()
        ctrl.load_auto_load_layers_async(on_done=lambda: done.set())
        assert done.wait(timeout=1.0)
        # 何もロードされていない
        assert not ctrl._backends

    def test_load_auto_load_layers_async_loads_only_target(
        self, populated_registry, config
    ) -> None:
        """auto_load=True のレイヤだけがロードされる。"""
        import threading
        ctrl = AppController(registry=populated_registry, config=config)
        ctrl.set_setting("backends_config", "faster_whisper", "auto_load", True)
        ctrl.set_setting("backends_config", "nllb200", "auto_load", True)

        done = threading.Event()
        ctrl.load_auto_load_layers_async(on_done=lambda: done.set())
        assert done.wait(timeout=3.0)
        assert LayerKind.ASR in ctrl._backends
        assert LayerKind.TRANSLATOR in ctrl._backends
        # 他は未ロード
        for layer in (LayerKind.CAPTURE, LayerKind.VAD, LayerKind.TTS, LayerKind.OUTPUT):
            assert layer not in ctrl._backends


class TestPhaseBStartButtonAlwaysOk:
    """Phase B: 開始ボタンは未ロード状態でも押せて、押下時に必要分だけロード→起動する。"""

    def test_start_async_loads_then_starts_when_nothing_preloaded(
        self, populated_registry, config, tmp_path
    ) -> None:
        """事前 load_models 無しでも、Start でロード→起動が一気通貫で完了する。"""
        import threading
        ctrl = AppController(registry=populated_registry, config=config)
        ctrl.set_setting("devices", "input", "mic_a")
        ctrl.set_setting("devices", "output", "hp")
        ctrl.set_setting("log", "directory", str(tmp_path / "logs"))
        assert not ctrl.is_loaded  # 未ロード

        started = threading.Event()
        ctrl.start_pipeline_async(on_started=lambda: started.set())
        try:
            assert started.wait(timeout=3.0)
            assert ctrl.is_running
            assert ctrl.is_loaded  # 起動時に裏でロードされた
        finally:
            ctrl.stop_pipeline()

    def test_start_sync_loads_when_not_preloaded(
        self, populated_registry, config, tmp_path
    ) -> None:
        ctrl = AppController(registry=populated_registry, config=config)
        ctrl.set_setting("devices", "input", "mic_a")
        ctrl.set_setting("devices", "output", "hp")
        ctrl.set_setting("log", "directory", str(tmp_path / "logs"))
        ctrl.start_pipeline()  # 同期版でも自動ロードされること
        try:
            assert ctrl.is_running
            assert ctrl.is_loaded
        finally:
            ctrl.stop_pipeline()


class TestPhaseBMissingCredentialsGate:
    """Phase B: MISSING_CREDENTIALS のレイヤがあると start を gate する(Phase D で本格化)。"""

    def test_start_raises_when_layer_missing_credentials(
        self, populated_registry, config, tmp_path
    ) -> None:
        ctrl = AppController(registry=populated_registry, config=config)
        ctrl.set_setting("devices", "input", "mic_a")
        ctrl.set_setting("devices", "output", "hp")
        ctrl.set_setting("log", "directory", str(tmp_path / "logs"))
        ctrl.load_model_layer(LayerKind.ASR)
        # backend の get_status を MISSING_CREDENTIALS に差し替え
        ctrl._backends[LayerKind.ASR].get_status = MagicMock(
            return_value=ModelStatus.MISSING_CREDENTIALS
        )

        with pytest.raises(FatalError, match="認証情報"):
            ctrl.start_pipeline()


class TestPhaseBConfigDefaults:
    """Phase B: backends_config.<backend>.auto_load と consents.* の既定値。"""

    def test_auto_load_defaults_false_for_all_backends(
        self, populated_registry, config
    ) -> None:
        ctrl = AppController(registry=populated_registry, config=config)
        for backend_name in (
            "soundcard", "sapi", "silero", "faster_whisper", "nllb200"
        ):
            assert (
                ctrl.get_setting("backends_config", backend_name, "auto_load") is False
            ), f"{backend_name}: auto_load の既定が False でない"

    def test_consents_suppress_dialogs_default_false(
        self, populated_registry, config
    ) -> None:
        ctrl = AppController(registry=populated_registry, config=config)
        assert ctrl.get_setting("consents", "suppress_dialogs") is False


class TestPhaseC3StatusSummary:
    """Phase C3 改(P1): ステータスはデータ(get_status_snapshot)で返す。

    旧 get_status_summary のシナリオを温存し、snapshot + gui/logic の
    format_status_summary の組で同じ表示が得られることを検証する
    (整形そのものの詳細は tests/test_logic_status_summary.py)。
    """

    @staticmethod
    def _summary_text(ctrl: AppController) -> str:
        """旧 get_status_summary 相当のテキストを snapshot + formatter で得る。"""
        from voice_translator.gui.logic.status_summary import format_status_summary

        lines, errors = ctrl.get_status_snapshot()
        return format_status_summary(lines, errors, [])

    def test_snapshot_lists_all_layers(self, populated_registry, config) -> None:
        ctrl = AppController(registry=populated_registry, config=config)
        ctrl.load_models()
        lines, _errors = ctrl.get_status_snapshot()
        assert [line.layer for line in lines] == list(LayerKind)
        summary = self._summary_text(ctrl)
        for layer in LayerKind:
            assert f"[{layer.value}]" in summary, f"{layer} 未含有"
            # backend 名(faster_whisper 等)も載っている
        assert "faster_whisper" in summary
        assert "Loaded" in summary

    def test_snapshot_includes_backend_name(self, populated_registry, config) -> None:
        ctrl = AppController(registry=populated_registry, config=config)
        summary = self._summary_text(ctrl)
        # 未ロードでも backend 名 + INIT は出る
        assert "faster_whisper" in summary
        assert "Init" in summary

    def test_snapshot_groups_errors(self, populated_registry, config) -> None:
        """各 backend の get_recent_errors を集約して末尾に追記する。"""
        from voice_translator.common.types import ErrorRecord
        ctrl = AppController(registry=populated_registry, config=config)
        ctrl.load_models()
        # 該当 backend にエラー履歴を仕込む
        fake_record = ErrorRecord(
            timestamp=1000.0,
            message="model not found",
            exc_type="OSError",
            context="model load",
        )
        ctrl._backends[LayerKind.ASR].get_recent_errors = MagicMock(
            return_value=[fake_record]
        )
        summary = self._summary_text(ctrl)
        assert "最近のエラー:" in summary
        assert "OSError" in summary
        assert "model not found" in summary
        assert "[asr]" in summary

    def test_snapshot_omits_error_section_when_no_errors(
        self, populated_registry, config
    ) -> None:
        ctrl = AppController(registry=populated_registry, config=config)
        ctrl.load_models()
        summary = self._summary_text(ctrl)
        # backend のエラー履歴が空のとき「最近のエラー:」セクションは出ない
        assert "最近のエラー:" not in summary

    def test_snapshot_shows_downloading_size_hint(
        self, populated_registry, config
    ) -> None:
        """DOWNLOADING 状態のレイヤがあると、list_recommended_models 先頭の size を併記。"""
        from voice_translator.common.types import ModelInfo
        ctrl = AppController(registry=populated_registry, config=config)
        ctrl.load_models()
        ctrl._backends[LayerKind.ASR].get_status = MagicMock(
            return_value=ModelStatus.DOWNLOADING
        )
        ctrl._backends[LayerKind.ASR].list_recommended_models = MagicMock(
            return_value=[
                ModelInfo(name="small", display_name="Small", download_size_gb=0.46),
            ]
        )
        lines, _errors = ctrl.get_status_snapshot()
        asr_line = next(line for line in lines if line.layer == LayerKind.ASR)
        # ~0.5GB 表示が含まれる(0.46GB → "~0.5GB" の表示、先頭スペース込み)
        assert asr_line.dl_size_hint == " (~0.5GB)"
        assert "0.5GB" in self._summary_text(ctrl)

    def test_snapshot_handles_missing_list_recommended_models(
        self, populated_registry, config
    ) -> None:
        """list_recommended_models が例外でも snapshot は落ちない(縮退)。"""
        ctrl = AppController(registry=populated_registry, config=config)
        ctrl.load_models()
        ctrl._backends[LayerKind.ASR].get_status = MagicMock(
            return_value=ModelStatus.DOWNLOADING
        )
        ctrl._backends[LayerKind.ASR].list_recommended_models = MagicMock(
            side_effect=RuntimeError("boom")
        )
        # 例外で落ちないこと
        summary = self._summary_text(ctrl)
        assert "Downloading" in summary


class TestReloadModelLayer:
    """`reload_model_layer(layer)` の挙動。既ロード時は evict → 作り直し。"""

    def test_reload_creates_new_instance(self, populated_registry, config) -> None:
        ctrl = AppController(registry=populated_registry, config=config)
        ctrl.load_model_layer(LayerKind.ASR)
        old = ctrl._backends[LayerKind.ASR]
        ctrl.reload_model_layer(LayerKind.ASR)
        new = ctrl._backends[LayerKind.ASR]
        assert new is not old, "reload で新インスタンスに置き換わるはず"

    def test_reload_on_unloaded_layer_just_loads(
        self, populated_registry, config
    ) -> None:
        ctrl = AppController(registry=populated_registry, config=config)
        assert LayerKind.ASR not in ctrl._backends
        ctrl.reload_model_layer(LayerKind.ASR)
        assert LayerKind.ASR in ctrl._backends

    def test_reload_unsubscribes_old_subscription(
        self, populated_registry, config
    ) -> None:
        ctrl = AppController(registry=populated_registry, config=config)
        ctrl.load_model_layer(LayerKind.ASR)
        old_sub = ctrl._backend_subscriptions[LayerKind.ASR]
        ctrl.reload_model_layer(LayerKind.ASR)
        assert old_sub.unsubscribe.called


class TestEvictModelLayer:
    """`evict_model_layer(layer)` の挙動。破棄するが再 load はしない(2026-05-30)。"""

    def test_evict_removes_backend_from_cache(
        self, populated_registry, config
    ) -> None:
        ctrl = AppController(registry=populated_registry, config=config)
        ctrl.load_model_layer(LayerKind.ASR)
        assert LayerKind.ASR in ctrl._backends
        ctrl.evict_model_layer(LayerKind.ASR)
        assert LayerKind.ASR not in ctrl._backends

    def test_evict_does_not_reload(self, populated_registry, config) -> None:
        """evict 後は backend が存在しない(reload とは違って自動で作り直さない)。"""
        ctrl = AppController(registry=populated_registry, config=config)
        ctrl.load_model_layer(LayerKind.ASR)
        ctrl.evict_model_layer(LayerKind.ASR)
        # 再 load されないこと
        assert LayerKind.ASR not in ctrl._backends
        # status は INIT に戻る
        assert ctrl.get_model_status(LayerKind.ASR).value == "Init"

    def test_evict_unsubscribes_old_subscription(
        self, populated_registry, config
    ) -> None:
        ctrl = AppController(registry=populated_registry, config=config)
        ctrl.load_model_layer(LayerKind.ASR)
        old_sub = ctrl._backend_subscriptions[LayerKind.ASR]
        ctrl.evict_model_layer(LayerKind.ASR)
        assert old_sub.unsubscribe.called

    def test_evict_unloaded_layer_is_noop(
        self, populated_registry, config
    ) -> None:
        ctrl = AppController(registry=populated_registry, config=config)
        assert LayerKind.ASR not in ctrl._backends
        # 未ロードのレイヤを evict しても例外にならない
        ctrl.evict_model_layer(LayerKind.ASR)
        assert LayerKind.ASR not in ctrl._backends

    def test_evict_then_load_models_recreates(
        self, populated_registry, config
    ) -> None:
        """evict → load_models で新インスタンスが入る(設定変更の反映フロー)。"""
        ctrl = AppController(registry=populated_registry, config=config)
        ctrl.load_model_layer(LayerKind.ASR)
        old = ctrl._backends[LayerKind.ASR]
        ctrl.evict_model_layer(LayerKind.ASR)
        ctrl.load_models()  # 中央ロードボタン相当の経路
        new = ctrl._backends[LayerKind.ASR]
        assert new is not old


class TestPhaseDCredentials:
    """Phase D: AppController が CredentialsStore を仲介する。"""

    def test_set_then_get_credential(
        self, populated_registry, config, tmp_path, monkeypatch
    ) -> None:
        from tests._fixtures import InMemoryKeyring
        import keyring
        keyring.set_keyring(InMemoryKeyring())
        ctrl = AppController(registry=populated_registry, config=config)
        ctrl.set_credential("openai", "api_key", "sk-test")
        assert ctrl.get_credential("openai", "api_key") == "sk-test"
        assert ctrl.has_credential("openai", "api_key") is True

    def test_has_credential_returns_false_when_unset(
        self, populated_registry, config
    ) -> None:
        from tests._fixtures import InMemoryKeyring
        import keyring
        keyring.set_keyring(InMemoryKeyring())
        ctrl = AppController(registry=populated_registry, config=config)
        assert ctrl.has_credential("openai", "api_key") is False

    def test_delete_credential(
        self, populated_registry, config
    ) -> None:
        from tests._fixtures import InMemoryKeyring
        import keyring
        keyring.set_keyring(InMemoryKeyring())
        ctrl = AppController(registry=populated_registry, config=config)
        ctrl.set_credential("a", "k", "v")
        ctrl.delete_credential("a", "k")
        assert ctrl.get_credential("a", "k") is None

    def test_use_local_file_flag_respected(
        self, populated_registry, config, tmp_path, monkeypatch
    ) -> None:
        """ConfigStore の credentials.use_local_file=True で file モードになる。"""
        # cwd を tmp_path に移して local.secrets が散らからないように
        monkeypatch.chdir(tmp_path)
        config.set("credentials", "use_local_file", True)
        ctrl = AppController(registry=populated_registry, config=config)
        # 初回 set で内部 store が生成される(P3: 実体は CredentialsService 側)
        ctrl.set_credential("deepl", "api_key", "v")
        store = ctrl.credentials._store  # noqa: SLF001
        assert store is not None
        assert store.mode == "file"


class TestPhaseE2CredentialFlow:
    """Phase E-2: 認証情報フローのパッキング(spec / verify / verified / gate)。"""

    def _setup_with_cloud_backend(self, populated_registry, config, tmp_path, monkeypatch):
        """`requires_credentials=True` の cloud backend をテストレジストリに足したセット。"""
        from voice_translator.common.types import (
            BackendCapabilities, CredentialField, VerifyResult,
        )
        from tests._fixtures import InMemoryKeyring
        import keyring
        keyring.set_keyring(InMemoryKeyring())
        monkeypatch.chdir(tmp_path)

        verify_calls: list[dict] = []

        class _FakeCloudAsr:
            ok_value = True
            ok_message = "OK"

            @classmethod
            def credential_spec(cls):
                return [
                    CredentialField("api_key", "API Key", secret=True),
                ]

            @classmethod
            def verify_credentials(cls, values):
                verify_calls.append(dict(values))
                return VerifyResult(ok=cls.ok_value, message=cls.ok_message)

        populated_registry.register(
            LayerKind.ASR, "fake_cloud_asr",
            lambda: _fake_simple_backend(),
            backend_cls=_FakeCloudAsr,
            capabilities=BackendCapabilities(
                is_cloud=True, requires_credentials=True,
                service_name="FakeCloud ASR",
            ),
        )
        ctrl = AppController(registry=populated_registry, config=config)
        return ctrl, _FakeCloudAsr, verify_calls

    def test_get_credential_spec_returns_fields(
        self, populated_registry, config, tmp_path, monkeypatch
    ) -> None:
        ctrl, _, _ = self._setup_with_cloud_backend(
            populated_registry, config, tmp_path, monkeypatch
        )
        spec = ctrl.get_credential_spec(LayerKind.ASR, "fake_cloud_asr")
        assert len(spec) == 1
        assert spec[0].key_name == "api_key"

    def test_get_credential_spec_empty_for_unregistered_class(
        self, populated_registry, config
    ) -> None:
        ctrl = AppController(registry=populated_registry, config=config)
        # 既存 mock backend は backend_cls 未登録
        assert ctrl.get_credential_spec(LayerKind.ASR, "faster_whisper") == []

    def test_verify_success_saves_credential_and_sets_verified(
        self, populated_registry, config, tmp_path, monkeypatch
    ) -> None:
        ctrl, cls, calls = self._setup_with_cloud_backend(
            populated_registry, config, tmp_path, monkeypatch
        )
        result = ctrl.verify_and_save_credentials(
            LayerKind.ASR, "fake_cloud_asr", {"api_key": "sk-test"}
        )
        assert result.ok is True
        # キーが保存され
        assert ctrl.get_credential("fake_cloud_asr", "api_key") == "sk-test"
        # verified=True が立つ
        assert ctrl.is_backend_verified("fake_cloud_asr") is True
        # verify_credentials に渡された値も確認
        assert calls == [{"api_key": "sk-test"}]

    def test_verify_failure_does_not_save_or_set_verified(
        self, populated_registry, config, tmp_path, monkeypatch
    ) -> None:
        ctrl, cls, _ = self._setup_with_cloud_backend(
            populated_registry, config, tmp_path, monkeypatch
        )
        cls.ok_value = False
        cls.ok_message = "auth failed"
        result = ctrl.verify_and_save_credentials(
            LayerKind.ASR, "fake_cloud_asr", {"api_key": "bad"}
        )
        assert result.ok is False
        assert result.message == "auth failed"
        assert ctrl.get_credential("fake_cloud_asr", "api_key") is None
        assert ctrl.is_backend_verified("fake_cloud_asr") is False

    def test_set_credential_resets_verified(
        self, populated_registry, config, tmp_path, monkeypatch
    ) -> None:
        """キーが変わったら再認証必須(verified を False に戻す)。"""
        ctrl, _, _ = self._setup_with_cloud_backend(
            populated_registry, config, tmp_path, monkeypatch
        )
        ctrl.verify_and_save_credentials(
            LayerKind.ASR, "fake_cloud_asr", {"api_key": "v1"}
        )
        assert ctrl.is_backend_verified("fake_cloud_asr") is True
        # 直接 set_credential するとリセット
        ctrl.set_credential("fake_cloud_asr", "api_key", "v2")
        assert ctrl.is_backend_verified("fake_cloud_asr") is False

    def test_invalidate_verification_clears_flag(
        self, populated_registry, config, tmp_path, monkeypatch
    ) -> None:
        """サブスク切れ等で動作中に呼ばれる。"""
        ctrl, _, _ = self._setup_with_cloud_backend(
            populated_registry, config, tmp_path, monkeypatch
        )
        ctrl.verify_and_save_credentials(
            LayerKind.ASR, "fake_cloud_asr", {"api_key": "v1"}
        )
        assert ctrl.is_backend_verified("fake_cloud_asr") is True
        ctrl.invalidate_verification("fake_cloud_asr")
        assert ctrl.is_backend_verified("fake_cloud_asr") is False

    def test_start_gate_blocks_when_credentials_missing(
        self, populated_registry, config, tmp_path, monkeypatch
    ) -> None:
        ctrl, _, _ = self._setup_with_cloud_backend(
            populated_registry, config, tmp_path, monkeypatch
        )
        ctrl.set_setting("backends", "asr", "fake_cloud_asr")
        ctrl.set_setting("devices", "input", "mic_a")
        ctrl.set_setting("devices", "output", "hp")
        ctrl.set_setting("log", "directory", str(tmp_path / "logs"))
        with pytest.raises(FatalError, match="認証情報未入力"):
            ctrl.start_pipeline()

    def test_start_gate_blocks_when_not_verified(
        self, populated_registry, config, tmp_path, monkeypatch
    ) -> None:
        ctrl, _, _ = self._setup_with_cloud_backend(
            populated_registry, config, tmp_path, monkeypatch
        )
        ctrl.set_setting("backends", "asr", "fake_cloud_asr")
        ctrl.set_setting("devices", "input", "mic_a")
        ctrl.set_setting("devices", "output", "hp")
        ctrl.set_setting("log", "directory", str(tmp_path / "logs"))
        # キーは保存したが verify を通していない
        ctrl.set_credential("fake_cloud_asr", "api_key", "sk-stored-but-unverified")
        with pytest.raises(FatalError, match="未検証"):
            ctrl.start_pipeline()

    def test_start_gate_passes_after_verification(
        self, populated_registry, config, tmp_path, monkeypatch
    ) -> None:
        ctrl, _, _ = self._setup_with_cloud_backend(
            populated_registry, config, tmp_path, monkeypatch
        )
        ctrl.set_setting("backends", "asr", "fake_cloud_asr")
        ctrl.set_setting("devices", "input", "mic_a")
        ctrl.set_setting("devices", "output", "hp")
        ctrl.set_setting("log", "directory", str(tmp_path / "logs"))
        # キーを入力 → verify → 保存(verified=True)
        ctrl.verify_and_save_credentials(
            LayerKind.ASR, "fake_cloud_asr", {"api_key": "good"}
        )
        # ここで gate を通過、start_pipeline がエラーなく走る(stop は必ず)
        ctrl.start_pipeline()
        try:
            assert ctrl.is_running
        finally:
            ctrl.stop_pipeline()


class TestAuthStateAndCredentialEvents:
    """認証準備状態の互換窓(`get_auth_state`)と credentials イベントの発火。

    セットアップは TestPhaseE2CredentialFlow と同じ fake cloud backend を共有する
    (継承すると親のテストが二重実行されるため、ヘルパーだけ借りる)。
    """

    _setup_with_cloud_backend = TestPhaseE2CredentialFlow._setup_with_cloud_backend

    def test_get_auth_state_for_selected_backend(
        self, populated_registry, config, tmp_path, monkeypatch
    ) -> None:
        """選択中 backend の AuthState を静的に返す(ロード不要)。"""
        from voice_translator.common.types import AuthState
        ctrl, _, _ = self._setup_with_cloud_backend(
            populated_registry, config, tmp_path, monkeypatch
        )
        # ローカル backend(既定の faster_whisper)は NOT_REQUIRED
        assert ctrl.get_auth_state(LayerKind.ASR) == AuthState.NOT_REQUIRED
        # cloud に切り替えると(未ロードのまま)MISSING になる
        ctrl.set_setting("backends", "asr", "fake_cloud_asr")
        assert LayerKind.ASR not in ctrl._backends  # ロードされていないことを確認
        assert ctrl.get_auth_state(LayerKind.ASR) == AuthState.MISSING
        # 鍵だけ保存 → UNVERIFIED、verify 通過 → VERIFIED
        ctrl.set_credential("fake_cloud_asr", "api_key", "sk-x")
        assert ctrl.get_auth_state(LayerKind.ASR) == AuthState.UNVERIFIED
        ctrl.verify_and_save_credentials(
            LayerKind.ASR, "fake_cloud_asr", {"api_key": "sk-x"}
        )
        assert ctrl.get_auth_state(LayerKind.ASR) == AuthState.VERIFIED

    def test_get_all_auth_states_covers_all_layers(
        self, populated_registry, config, tmp_path, monkeypatch
    ) -> None:
        from voice_translator.common.types import AuthState
        ctrl, _, _ = self._setup_with_cloud_backend(
            populated_registry, config, tmp_path, monkeypatch
        )
        states = ctrl.get_all_auth_states()
        assert set(states.keys()) == set(LayerKind)
        assert all(isinstance(s, AuthState) for s in states.values())

    def test_credential_changes_emit_settings_event(
        self, populated_registry, config, tmp_path, monkeypatch
    ) -> None:
        """set / delete / verify / invalidate が ("credentials", <backend>) を emit する。

        AuthState は status イベントが出ない経路(store / config 直)で変わるため、
        UI の再計算はこのイベントが頼り。
        """
        ctrl, _, _ = self._setup_with_cloud_backend(
            populated_registry, config, tmp_path, monkeypatch
        )
        events: list[tuple[str, ...]] = []
        ctrl.add_settings_listener(lambda keys: events.append(keys))

        ctrl.set_credential("fake_cloud_asr", "api_key", "sk-1")
        ctrl.verify_and_save_credentials(
            LayerKind.ASR, "fake_cloud_asr", {"api_key": "sk-1"}
        )
        ctrl.invalidate_verification("fake_cloud_asr")
        ctrl.delete_credential("fake_cloud_asr", "api_key")

        cred_events = [k for k in events if k[0] == "credentials"]
        assert cred_events == [("credentials", "fake_cloud_asr")] * 4

    def test_verify_failure_does_not_emit_credentials_event(
        self, populated_registry, config, tmp_path, monkeypatch
    ) -> None:
        ctrl, cls, _ = self._setup_with_cloud_backend(
            populated_registry, config, tmp_path, monkeypatch
        )
        cls.ok_value = False
        events: list[tuple[str, ...]] = []
        ctrl.add_settings_listener(lambda keys: events.append(keys))
        ctrl.verify_and_save_credentials(
            LayerKind.ASR, "fake_cloud_asr", {"api_key": "bad"}
        )
        assert [k for k in events if k[0] == "credentials"] == []

    def test_verify_success_evicts_loaded_instance(
        self, populated_registry, config, tmp_path, monkeypatch
    ) -> None:
        """認証成功時、選択中レイヤのロード済みインスタンスは evict + INIT に戻る。

        旧挙動(MISSING_CREDENTIALS のときだけ即 reload)は lazy 方針に置き換え:
        古い認証情報で作られたインスタンスを捨て、次の Start / ↻ ロードで作り直す。
        """
        ctrl, _, _ = self._setup_with_cloud_backend(
            populated_registry, config, tmp_path, monkeypatch
        )
        ctrl.set_setting("backends", "asr", "fake_cloud_asr")
        ctrl.load_model_layer(LayerKind.ASR)
        assert LayerKind.ASR in ctrl._backends

        ctrl.verify_and_save_credentials(
            LayerKind.ASR, "fake_cloud_asr", {"api_key": "sk-new"}
        )
        assert LayerKind.ASR not in ctrl._backends
        assert ctrl.get_model_status(LayerKind.ASR) == ModelStatus.INIT

    def test_verify_for_unselected_backend_does_not_evict(
        self, populated_registry, config, tmp_path, monkeypatch
    ) -> None:
        """選択されていない backend の認証ではキャッシュに触らない。"""
        ctrl, _, _ = self._setup_with_cloud_backend(
            populated_registry, config, tmp_path, monkeypatch
        )
        # ASR は既定(faster_whisper)のままロード
        ctrl.load_model_layer(LayerKind.ASR)
        inst = ctrl._backends[LayerKind.ASR]
        ctrl.verify_and_save_credentials(
            LayerKind.ASR, "fake_cloud_asr", {"api_key": "sk-x"}
        )
        assert ctrl._backends[LayerKind.ASR] is inst


class TestAsrSupportedLanguages:
    """ASR の対応言語問い合わせ口(`get_supported_input_languages` / `supports_auto_detect`)。

    registry 経由で backend_cls からクラスメソッドを呼ぶ。未登録 / 例外は防御で空 / False。
    """

    def test_returns_languages_from_registered_backend_class(self, config) -> None:
        from voice_translator.common.backend_registry import BackendRegistry

        class FakeAsrCls:
            @classmethod
            def supported_input_languages(cls) -> list[str]:
                return ["en", "ja", "fr"]

            @classmethod
            def supports_auto_detect(cls) -> bool:
                return True

        reg = BackendRegistry()
        reg.register(
            LayerKind.ASR, "fake_asr",
            lambda: MagicMock(), backend_cls=FakeAsrCls,
        )
        ctrl = AppController(registry=reg, config=config)
        assert ctrl.get_supported_input_languages("fake_asr") == ["en", "ja", "fr"]
        assert ctrl.supports_auto_detect("fake_asr") is True

    def test_unregistered_backend_returns_empty(
        self, populated_registry, config
    ) -> None:
        ctrl = AppController(registry=populated_registry, config=config)
        assert ctrl.get_supported_input_languages("unknown") == []
        assert ctrl.supports_auto_detect("unknown") is False

    def test_backend_class_not_provided_returns_empty(
        self, populated_registry, config
    ) -> None:
        """populated_registry は backend_cls を渡さず register しているので
        get_backend_class が None を返し、防御で空リストになる。"""
        ctrl = AppController(registry=populated_registry, config=config)
        assert ctrl.get_supported_input_languages("faster_whisper") == []
        assert ctrl.supports_auto_detect("faster_whisper") is False

    def test_exception_in_class_method_swallowed(self, config) -> None:
        from voice_translator.common.backend_registry import BackendRegistry

        class BrokenAsrCls:
            @classmethod
            def supported_input_languages(cls) -> list[str]:
                raise RuntimeError("boom")

            @classmethod
            def supports_auto_detect(cls) -> bool:
                raise RuntimeError("boom")

        reg = BackendRegistry()
        reg.register(
            LayerKind.ASR, "broken",
            lambda: MagicMock(), backend_cls=BrokenAsrCls,
        )
        ctrl = AppController(registry=reg, config=config)
        # 例外は飲んで防御値
        assert ctrl.get_supported_input_languages("broken") == []
        assert ctrl.supports_auto_detect("broken") is False


class TestTranslatorSupportedLanguages:
    """Translator の対応出力言語の問い合わせ口。"""

    def test_returns_languages_from_registered_backend_class(self, config) -> None:
        from voice_translator.common.backend_registry import BackendRegistry

        class FakeTrans:
            @classmethod
            def supported_target_languages(cls) -> list[str]:
                return ["en", "ja", "fr"]

        reg = BackendRegistry()
        reg.register(
            LayerKind.TRANSLATOR, "fake_trans",
            lambda: MagicMock(), backend_cls=FakeTrans,
        )
        ctrl = AppController(registry=reg, config=config)
        assert ctrl.get_supported_target_languages("fake_trans") == ["en", "ja", "fr"]

    def test_unregistered_returns_empty(self, populated_registry, config) -> None:
        ctrl = AppController(registry=populated_registry, config=config)
        assert ctrl.get_supported_target_languages("unknown") == []

    def test_backend_class_not_provided_returns_empty(
        self, populated_registry, config
    ) -> None:
        """populated_registry の nllb200 は backend_cls を渡さず登録 → 空。"""
        ctrl = AppController(registry=populated_registry, config=config)
        assert ctrl.get_supported_target_languages("nllb200") == []


class TestTtsSupportedOutputLanguages:
    """TTS の対応読み上げ言語の問い合わせ口。"""

    def test_returns_languages_from_registered_backend_class(self, config) -> None:
        from voice_translator.common.backend_registry import BackendRegistry

        class FakeTts:
            @classmethod
            def supported_output_languages(cls) -> list[str]:
                return ["en", "ja"]

        reg = BackendRegistry()
        reg.register(
            LayerKind.TTS, "fake_tts",
            lambda: MagicMock(), backend_cls=FakeTts,
        )
        ctrl = AppController(registry=reg, config=config)
        assert ctrl.get_supported_output_languages("fake_tts") == ["en", "ja"]

    def test_unregistered_returns_empty(self, populated_registry, config) -> None:
        ctrl = AppController(registry=populated_registry, config=config)
        assert ctrl.get_supported_output_languages("unknown") == []

    def test_backend_class_not_provided_returns_empty(
        self, populated_registry, config
    ) -> None:
        """populated_registry の sapi は backend_cls を渡さず登録 → 空。"""
        ctrl = AppController(registry=populated_registry, config=config)
        assert ctrl.get_supported_output_languages("sapi") == []

    def test_exception_returns_empty(self, config) -> None:
        """`supported_output_languages` が例外を吐いても飲んで空を返す。"""
        from voice_translator.common.backend_registry import BackendRegistry

        class BrokenTts:
            @classmethod
            def supported_output_languages(cls) -> list[str]:
                raise RuntimeError("boom")

        reg = BackendRegistry()
        reg.register(
            LayerKind.TTS, "broken_tts",
            lambda: MagicMock(), backend_cls=BrokenTts,
        )
        ctrl = AppController(registry=reg, config=config)
        assert ctrl.get_supported_output_languages("broken_tts") == []


class TestPhaseDCapabilityHint:
    """Phase D: BackendRegistry の capability hint。"""

    def test_capability_hint_registered_returns_it(self) -> None:
        from voice_translator.common.backend_registry import BackendRegistry
        from voice_translator.common.types import BackendCapabilities

        reg = BackendRegistry()
        cap = BackendCapabilities(
            is_cloud=True, requires_credentials=True,
            service_name="OpenAI", terms_url="https://example.com/terms",
        )
        reg.register(LayerKind.ASR, "cloud_asr", lambda: None, capabilities=cap)
        got = reg.get_capability_hint(LayerKind.ASR, "cloud_asr")
        assert got is not None
        assert got.is_cloud is True
        assert got.service_name == "OpenAI"

    def test_capability_hint_returns_none_when_not_registered(
        self, populated_registry, config
    ) -> None:
        ctrl = AppController(registry=populated_registry, config=config)
        # 既存の mock backend は capability hint 無し
        hint = ctrl.get_backend_capability_hint(LayerKind.ASR, "faster_whisper")
        assert hint is None


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


class TestP2EventListeners:
    """P2: 全 UI 通知が add_<event>_listener(Subscription)で購読できる。"""

    def test_text_ready_listener_receives_record(
        self, populated_registry, config
    ) -> None:
        ctrl = AppController(registry=populated_registry, config=config)
        seen: list[dict] = []
        ctrl.add_text_ready_listener(lambda r: seen.append(r))
        record = _sample_record(seq_id=7)
        ctrl._handle_text_ready(record)
        assert seen == [record]

    def test_fatal_and_warn_listeners_receive_context(
        self, populated_registry, config
    ) -> None:
        ctrl = AppController(registry=populated_registry, config=config)
        fatals: list[tuple] = []
        warns: list[tuple] = []
        ctrl.add_fatal_listener(
            lambda m, **kw: fatals.append((m, kw.get("stage"), kw.get("seq_id")))
        )
        ctrl.add_warn_listener(lambda m, **kw: warns.append((m, kw.get("stage"))))
        # ErrorHandler 注入口(_emit_fatal / _emit_warn)経由で発火
        ctrl._emit_fatal("boom", exc=None, stage="ASR", seq_id=3, suppressed=0)
        ctrl._emit_warn("careful", exc=None, stage="TTS", seq_id=None, suppressed=0)
        assert fatals == [("boom", "ASR", 3)]
        assert warns == [("careful", "TTS")]

    def test_settings_listener_receives_keys_without_value(
        self, populated_registry, config
    ) -> None:
        ctrl = AppController(registry=populated_registry, config=config)
        seen: list[tuple[str, ...]] = []
        ctrl.add_settings_listener(lambda keys: seen.append(keys))
        ctrl.set_setting("languages", "src", "en")
        assert ("languages", "src") in seen

    def test_event_listener_unsubscribe(self, populated_registry, config) -> None:
        ctrl = AppController(registry=populated_registry, config=config)
        seen: list[dict] = []
        sub = ctrl.add_text_ready_listener(lambda r: seen.append(r))
        sub.unsubscribe()
        ctrl._handle_text_ready(_sample_record(seq_id=1))
        assert seen == []

    def test_event_listener_exception_does_not_break_others(
        self, populated_registry, config
    ) -> None:
        ctrl = AppController(registry=populated_registry, config=config)
        seen: list[dict] = []

        def bad(_r):
            raise RuntimeError("listener bug")

        ctrl.add_text_ready_listener(bad)
        ctrl.add_text_ready_listener(lambda r: seen.append(r))
        ctrl._handle_text_ready(_sample_record(seq_id=2))
        assert len(seen) == 1

    def test_listeners_of_different_events_are_isolated(
        self, populated_registry, config
    ) -> None:
        """status listener に text_ready が紛れ込まない(イベント種の分離)。"""
        ctrl = AppController(registry=populated_registry, config=config)
        status_seen: list = []
        ctrl.add_status_listener(lambda l, s: status_seen.append((l, s)))
        ctrl._handle_text_ready(_sample_record(seq_id=1))
        assert status_seen == []


class TestP2DeviceRestartReactive:
    """P2: 動作中の devices.* 変更が set_setting 反応系で自動 restart になる。

    restart_pipeline_async 自体の挙動(stop→start 順序 / 失敗 / 多重防御)は
    tests/test_dynamic_devices.py 側で検証済みのため、ここでは
    「set_setting がライフサイクルイベントを正しく流すか」に絞る。
    """

    @staticmethod
    def _running_ctrl(populated_registry, config):
        ctrl = AppController(registry=populated_registry, config=config)
        ctrl._coord = MagicMock(is_running=True)  # is_running を True にする
        return ctrl

    def test_input_change_while_running_emits_started_then_completed(
        self, populated_registry, config
    ) -> None:
        ctrl = self._running_ctrl(populated_registry, config)
        ctrl.restart_pipeline_async = MagicMock(
            side_effect=lambda *, on_restarted=None, on_failed=None: on_restarted()
        )
        events: list = []
        ctrl.add_restart_listener(lambda e: events.append(e))

        ctrl.set_setting("devices", "input", "mic_b")

        assert [e.phase for e in events] == ["started", "completed"]
        assert all(e.device_key == "input" for e in events)
        ctrl.restart_pipeline_async.assert_called_once()

    def test_output_change_while_running_emits_with_output_key(
        self, populated_registry, config
    ) -> None:
        ctrl = self._running_ctrl(populated_registry, config)
        ctrl.restart_pipeline_async = MagicMock(
            side_effect=lambda *, on_restarted=None, on_failed=None: on_restarted()
        )
        events: list = []
        ctrl.add_restart_listener(lambda e: events.append(e))

        ctrl.set_setting("devices", "output", "hp2")

        assert events[0].phase == "started"
        assert events[0].device_key == "output"

    def test_restart_failure_emits_failed_with_message(
        self, populated_registry, config
    ) -> None:
        ctrl = self._running_ctrl(populated_registry, config)
        ctrl.restart_pipeline_async = MagicMock(
            side_effect=lambda *, on_restarted=None, on_failed=None: on_failed(
                "再開に失敗: boom"
            )
        )
        events: list = []
        ctrl.add_restart_listener(lambda e: events.append(e))

        ctrl.set_setting("devices", "input", "mic_b")

        assert [e.phase for e in events] == ["started", "failed"]
        assert "boom" in events[-1].message

    def test_no_restart_when_not_running(self, populated_registry, config) -> None:
        ctrl = AppController(registry=populated_registry, config=config)
        ctrl.restart_pipeline_async = MagicMock()
        events: list = []
        ctrl.add_restart_listener(lambda e: events.append(e))

        ctrl.set_setting("devices", "input", "mic_b")

        assert events == []
        ctrl.restart_pipeline_async.assert_not_called()

    def test_non_device_keys_do_not_restart(self, populated_registry, config) -> None:
        ctrl = self._running_ctrl(populated_registry, config)
        ctrl.restart_pipeline_async = MagicMock()
        ctrl.set_setting("ui", "collapsed", "backends", True)
        ctrl.restart_pipeline_async.assert_not_called()


class TestP3CatalogAndCredentialsProperties:
    """P3: メタ問合せ / 認証の実体が catalog / credentials プロパティで公開される。

    既存の同名メソッドは互換窓(1 行委譲)であり、本クラス以外の既存テストが
    無修正で通ること自体が委譲の正しさの検証になっている。
    """

    def test_properties_expose_real_instances(
        self, populated_registry, config
    ) -> None:
        from voice_translator.common.backend_catalog import BackendCatalog
        from voice_translator.common.credentials_service import CredentialsService

        ctrl = AppController(registry=populated_registry, config=config)
        assert isinstance(ctrl.catalog, BackendCatalog)
        assert isinstance(ctrl.credentials, CredentialsService)

    def test_delegates_hit_same_instances(self, populated_registry, config) -> None:
        """互換窓が catalog / credentials と同じ実体に届く(別状態を持たない)。"""
        from tests._fixtures import InMemoryKeyring
        import keyring
        keyring.set_keyring(InMemoryKeyring())

        ctrl = AppController(registry=populated_registry, config=config)
        ctrl.set_credential("a", "k", "v")  # 互換窓で書く
        assert ctrl.credentials.get("a", "k") == "v"  # 実体から読める
        assert ctrl.catalog.get_capture_kind("soundcard") == ctrl.get_capture_kind(
            "soundcard"
        )
