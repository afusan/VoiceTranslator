"""hw_info: ローカル環境の RAM / GPU / VRAM を検出し、モデル選択の目安に使う。

役割: GUI 詳細ダイアログで「✓ 推奨 / ⚠ 重い / ✗ 不可」のアイコンを出す材料を提供する。
正確さは要求しない(数 GB の誤差は許容)。psutil / torch.cuda が利用可能なら使い、
未インストール/失敗時は値を None にする。CPU floor 配布方針の前提上、検出失敗で
アプリが落ちることは避ける。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


@dataclass(frozen=True)
class HwInfo:
    """検出したハードウェア情報(値はベストエフォート)。

    - `ram_gb`: 物理 RAM の総量(GB)。検出失敗で None。
    - `has_gpu`: CUDA / MPS のどちらかが利用可能か。
    - `vram_gb`: GPU の総 VRAM(GB)。CUDA でしか出ない(MPS は共有 RAM のため None)。
    """

    ram_gb: float | None
    has_gpu: bool
    vram_gb: float | None


class ModelFit(str, Enum):
    """モデル選択時のリソース判定。

    - `OK`: 余裕で乗る(推奨)
    - `HEAVY`: ぎりぎり乗る or 動くが遅い(警告アイコン)
    - `INFEASIBLE`: 物理的に乗らない(選択非推奨)
    - `UNKNOWN`: 判定材料が無い(モデル情報 or HW 情報のどちらかが None)
    """

    OK = "OK"
    HEAVY = "HEAVY"
    INFEASIBLE = "INFEASIBLE"
    UNKNOWN = "UNKNOWN"


def detect_hw() -> HwInfo:
    """ローカル環境の RAM / GPU / VRAM を検出して `HwInfo` を返す。

    検出に必要なライブラリ(`psutil` / `torch`)が無い・失敗する場合は該当値を None にする。
    """
    ram = _detect_ram_gb()
    has_gpu, vram = _detect_gpu()
    return HwInfo(ram_gb=ram, has_gpu=has_gpu, vram_gb=vram)


def assess_model_fit(
    *,
    model_ram_gb: float | None,
    model_vram_gb: float | None,
    hw: HwInfo,
) -> ModelFit:
    """モデルの必要リソースと HW を照合してフィット度を返す。

    判定方針(暫定。Phase F の実機検証でしきい値見直しの可能性あり):
    - 必要リソースが未指定なら UNKNOWN
    - GPU 利用可 + VRAM 値判明 + model_vram_gb 指定 → VRAM で判定:
        - HW VRAM >= 必要 VRAM × 1.5 → OK
        - HW VRAM >= 必要 VRAM × 1.0 → HEAVY
        - それ以下 → 一旦 RAM 判定にフォールバック(CPU 動作の可能性を残す)
    - CPU フォールバック(or GPU 無し): RAM で判定:
        - HW RAM >= 必要 RAM × 2.0 → OK
        - HW RAM >= 必要 RAM × 1.2 → HEAVY
        - それ以下 → INFEASIBLE
    - HW 値も判定材料も無いなら UNKNOWN
    """
    has_model_info = (model_ram_gb is not None) or (model_vram_gb is not None)
    if not has_model_info:
        return ModelFit.UNKNOWN

    # GPU 経路
    if hw.has_gpu and hw.vram_gb is not None and model_vram_gb is not None:
        if hw.vram_gb >= model_vram_gb * 1.5:
            return ModelFit.OK
        if hw.vram_gb >= model_vram_gb:
            return ModelFit.HEAVY
        # VRAM 不足 → CPU 経路(RAM)で再判定。INFEASIBLE 判定は CPU 経路に委ねる。

    # CPU 経路(GPU 無し or VRAM 不足の CPU フォールバック)
    if hw.ram_gb is not None and model_ram_gb is not None:
        if hw.ram_gb >= model_ram_gb * 2.0:
            return ModelFit.OK
        if hw.ram_gb >= model_ram_gb * 1.2:
            return ModelFit.HEAVY
        return ModelFit.INFEASIBLE

    return ModelFit.UNKNOWN


# ============================================================
# 内部: HW 検出ヘルパ
# ============================================================
def _detect_ram_gb() -> float | None:
    try:
        import psutil  # type: ignore
    except Exception:  # noqa: BLE001
        return None
    try:
        return psutil.virtual_memory().total / (1024 ** 3)
    except Exception:  # noqa: BLE001
        return None


def _detect_gpu() -> tuple[bool, float | None]:
    """CUDA → MPS の順に検出。VRAM は CUDA のときのみ取得。"""
    # CUDA
    try:
        import torch  # type: ignore
    except Exception:  # noqa: BLE001
        return False, None
    try:
        if torch.cuda.is_available():
            try:
                props = torch.cuda.get_device_properties(0)
                vram = props.total_memory / (1024 ** 3)
            except Exception:  # noqa: BLE001
                vram = None
            return True, vram
    except Exception:  # noqa: BLE001
        pass
    # MPS(Apple Silicon)。VRAM は共有 RAM なので None。
    try:
        if hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
            return True, None
    except Exception:  # noqa: BLE001
        pass
    return False, None
