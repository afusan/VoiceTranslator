"""デバイス選択ヘルパ: `device="auto"` を実際のデバイス名に解決する。

役割: PyTorch ベースのモデル(NLLB-200 など)と CTranslate2 ベースのモデル
(faster-whisper)で「使えるアクセラレータがあれば使う」を共通化する。
GPU 専用のコードパスを増やさず、単一の引数 `device` で「auto / cuda / mps / cpu」を
吸収するのが狙い。

配布方針(CLAUDE.md「配布方針」参照):
- CPU を floor とする(なければ CPU で動く)
- GPU/アクセラレータがあれば自動で使う(NVIDIA CUDA / Apple Silicon MPS)
- コードパスは1本(条件分岐を呼び出し側に作らない)
"""

from __future__ import annotations


def resolve_torch_device(preference: str = "auto") -> str:
    """PyTorch 用のデバイス名を返す。

    Args:
        preference: "auto" / "cuda" / "mps" / "cpu" のいずれか。
            "auto" の場合は cuda → mps → cpu の順に試す。
            明示指定の場合は **そのまま返す**(利用不可ならモデルロード時にエラーになる)。

    Returns:
        実際に使うデバイス名(小文字)。
    """
    pref = (preference or "auto").lower().strip()
    if pref != "auto":
        return pref

    try:
        import torch  # type: ignore
    except Exception:  # noqa: BLE001 - torch 未インストール時は CPU 扱い
        return "cpu"

    try:
        if torch.cuda.is_available():
            return "cuda"
    except Exception:  # noqa: BLE001
        pass

    # Apple Silicon (Metal Performance Shaders)
    try:
        mps_backend = getattr(torch.backends, "mps", None)
        if mps_backend is not None and mps_backend.is_available():
            return "mps"
    except Exception:  # noqa: BLE001
        pass

    return "cpu"


def resolve_ctranslate2_device(preference: str = "auto") -> str:
    """CTranslate2(faster-whisper)用のデバイス名を返す。

    CTranslate2 は MPS を未サポートなので、PyTorch 用とは別ロジックにする
    (auto で MPS にしようとしても fallback で CPU を返す)。
    """
    pref = (preference or "auto").lower().strip()
    if pref == "auto":
        # faster-whisper / CTranslate2 自体が "auto" を解釈する。素直に渡す。
        return "auto"
    if pref == "mps":
        # CTranslate2 は MPS 未対応 → CPU に落とす
        return "cpu"
    return pref


def resolve_ctranslate2_compute_type(
    device: str, preference: str = "auto"
) -> str:
    """CTranslate2 の compute_type を device に合わせて決める。

    Args:
        device: `resolve_ctranslate2_device` の結果(`"auto"` も含む)。
        preference: 明示指定があればそれを返す。"auto" のときに自動選択。

    Returns:
        - GPU(`cuda` / `auto`): `"float16"`(GPU で高速、VRAM 削減)
        - CPU: `"int8"`(CPU で最も実用的な量子化)
    """
    pref = (preference or "auto").lower().strip()
    if pref != "auto":
        return pref
    if device in ("cuda", "auto"):
        return "float16"
    return "int8"
