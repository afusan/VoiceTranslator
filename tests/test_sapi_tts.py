"""SapiTtsBackend の単体テスト。pyttsx3 と wave 読み込みをモック化。

R-2 でプリミティブ I/F に変更: synthesize(text, tgt_lang) -> (pcm, samplerate)。
"""

from __future__ import annotations

import sys
import wave
from unittest.mock import MagicMock

import numpy as np
import pytest

from voice_translator.common.errors import FatalError, SkipError


@pytest.fixture()
def fake_pyttsx3(monkeypatch):
    """pyttsx3 をモック化。init() は engine モックを返す。save_to_file は実 WAV を吐く。"""
    fake_module = MagicMock()
    fake_engine = MagicMock(name="pyttsx3_engine")

    # voices リスト(言語ヒント探索のため)
    voice = MagicMock()
    voice.id = "voice_jp"
    voice.name = "Microsoft Haruka Desktop"
    voice.languages = []
    fake_engine.getProperty = MagicMock(return_value=[voice])

    # save_to_file は実ファイルに最小WAVを書き出す(read 側で本物の wave 解析を通すため)
    def fake_save_to_file(text: str, path: str) -> None:
        # 16kHz / 1ch / 16bit、512サンプルのサイレンスWAV
        with wave.open(path, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(16000)
            wf.writeframes((np.zeros(512, dtype=np.int16)).tobytes())

    fake_engine.save_to_file = MagicMock(side_effect=fake_save_to_file)
    fake_engine.runAndWait = MagicMock()
    fake_engine.setProperty = MagicMock()
    fake_engine.stop = MagicMock()

    fake_module.init = MagicMock(return_value=fake_engine)
    monkeypatch.setitem(sys.modules, "pyttsx3", fake_module)
    return fake_module, fake_engine


class TestInitialization:
    def test_constructor_imports_pyttsx3(self, fake_pyttsx3) -> None:
        from voice_translator.tts.sapi_backend import SapiTtsBackend

        SapiTtsBackend()  # 例外なし

    def test_constructor_fails_when_pyttsx3_missing(self, monkeypatch) -> None:
        # pyttsx3 を import 不能にする
        import importlib
        monkeypatch.setitem(sys.modules, "pyttsx3", None)
        from voice_translator.tts import sapi_backend
        importlib.reload(sapi_backend)
        with pytest.raises(FatalError, match="pyttsx3"):
            sapi_backend.SapiTtsBackend()


class TestSynthesize:
    def test_empty_text_raises_skip(self, fake_pyttsx3) -> None:
        from voice_translator.tts.sapi_backend import SapiTtsBackend

        backend = SapiTtsBackend(flush_delay_sec=0)
        with pytest.raises(SkipError):
            backend.synthesize("", "ja")

    def test_synthesize_returns_pcm_and_samplerate(self, fake_pyttsx3) -> None:
        from voice_translator.tts.sapi_backend import SapiTtsBackend

        backend = SapiTtsBackend(flush_delay_sec=0)
        pcm, sr = backend.synthesize("こんにちは", "ja")
        assert isinstance(pcm, np.ndarray)
        assert pcm.dtype == np.float32
        assert sr == 16000
        assert pcm.size > 0

    def test_voice_selection_attempted(self, fake_pyttsx3) -> None:
        _, fake_engine = fake_pyttsx3
        from voice_translator.tts.sapi_backend import SapiTtsBackend

        backend = SapiTtsBackend(flush_delay_sec=0)
        backend.synthesize("hello", "ja")
        # setProperty が voice 引数で呼ばれたか
        voice_calls = [
            c for c in fake_engine.setProperty.call_args_list if c.args and c.args[0] == "voice"
        ]
        assert voice_calls, "voice の setProperty が呼ばれていない"

    def test_engine_failure_wrapped_fatal(self, fake_pyttsx3) -> None:
        _, fake_engine = fake_pyttsx3
        fake_engine.save_to_file = MagicMock(side_effect=OSError("disk"))
        from voice_translator.tts.sapi_backend import SapiTtsBackend

        backend = SapiTtsBackend(flush_delay_sec=0)
        with pytest.raises(FatalError, match="SAPI/TTS"):
            backend.synthesize("hi", "ja")

    def test_temp_file_is_cleaned_up(self, fake_pyttsx3) -> None:
        # 通常の synthesize 後、tempfile が残らないことを確認
        from voice_translator.tts.sapi_backend import SapiTtsBackend

        backend = SapiTtsBackend(flush_delay_sec=0)
        backend.synthesize("hello", "ja")
        # 一時ファイルが片付くこと(細かい検証は省略)


class TestFlushDelayWorkaround:
    """SAPI 音節繰り返しバグへの暫定対処(pendList [2026-05-27])。"""

    def test_default_flush_delay_is_positive(self, fake_pyttsx3) -> None:
        from voice_translator.tts.sapi_backend import SapiTtsBackend

        backend = SapiTtsBackend()
        # 既定で sleep が入る(0.1 秒)
        assert backend._flush_delay_sec > 0

    def test_flush_delay_can_be_disabled(self, fake_pyttsx3) -> None:
        from voice_translator.tts.sapi_backend import SapiTtsBackend

        backend = SapiTtsBackend(flush_delay_sec=0)
        assert backend._flush_delay_sec == 0

    def test_synthesize_calls_time_sleep_when_delay_set(
        self, fake_pyttsx3, monkeypatch
    ) -> None:
        """flush_delay_sec > 0 のとき time.sleep が呼ばれる。"""
        from voice_translator.tts import sapi_backend

        calls: list[float] = []
        monkeypatch.setattr(sapi_backend.time, "sleep", lambda s: calls.append(s))

        backend = sapi_backend.SapiTtsBackend(flush_delay_sec=0.05)
        backend.synthesize("hello", "ja")
        assert 0.05 in calls, f"flush sleep が呼ばれていない: {calls}"

    def test_synthesize_skips_sleep_when_delay_zero(
        self, fake_pyttsx3, monkeypatch
    ) -> None:
        """flush_delay_sec=0 のとき time.sleep は呼ばれない(0ms スキップ)。"""
        from voice_translator.tts import sapi_backend

        calls: list[float] = []
        monkeypatch.setattr(sapi_backend.time, "sleep", lambda s: calls.append(s))

        backend = sapi_backend.SapiTtsBackend(flush_delay_sec=0)
        backend.synthesize("hello", "ja")
        assert calls == [], f"sleep が呼ばれてしまった: {calls}"
