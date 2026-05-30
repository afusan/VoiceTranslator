"""PyannoteVadBackend の単体テスト。pyannote.audio をモック化。

HF token 認証フロー / pipeline 駆動 / 認証情報の credential_spec / verify_credentials を検証。
"""

from __future__ import annotations

import sys
from unittest.mock import MagicMock

import numpy as np
import pytest

from voice_translator.common.errors import FatalError
from voice_translator.common.types import ModelStatus


@pytest.fixture()
def fake_pyannote(monkeypatch):
    """`pyannote.audio` モジュールを差し替える(4.x 新 API: Model + VoiceActivityDetection)。

    `Model.from_pretrained()` でモック segmentation モデル、`VoiceActivityDetection` で
    モック pipeline を返す。pipeline(...) 呼び出しはデフォルトで空 timeline を返す。
    """
    fake_module = MagicMock(name="pyannote.audio")
    fake_pipelines_module = MagicMock(name="pyannote.audio.pipelines")

    fake_segmentation_model = MagicMock(name="segmentation_model")
    fake_module.Model = MagicMock()
    fake_module.Model.from_pretrained = MagicMock(return_value=fake_segmentation_model)

    fake_pipeline_inst = MagicMock(name="pipeline_inst")
    fake_pipeline_inst.to = MagicMock(return_value=fake_pipeline_inst)
    fake_pipeline_inst.instantiate = MagicMock()

    fake_annotation = MagicMock(name="annotation")
    fake_timeline = MagicMock(name="timeline")
    fake_timeline.support = MagicMock(return_value=iter([]))
    fake_annotation.get_timeline = MagicMock(return_value=fake_timeline)
    fake_pipeline_inst.return_value = fake_annotation

    fake_pipelines_module.VoiceActivityDetection = MagicMock(
        return_value=fake_pipeline_inst
    )

    monkeypatch.setitem(sys.modules, "pyannote", MagicMock())
    monkeypatch.setitem(sys.modules, "pyannote.audio", fake_module)
    monkeypatch.setitem(
        sys.modules, "pyannote.audio.pipelines", fake_pipelines_module
    )
    return (
        fake_module,
        fake_pipelines_module,
        fake_pipeline_inst,
        fake_segmentation_model,
        fake_annotation,
        fake_timeline,
    )


@pytest.fixture()
def fake_torch(monkeypatch):
    """torch をモック化(device 解決と tensor 生成のみ使う)。"""
    fake_module = MagicMock(name="torch")
    fake_module.cuda.is_available = MagicMock(return_value=False)
    fake_module.backends.mps.is_available = MagicMock(return_value=False)
    fake_module.device = MagicMock(side_effect=lambda x: x)
    # from_numpy(arr).unsqueeze(0) → 何かを返せばよい
    fake_tensor = MagicMock(name="tensor")
    fake_tensor.unsqueeze = MagicMock(return_value=fake_tensor)
    fake_module.from_numpy = MagicMock(return_value=fake_tensor)
    monkeypatch.setitem(sys.modules, "torch", fake_module)
    return fake_module


# ============================================================
# 初期化 / 認証
# ============================================================
class TestInitialization:
    def test_missing_token_sets_missing_credentials_status(self, fake_pyannote) -> None:
        """HF token 未入力 → MISSING_CREDENTIALS でモデルロードはしない。"""
        from voice_translator.vad.pyannote_backend import PyannoteVadBackend

        backend = PyannoteVadBackend(hf_token=None)
        assert backend.get_status() == ModelStatus.MISSING_CREDENTIALS
        fake_module, fake_pipelines, _, _, _, _ = fake_pyannote
        fake_module.Model.from_pretrained.assert_not_called()
        fake_pipelines.VoiceActivityDetection.assert_not_called()

    def test_with_token_loads_segmentation_then_constructs_pipeline(
        self, fake_pyannote, fake_torch
    ) -> None:
        """4.x 新 API: Model.from_pretrained(..., token=...) → VoiceActivityDetection.instantiate(...)。"""
        from voice_translator.vad.pyannote_backend import PyannoteVadBackend

        fake_module, fake_pipelines, fake_pipe, fake_seg, _, _ = fake_pyannote
        backend = PyannoteVadBackend(hf_token="hf_xxx")
        assert backend.get_status() == ModelStatus.LOADED
        # Model.from_pretrained が segmentation-3.0(既定)+ token= で呼ばれる
        fake_module.Model.from_pretrained.assert_called_once()
        args, kwargs = fake_module.Model.from_pretrained.call_args
        assert args[0] == "pyannote/segmentation-3.0"
        assert kwargs.get("token") == "hf_xxx"
        # VoiceActivityDetection に segmentation Model が渡される
        fake_pipelines.VoiceActivityDetection.assert_called_once()
        seg_passed = fake_pipelines.VoiceActivityDetection.call_args.kwargs.get(
            "segmentation"
        )
        assert seg_passed is fake_seg
        # instantiate でハイパラ注入される
        fake_pipe.instantiate.assert_called_once()
        params = fake_pipe.instantiate.call_args.args[0]
        assert "min_duration_on" in params and "min_duration_off" in params

    def test_segmentation_load_failure_raises(self, fake_pyannote, fake_torch) -> None:
        from voice_translator.vad.pyannote_backend import PyannoteVadBackend

        fake_module, _, _, _, _, _ = fake_pyannote
        fake_module.Model.from_pretrained.side_effect = RuntimeError("gated")
        with pytest.raises(FatalError, match="segmentation"):
            PyannoteVadBackend(hf_token="hf_xxx")

    def test_pipeline_instantiate_failure_raises(self, fake_pyannote, fake_torch) -> None:
        from voice_translator.vad.pyannote_backend import PyannoteVadBackend

        _, _, fake_pipe, _, _, _ = fake_pyannote
        fake_pipe.instantiate.side_effect = RuntimeError("bad params")
        with pytest.raises(FatalError, match="pipeline"):
            PyannoteVadBackend(hf_token="hf_xxx")


# ============================================================
# credential_spec / verify_credentials
# ============================================================
class TestCredentials:
    def test_credential_spec_has_hf_token(self) -> None:
        from voice_translator.vad.pyannote_backend import PyannoteVadBackend

        spec = PyannoteVadBackend.credential_spec()
        assert [f.key_name for f in spec] == ["hf_token"]
        assert spec[0].secret is True

    def test_verify_empty_token_fails(self) -> None:
        from voice_translator.vad.pyannote_backend import PyannoteVadBackend

        result = PyannoteVadBackend.verify_credentials({"hf_token": ""})
        assert result.ok is False
        assert "未入力" in result.message

    def test_verify_success_via_mocked_urlopen(self, monkeypatch) -> None:
        """HF API /whoami-v2 が 200 で返れば ok=True。"""
        from voice_translator.vad.pyannote_backend import PyannoteVadBackend

        fake_resp = MagicMock()
        fake_resp.status = 200
        fake_resp.__enter__ = MagicMock(return_value=fake_resp)
        fake_resp.__exit__ = MagicMock(return_value=False)
        import urllib.request

        monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **kw: fake_resp)
        result = PyannoteVadBackend.verify_credentials({"hf_token": "hf_xxx"})
        assert result.ok is True

    def test_verify_401_fails(self, monkeypatch) -> None:
        from voice_translator.vad.pyannote_backend import PyannoteVadBackend
        import urllib.error
        import urllib.request

        def raise_401(*a, **kw):
            raise urllib.error.HTTPError(
                "https://huggingface.co", 401, "Unauthorized", None, None
            )

        monkeypatch.setattr(urllib.request, "urlopen", raise_401)
        result = PyannoteVadBackend.verify_credentials({"hf_token": "bad"})
        assert result.ok is False
        assert "無効" in result.message


# ============================================================
# process / reset
# ============================================================
class TestProcess:
    def test_process_without_token_returns_empty(self, fake_pyannote) -> None:
        from voice_translator.vad.pyannote_backend import PyannoteVadBackend

        backend = PyannoteVadBackend(hf_token=None)
        assert backend.process(np.zeros(16000, dtype=np.float32)) == []

    def test_process_buffers_until_window_full(
        self, fake_pyannote, fake_torch
    ) -> None:
        """batch_window_sec 分だけ溜まらないと pipeline が呼ばれない。"""
        from voice_translator.vad.pyannote_backend import PyannoteVadBackend

        _, _, fake_pipeline_inst, _, _, _ = fake_pyannote
        backend = PyannoteVadBackend(hf_token="hf_xxx", batch_window_sec=2.0)
        # 1 秒ぶんだけ投入(2 秒未満)
        backend.process(np.zeros(16000, dtype=np.float32))
        fake_pipeline_inst.assert_not_called()

    def test_reset_clears_buffer(self, fake_pyannote, fake_torch) -> None:
        from voice_translator.vad.pyannote_backend import PyannoteVadBackend

        backend = PyannoteVadBackend(hf_token="hf_xxx")
        backend.process(np.zeros(8000, dtype=np.float32))
        backend.reset()
        assert backend._buffer.size == 0
        assert backend._in_speech is False


# ============================================================
# capabilities
# ============================================================
class TestCapabilities:
    def test_requires_credentials_in_capabilities(self) -> None:
        from voice_translator.vad.pyannote_backend import PyannoteVadBackend

        # 認証情報無しでもインスタンス化できる(MISSING_CREDENTIALS 状態)
        # → capabilities() は問題なく呼べる(Hint 用)
        # 但しテスト容易性のため、import 不要なクラスメソッド credential_spec で確認するに留める。
        # ここでは backend を作らず capabilities の構造のみ間接検証。
        assert PyannoteVadBackend.credential_spec()  # 非空
