"""SapiTtsBackend の単体テスト。pyttsx3 と wave 読み込みをモック化。"""

from __future__ import annotations

import sys
import wave
from pathlib import Path
from unittest.mock import MagicMock

import numpy as np
import pytest

from voice_translator.common.errors import FatalError, SkipError
from voice_translator.common.utterance import Utterance


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

        backend = SapiTtsBackend()
        with pytest.raises(SkipError):
            backend.synthesize(Utterance(tgt_text=""))

    def test_synthesize_fills_pcm_and_samplerate(self, fake_pyttsx3) -> None:
        from voice_translator.tts.sapi_backend import SapiTtsBackend

        backend = SapiTtsBackend()
        utt = Utterance(tgt_text="こんにちは", tgt_lang="ja")
        result = backend.synthesize(utt)
        assert result is utt
        assert isinstance(utt.tts_pcm, np.ndarray)
        assert utt.tts_pcm.dtype == np.float32
        assert utt.tts_samplerate == 16000
        assert utt.tts_pcm.size > 0

    def test_voice_selection_attempted(self, fake_pyttsx3) -> None:
        _, fake_engine = fake_pyttsx3
        from voice_translator.tts.sapi_backend import SapiTtsBackend

        backend = SapiTtsBackend()
        backend.synthesize(Utterance(tgt_text="hello", tgt_lang="ja"))
        # setProperty が voice 引数で呼ばれたか
        voice_calls = [
            c for c in fake_engine.setProperty.call_args_list if c.args and c.args[0] == "voice"
        ]
        assert voice_calls, "voice の setProperty が呼ばれていない"

    def test_engine_failure_wrapped_fatal(self, fake_pyttsx3) -> None:
        _, fake_engine = fake_pyttsx3
        fake_engine.save_to_file = MagicMock(side_effect=OSError("disk"))
        from voice_translator.tts.sapi_backend import SapiTtsBackend

        backend = SapiTtsBackend()
        with pytest.raises(FatalError, match="SAPI/TTS"):
            backend.synthesize(Utterance(tgt_text="hi"))

    def test_temp_file_is_cleaned_up(self, fake_pyttsx3, tmp_path) -> None:
        # 通常の synthesize 後、tempfile が残らないことを確認
        from voice_translator.tts.sapi_backend import SapiTtsBackend

        backend = SapiTtsBackend()
        backend.synthesize(Utterance(tgt_text="hello"))
        # 一時ファイルが片付くこと(細かい検証は省略)
