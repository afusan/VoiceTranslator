"""動的言語変更(P2)のテスト。

- PipelineCoordinator.set_languages: 内部値の差し替え / None フィールド維持
- 次発話の RawPayload に新 src_lang_hint が乗る
- 次発話の Translator 呼び出しが新 tgt_lang を見る
- AppController.set_setting("languages", ...) で動作中の Coordinator に転送される
- 動作中でないときは Coordinator メソッドが呼ばれない
"""

from __future__ import annotations

from unittest.mock import MagicMock

import numpy as np
import pytest

from voice_translator.asr.backend import AsrBackend
from voice_translator.capture.backend import AudioCaptureBackend
from voice_translator.common.app_controller import AppController
from voice_translator.common.error_handler import ErrorHandler
from voice_translator.common.pipeline import PipelineCoordinator
from voice_translator.common.types import (
    CaptureSource,
    LayerKind,
    OutputDevice,
    PcmChunk,
)
from voice_translator.output.backend import AudioOutputBackend
from voice_translator.translator.backend import TranslatorBackend
from voice_translator.tts.backend import TtsBackend
from voice_translator.vad.backend import VadBackend, VadSegment


# ============================================================
# モック群(test_pipeline.py の縮約版。set_languages 観察に必要な最小)
# ============================================================
class _StubCapture(AudioCaptureBackend):
    def __init__(self) -> None:
        self._started = False

    def list_sources(self) -> list[CaptureSource]:
        return [CaptureSource("d", "Dummy")]

    def start(self, source_id: str) -> None:
        self._started = True

    def stop(self) -> None:
        self._started = False

    def read_chunk(self, timeout: float = 0.1) -> PcmChunk | None:
        return None


class _StubVad(VadBackend):
    def reset(self) -> None:
        pass

    def process(self, chunk: PcmChunk) -> list[VadSegment]:
        return []


class _RecordingAsr(AsrBackend):
    """transcribe 呼び出し時の hint を記録する。"""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def transcribe(self, pcm, src_lang_hint: str = "auto") -> tuple[str, str]:
        self.calls.append(src_lang_hint)
        return "hello", "en"

    @classmethod
    def supported_input_languages(cls) -> list[str]:
        return ["en"]

    @classmethod
    def supports_auto_detect(cls) -> bool:
        return True


class _RecordingTranslator(TranslatorBackend):
    """translate 呼び出し時の tgt_lang を記録する。"""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str]] = []

    def translate(self, src_text: str, src_lang: str, tgt_lang: str) -> str:
        self.calls.append((src_text, src_lang, tgt_lang))
        return f"{src_text}@{tgt_lang}"

    @classmethod
    def supported_target_languages(cls) -> list[str]:
        return ["en", "ja"]


class _StubTts(TtsBackend):
    def synthesize(self, text: str, tgt_lang: str) -> tuple:
        return np.zeros(16, dtype=np.float32), 16000

    @classmethod
    def supported_output_languages(cls) -> list[str]:
        return ["en", "ja"]


class _StubOutput(AudioOutputBackend):
    def list_devices(self) -> list[OutputDevice]:
        return [OutputDevice("o", "Out")]

    def start(self, device_id: str) -> None:
        pass

    def play(self, pcm, samplerate: int) -> None:
        pass

    def stop(self) -> None:
        pass


def _make_coord(*, src: str = "auto", tgt: str = "ja") -> PipelineCoordinator:
    """言語変更の観察用に最小構成の Coordinator を作る(スレッドは起動しない)。"""
    return PipelineCoordinator(
        capture=_StubCapture(),
        vad=_StubVad(),
        asr=_RecordingAsr(),
        translator=_RecordingTranslator(),
        tts=_StubTts(),
        output=_StubOutput(),
        error_handler=ErrorHandler(),
        src_lang=src,
        tgt_lang=tgt,
    )


# ============================================================
# PipelineCoordinator.set_languages
# ============================================================
class TestCoordinatorSetLanguages:
    def test_both_swap(self) -> None:
        coord = _make_coord(src="auto", tgt="ja")
        coord.set_languages(src="en", tgt="fr")
        assert coord._src_lang == "en"  # noqa: SLF001
        assert coord._tgt_lang == "fr"  # noqa: SLF001

    def test_none_keeps_field(self) -> None:
        coord = _make_coord(src="en", tgt="ja")
        coord.set_languages(src="fr")  # tgt は維持
        assert coord._src_lang == "fr"  # noqa: SLF001
        assert coord._tgt_lang == "ja"  # noqa: SLF001
        coord.set_languages(tgt="ko")  # src は維持
        assert coord._src_lang == "fr"  # noqa: SLF001
        assert coord._tgt_lang == "ko"  # noqa: SLF001

    def test_no_args_is_noop(self) -> None:
        coord = _make_coord(src="en", tgt="ja")
        coord.set_languages()
        assert coord._src_lang == "en"  # noqa: SLF001
        assert coord._tgt_lang == "ja"  # noqa: SLF001

    def test_coerces_to_string(self) -> None:
        """非 str を渡しても str に変換される(防衛)。"""
        coord = _make_coord()
        coord.set_languages(src=123, tgt=None)  # type: ignore[arg-type]
        assert coord._src_lang == "123"  # noqa: SLF001

    def test_next_payload_uses_new_src_lang(self) -> None:
        """set_languages 後に作る RawPayload に新 src_lang_hint が乗る。

        Coordinator の Input ループは self._src_lang を読んで RawPayload を作る。
        ここではループを起動せず、内部値の差し替えだけを確認する(統合テストは
        test_pipeline.py が担保)。
        """
        from voice_translator.common.messages import RawPayload

        coord = _make_coord(src="auto", tgt="ja")
        coord.set_languages(src="en")
        # _input_loop と同じく、payload 生成で self._src_lang を読むと新値になる
        payload = RawPayload(
            pcm=np.zeros(16, dtype=np.float32),
            src_lang_hint=coord._src_lang,  # noqa: SLF001
        )
        assert payload.src_lang_hint == "en"


# ============================================================
# AppController.set_setting → Coordinator 中継
# ============================================================
@pytest.fixture()
def stub_controller():
    """AppController を __init__ 経由せず、set_setting の言語中継を試せる shim。"""
    shim = MagicMock(spec=AppController)
    # 実メソッドを self に bind
    shim.set_setting = AppController.set_setting.__get__(shim)
    shim._config = MagicMock(name="config")
    shim._load_lock = MagicMock(name="load_lock")
    shim._logger = MagicMock(name="logger")
    shim._backends = {}
    shim._backend_subscriptions = {}
    shim._coord = None
    return shim


class TestAppControllerLanguageRelay:
    def test_running_coord_receives_src(self, stub_controller) -> None:
        coord = MagicMock(spec=PipelineCoordinator)
        coord.is_running = True
        stub_controller._coord = coord
        stub_controller.set_setting("languages", "src", "en")
        coord.set_languages.assert_called_once_with(src="en")

    def test_running_coord_receives_tgt(self, stub_controller) -> None:
        coord = MagicMock(spec=PipelineCoordinator)
        coord.is_running = True
        stub_controller._coord = coord
        stub_controller.set_setting("languages", "tgt", "fr")
        coord.set_languages.assert_called_once_with(tgt="fr")

    def test_no_coord_no_relay(self, stub_controller) -> None:
        """Coordinator が無い(未起動 / 停止済み)ときは中継しない。"""
        stub_controller._coord = None
        # 例外なく ConfigStore への保存だけ走る
        stub_controller.set_setting("languages", "tgt", "fr")
        # _config.set が呼ばれる
        stub_controller._config.set.assert_called_once_with("languages", "tgt", "fr")

    def test_stopped_coord_no_relay(self, stub_controller) -> None:
        """Coordinator はあるが動作中でないときは中継しない。"""
        coord = MagicMock(spec=PipelineCoordinator)
        coord.is_running = False
        stub_controller._coord = coord
        stub_controller.set_setting("languages", "src", "en")
        coord.set_languages.assert_not_called()

    def test_other_setting_does_not_call_coord(self, stub_controller) -> None:
        """languages 以外の set_setting で Coordinator に転送しない。"""
        coord = MagicMock(spec=PipelineCoordinator)
        coord.is_running = True
        stub_controller._coord = coord
        stub_controller.set_setting("log", "directory", "./logs")
        coord.set_languages.assert_not_called()

    def test_value_is_stringified(self, stub_controller) -> None:
        """非文字列の値も str に変換されて Coordinator に渡る。"""
        coord = MagicMock(spec=PipelineCoordinator)
        coord.is_running = True
        stub_controller._coord = coord
        stub_controller.set_setting("languages", "src", 42)
        coord.set_languages.assert_called_once_with(src="42")

    def test_unknown_languages_key_is_ignored(self, stub_controller) -> None:
        """languages.foo のような未知キーは set_languages を呼ばない(ConfigStore のみ更新)。"""
        coord = MagicMock(spec=PipelineCoordinator)
        coord.is_running = True
        stub_controller._coord = coord
        stub_controller.set_setting("languages", "foo", "bar")
        coord.set_languages.assert_not_called()
        stub_controller._config.set.assert_called_once_with("languages", "foo", "bar")
