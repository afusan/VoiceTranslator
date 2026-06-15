"""FasterWhisperAsrBackend の単体テスト。faster-whisper を完全モック化。

R-2 でプリミティブ I/F に変更: transcribe(pcm, hint) -> (text, lang)。
"""

from __future__ import annotations

import sys
from unittest.mock import MagicMock

import numpy as np
import pytest

from voice_translator.common.errors import FatalError, SkipError


@pytest.fixture()
def fake_faster_whisper(monkeypatch):
    """faster_whisper.WhisperModel をモックに差し替える。"""
    fake_module = MagicMock()
    fake_model = MagicMock(name="whisper_model")

    # transcribe の戻り値: (segments_iter, info)
    fake_segment = MagicMock()
    fake_segment.text = "  hello world  "
    fake_info = MagicMock()
    fake_info.language = "en"

    fake_model.transcribe = MagicMock(return_value=(iter([fake_segment]), fake_info))
    fake_module.WhisperModel = MagicMock(return_value=fake_model)

    monkeypatch.setitem(sys.modules, "faster_whisper", fake_module)
    return fake_module, fake_model


class TestInitialization:
    def test_calls_whisper_model_with_size(self, fake_faster_whisper) -> None:
        fake_module, _ = fake_faster_whisper
        from voice_translator.asr.faster_whisper_backend import (
            FasterWhisperAsrBackend,
        )

        FasterWhisperAsrBackend(model_size="tiny", device="cpu", compute_type="int8")

        fake_module.WhisperModel.assert_called_once_with(
            "tiny", device="cpu", compute_type="int8"
        )

    def test_init_failure_raises_fatal(self, monkeypatch) -> None:
        fake_module = MagicMock()
        fake_module.WhisperModel = MagicMock(side_effect=OSError("no model"))
        monkeypatch.setitem(sys.modules, "faster_whisper", fake_module)
        from voice_translator.asr.faster_whisper_backend import (
            FasterWhisperAsrBackend,
        )

        with pytest.raises(FatalError, match="初期化に失敗"):
            FasterWhisperAsrBackend()


class TestTranscribe:
    def test_empty_pcm_raises_skip(self, fake_faster_whisper) -> None:
        from voice_translator.asr.faster_whisper_backend import (
            FasterWhisperAsrBackend,
        )

        backend = FasterWhisperAsrBackend()
        with pytest.raises(SkipError):
            backend.transcribe(np.zeros(0, dtype=np.float32))

    def test_none_pcm_raises_skip(self, fake_faster_whisper) -> None:
        from voice_translator.asr.faster_whisper_backend import (
            FasterWhisperAsrBackend,
        )

        backend = FasterWhisperAsrBackend()
        with pytest.raises(SkipError):
            backend.transcribe(None)

    def test_transcribe_returns_text_and_lang(self, fake_faster_whisper) -> None:
        from voice_translator.asr.faster_whisper_backend import (
            FasterWhisperAsrBackend,
        )

        backend = FasterWhisperAsrBackend()
        text, lang = backend.transcribe(np.ones(16000, dtype=np.float32), "auto")
        assert text == "hello world"
        # Whisper の検出言語(639-1 "en")は正準 639-3 へ持ち上げて返す
        assert lang == "eng"

    def test_explicit_lang_passed_to_model(self, fake_faster_whisper) -> None:
        _, fake_model = fake_faster_whisper
        from voice_translator.asr.faster_whisper_backend import (
            FasterWhisperAsrBackend,
        )

        backend = FasterWhisperAsrBackend()
        # ヒントは正準 639-3。Whisper には 639-1 に落として渡す。
        text, lang = backend.transcribe(np.ones(160, dtype=np.float32), "eng")
        kwargs = fake_model.transcribe.call_args.kwargs
        assert kwargs["language"] == "en"
        assert kwargs["task"] == "transcribe"
        # 明示指定があれば検出結果ではなく指定(正準)を返す
        assert lang == "eng"

    def test_auto_lang_passes_none(self, fake_faster_whisper) -> None:
        _, fake_model = fake_faster_whisper
        from voice_translator.asr.faster_whisper_backend import (
            FasterWhisperAsrBackend,
        )

        backend = FasterWhisperAsrBackend()
        backend.transcribe(np.ones(160, dtype=np.float32), "auto")
        assert fake_model.transcribe.call_args.kwargs["language"] is None

    def test_inference_exception_wrapped_fatal(self, fake_faster_whisper) -> None:
        _, fake_model = fake_faster_whisper
        fake_model.transcribe = MagicMock(side_effect=RuntimeError("oom"))
        from voice_translator.asr.faster_whisper_backend import (
            FasterWhisperAsrBackend,
        )

        backend = FasterWhisperAsrBackend()
        with pytest.raises(FatalError, match="推論失敗"):
            backend.transcribe(np.ones(160, dtype=np.float32))


class TestDeviceSelection:
    """device 引数の振る舞い: auto / 明示 / フォールバック。"""

    def test_default_auto_resolves_to_cpu_without_cuda(
        self, fake_faster_whisper, monkeypatch
    ) -> None:
        """auto + GPU 無し環境 → cpu+int8 に解決される。"""
        fake_torch = MagicMock(name="torch")
        fake_torch.cuda.is_available = MagicMock(return_value=False)
        monkeypatch.setitem(sys.modules, "torch", fake_torch)

        fake_module, _ = fake_faster_whisper
        from voice_translator.asr.faster_whisper_backend import (
            FasterWhisperAsrBackend,
        )

        backend = FasterWhisperAsrBackend()
        assert backend.device == "cpu"
        assert backend.compute_type == "int8"
        fake_module.WhisperModel.assert_called_with(
            "small", device="cpu", compute_type="int8"
        )

    def test_default_auto_resolves_to_cuda_when_available(
        self, fake_faster_whisper, monkeypatch
    ) -> None:
        """auto + CUDA 有り環境 → cuda+int8_float16 に解決される。"""
        fake_torch = MagicMock(name="torch")
        fake_torch.cuda.is_available = MagicMock(return_value=True)
        monkeypatch.setitem(sys.modules, "torch", fake_torch)

        fake_module, _ = fake_faster_whisper
        from voice_translator.asr.faster_whisper_backend import (
            FasterWhisperAsrBackend,
        )

        backend = FasterWhisperAsrBackend()
        assert backend.device == "cuda"
        assert backend.compute_type == "int8_float16"
        fake_module.WhisperModel.assert_called_with(
            "small", device="cuda", compute_type="int8_float16"
        )

    def test_explicit_cpu_picks_int8(self, fake_faster_whisper) -> None:
        fake_module, _ = fake_faster_whisper
        from voice_translator.asr.faster_whisper_backend import (
            FasterWhisperAsrBackend,
        )

        backend = FasterWhisperAsrBackend(device="cpu", compute_type="auto")
        assert backend.device == "cpu"
        assert backend.compute_type == "int8"
        fake_module.WhisperModel.assert_called_with(
            "small", device="cpu", compute_type="int8"
        )

    def test_mps_falls_back_to_cpu(self, fake_faster_whisper) -> None:
        """CTranslate2 は MPS 未対応 → CPU に落ちる(Apple Silicon ユーザの保険)。"""
        fake_module, _ = fake_faster_whisper
        from voice_translator.asr.faster_whisper_backend import (
            FasterWhisperAsrBackend,
        )

        backend = FasterWhisperAsrBackend(device="mps")
        assert backend.device == "cpu"

    def test_gpu_init_failure_retries_on_cpu(self, monkeypatch) -> None:
        """device=cuda で WhisperModel 初期化が失敗したら CPU で再試行する。"""
        fake_module = MagicMock()
        fake_model = MagicMock(name="cpu_model")
        # 1 回目(cuda)は失敗、2 回目(cpu)は成功
        call_log: list[dict] = []

        def whisper_factory(*args, **kwargs):
            call_log.append(kwargs)
            if kwargs.get("device") == "cuda":
                raise RuntimeError("CUDA not available")
            return fake_model

        fake_module.WhisperModel = MagicMock(side_effect=whisper_factory)
        monkeypatch.setitem(sys.modules, "faster_whisper", fake_module)

        from voice_translator.asr.faster_whisper_backend import (
            FasterWhisperAsrBackend,
        )

        backend = FasterWhisperAsrBackend(device="cuda")
        assert backend.device == "cpu"
        assert backend.compute_type == "int8"
        # 2 回呼ばれている(cuda → cpu)
        assert len(call_log) == 2
        assert call_log[0]["device"] == "cuda"
        assert call_log[1]["device"] == "cpu"


class TestSupportedInputLanguages:
    """対応言語 I/F(クラスメソッド、未ロードでも問い合わせ可能)。"""

    def test_returns_whisper_languages(self) -> None:
        from voice_translator.asr.faster_whisper_backend import (
            FasterWhisperAsrBackend,
        )

        langs = FasterWhisperAsrBackend.supported_input_languages()
        # Whisper 99 言語の代表をいくつか含む(申告は正準 639-3)
        assert "eng" in langs
        assert "jpn" in langs
        assert "zho" in langs
        # "auto" はリストに含めない(supports_auto_detect で別途宣言)
        assert "auto" not in langs

    def test_supports_auto_detect(self) -> None:
        from voice_translator.asr.faster_whisper_backend import (
            FasterWhisperAsrBackend,
        )

        assert FasterWhisperAsrBackend.supports_auto_detect() is True

    def test_all_codes_known_in_language_table(self) -> None:
        """faster-whisper の返すコードは全て共通言語テーブルに存在する。"""
        from voice_translator.asr.faster_whisper_backend import (
            FasterWhisperAsrBackend,
        )
        from voice_translator.common.languages import LANGUAGE_NAMES

        langs = FasterWhisperAsrBackend.supported_input_languages()
        unknown = [c for c in langs if c not in LANGUAGE_NAMES]
        assert not unknown, f"共通言語テーブルに未登録のコード: {unknown}"

    def test_no_load_required(self, monkeypatch) -> None:
        """faster_whisper モジュール未インストール環境でもクラスメソッドは呼べる。"""
        # sys.modules から faster_whisper を消す(インストール済み環境用)
        monkeypatch.delitem(sys.modules, "faster_whisper", raising=False)
        from voice_translator.asr.faster_whisper_backend import (
            FasterWhisperAsrBackend,
        )

        # 例外なく呼べること
        langs = FasterWhisperAsrBackend.supported_input_languages()
        assert len(langs) > 50  # 99 言語のはず
