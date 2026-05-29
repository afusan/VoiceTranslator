"""hw_info の単体テスト。

実機検出は環境依存なので、`detect_hw` 自体は呼び出して落ちないことだけ確認。
判定ロジック(`assess_model_fit`)は HwInfo を固定値で渡して網羅する。
"""

from __future__ import annotations

from voice_translator.common.hw_info import (
    HwInfo,
    ModelFit,
    assess_model_fit,
    detect_hw,
)


class TestDetectHw:
    def test_detect_returns_hw_info(self) -> None:
        """検出ライブラリが無い環境でも例外を投げず HwInfo を返す。"""
        info = detect_hw()
        assert isinstance(info, HwInfo)
        # 値は環境依存なので存在検証だけ
        assert info.has_gpu in (True, False)
        # ram_gb / vram_gb は None または正数
        assert info.ram_gb is None or info.ram_gb > 0
        assert info.vram_gb is None or info.vram_gb > 0


class TestAssessModelFitGpuPath:
    def test_ok_when_vram_well_over(self) -> None:
        hw = HwInfo(ram_gb=16.0, has_gpu=True, vram_gb=8.0)
        fit = assess_model_fit(model_ram_gb=2.0, model_vram_gb=2.0, hw=hw)
        assert fit == ModelFit.OK

    def test_heavy_when_vram_just_fits(self) -> None:
        """HW VRAM がモデル VRAM の 1.0x〜1.5x の間は HEAVY。"""
        hw = HwInfo(ram_gb=16.0, has_gpu=True, vram_gb=3.0)
        fit = assess_model_fit(model_ram_gb=1.0, model_vram_gb=2.5, hw=hw)
        assert fit == ModelFit.HEAVY

    def test_falls_back_to_ram_when_vram_insufficient(self) -> None:
        """VRAM 不足 → CPU 経路で RAM 判定。RAM 余裕なら OK。"""
        hw = HwInfo(ram_gb=32.0, has_gpu=True, vram_gb=1.0)
        fit = assess_model_fit(model_ram_gb=3.0, model_vram_gb=4.0, hw=hw)
        assert fit == ModelFit.OK  # RAM 32GB は 3GB の 2x 以上


class TestAssessModelFitCpuPath:
    def test_ok_when_ram_double(self) -> None:
        hw = HwInfo(ram_gb=16.0, has_gpu=False, vram_gb=None)
        fit = assess_model_fit(model_ram_gb=3.0, model_vram_gb=None, hw=hw)
        assert fit == ModelFit.OK

    def test_heavy_when_ram_just_above(self) -> None:
        """RAM がモデルの 1.2x〜2.0x の間は HEAVY。"""
        hw = HwInfo(ram_gb=8.0, has_gpu=False, vram_gb=None)
        fit = assess_model_fit(model_ram_gb=5.0, model_vram_gb=None, hw=hw)
        assert fit == ModelFit.HEAVY

    def test_infeasible_when_ram_below(self) -> None:
        hw = HwInfo(ram_gb=4.0, has_gpu=False, vram_gb=None)
        fit = assess_model_fit(model_ram_gb=10.0, model_vram_gb=None, hw=hw)
        assert fit == ModelFit.INFEASIBLE


class TestAssessModelFitUnknown:
    def test_unknown_when_no_model_info(self) -> None:
        hw = HwInfo(ram_gb=16.0, has_gpu=True, vram_gb=8.0)
        fit = assess_model_fit(model_ram_gb=None, model_vram_gb=None, hw=hw)
        assert fit == ModelFit.UNKNOWN

    def test_unknown_when_no_hw_info(self) -> None:
        hw = HwInfo(ram_gb=None, has_gpu=False, vram_gb=None)
        fit = assess_model_fit(model_ram_gb=2.0, model_vram_gb=None, hw=hw)
        assert fit == ModelFit.UNKNOWN

    def test_unknown_when_gpu_only_info_and_no_gpu(self) -> None:
        """モデルが VRAM 情報しか持たず、HW に GPU が無いケース。

        現状仕様: CPU 経路に RAM 判定材料がないので UNKNOWN。
        """
        hw = HwInfo(ram_gb=16.0, has_gpu=False, vram_gb=None)
        fit = assess_model_fit(model_ram_gb=None, model_vram_gb=2.0, hw=hw)
        # CPU 経路で model_ram_gb=None → 判定不能
        assert fit == ModelFit.UNKNOWN
