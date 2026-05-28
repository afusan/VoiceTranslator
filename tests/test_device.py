"""device.py(デバイス解決ヘルパ)の単体テスト。

実 GPU の有無に依存しないよう、torch.cuda / torch.backends.mps を
monkeypatch でモックする。
"""

from __future__ import annotations

import sys
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from voice_translator.common.device import (
    resolve_ctranslate2_compute_type,
    resolve_ctranslate2_device,
    resolve_torch_device,
)


# ============================================================
# resolve_torch_device
# ============================================================
class TestResolveTorchDevice:
    def test_explicit_cpu_passthrough(self) -> None:
        assert resolve_torch_device("cpu") == "cpu"

    def test_explicit_cuda_passthrough(self) -> None:
        # 利用可否は問わずそのまま返す(明示指定は尊重)
        assert resolve_torch_device("cuda") == "cuda"

    def test_explicit_mps_passthrough(self) -> None:
        assert resolve_torch_device("mps") == "mps"

    def test_empty_preference_is_treated_as_auto(self) -> None:
        # 空 / None → "auto" 扱い
        # ※ "auto" 解決は torch のインストール状況に依存するため戻り値はチェックしない
        result = resolve_torch_device("")
        assert result in ("cuda", "mps", "cpu")

    def test_auto_picks_cuda_when_available(self, monkeypatch) -> None:
        fake_torch = MagicMock(name="torch")
        fake_torch.cuda.is_available = MagicMock(return_value=True)
        fake_torch.backends = SimpleNamespace(mps=MagicMock())
        fake_torch.backends.mps.is_available = MagicMock(return_value=False)
        monkeypatch.setitem(sys.modules, "torch", fake_torch)
        assert resolve_torch_device("auto") == "cuda"

    def test_auto_picks_mps_when_cuda_unavailable(self, monkeypatch) -> None:
        fake_torch = MagicMock(name="torch")
        fake_torch.cuda.is_available = MagicMock(return_value=False)
        fake_torch.backends = SimpleNamespace(mps=MagicMock())
        fake_torch.backends.mps.is_available = MagicMock(return_value=True)
        monkeypatch.setitem(sys.modules, "torch", fake_torch)
        assert resolve_torch_device("auto") == "mps"

    def test_auto_falls_back_to_cpu(self, monkeypatch) -> None:
        fake_torch = MagicMock(name="torch")
        fake_torch.cuda.is_available = MagicMock(return_value=False)
        fake_torch.backends = SimpleNamespace(mps=MagicMock())
        fake_torch.backends.mps.is_available = MagicMock(return_value=False)
        monkeypatch.setitem(sys.modules, "torch", fake_torch)
        assert resolve_torch_device("auto") == "cpu"

    def test_auto_returns_cpu_when_torch_not_importable(self, monkeypatch) -> None:
        # sys.modules["torch"] にダミーを置いて、属性アクセスで例外を出させる
        broken = MagicMock()
        broken.cuda.is_available = MagicMock(side_effect=RuntimeError("dead"))
        broken.backends = SimpleNamespace()  # mps 属性なし
        monkeypatch.setitem(sys.modules, "torch", broken)
        # cuda 判定で例外 → mps 属性なし → cpu fallback
        assert resolve_torch_device("auto") == "cpu"


# ============================================================
# resolve_ctranslate2_device
# ============================================================
class TestResolveCtranslate2Device:
    def test_auto_passes_through(self) -> None:
        # CTranslate2 自体が "auto" を解釈するのでそのまま渡す
        assert resolve_ctranslate2_device("auto") == "auto"

    def test_explicit_cuda(self) -> None:
        assert resolve_ctranslate2_device("cuda") == "cuda"

    def test_explicit_cpu(self) -> None:
        assert resolve_ctranslate2_device("cpu") == "cpu"

    def test_mps_falls_back_to_cpu(self) -> None:
        # CTranslate2 は MPS 未対応 → CPU に落とす
        assert resolve_ctranslate2_device("mps") == "cpu"

    def test_empty_treated_as_auto(self) -> None:
        assert resolve_ctranslate2_device("") == "auto"
        assert resolve_ctranslate2_device(None) == "auto"  # type: ignore[arg-type]


# ============================================================
# resolve_ctranslate2_compute_type
# ============================================================
class TestResolveComputeType:
    def test_explicit_value_passthrough(self) -> None:
        assert resolve_ctranslate2_compute_type("cuda", "float32") == "float32"
        assert resolve_ctranslate2_compute_type("cpu", "int8") == "int8"

    def test_auto_picks_float16_for_cuda(self) -> None:
        assert resolve_ctranslate2_compute_type("cuda", "auto") == "float16"

    def test_auto_picks_float16_for_auto_device(self) -> None:
        # device="auto" のときも GPU を期待した値(float16)
        assert resolve_ctranslate2_compute_type("auto", "auto") == "float16"

    def test_auto_picks_int8_for_cpu(self) -> None:
        assert resolve_ctranslate2_compute_type("cpu", "auto") == "int8"

    def test_empty_preference_treated_as_auto(self) -> None:
        assert resolve_ctranslate2_compute_type("cpu", "") == "int8"
