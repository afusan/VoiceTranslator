"""dev ランナー共通ヘルパ。

役割: 各レイヤ CLI ランナー(`runner_*`)で重複する処理を集約する。
- WAV ファイルの読み込み(int16/float32 → float32 mono に正規化)
- JSON ファイルの読み書き
- argparse の共通オプション・ロガー初期化
- 入力が「ダンプ出力 JSON」か「素のテキスト」かの判定

ランナー本体は薄く保ち、ここに副作用とフォーマット規約を寄せる。
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import wave
from pathlib import Path
from typing import Any

import numpy as np


# ============================================================
# WAV IO
# ============================================================
def read_wav_as_float32_mono(path: Path | str) -> tuple[np.ndarray, int]:
    """WAV を float32 mono PCM として読む。

    対応: 8bit unsigned / 16bit signed int / モノラル または ステレオ(平均してモノ化)。
    Returns: (pcm, samplerate)。pcm は float32, 範囲 [-1, 1] 目安。
    """
    with wave.open(str(path), "rb") as wf:
        sr = wf.getframerate()
        nch = wf.getnchannels()
        sampwidth = wf.getsampwidth()
        frames = wf.readframes(wf.getnframes())
    if sampwidth == 2:
        pcm = np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0
    elif sampwidth == 1:
        pcm = np.frombuffer(frames, dtype=np.uint8).astype(np.float32) / 128.0 - 1.0
    else:
        raise ValueError(f"未対応のWAVサンプル幅: {sampwidth} bytes")
    if nch == 2 and pcm.size % 2 == 0:
        pcm = pcm.reshape(-1, 2).mean(axis=1)
    elif nch > 2:
        raise ValueError(f"未対応のチャネル数: {nch}")
    return pcm.astype(np.float32, copy=False), int(sr)


def write_wav_float32(path: Path | str, pcm: np.ndarray, samplerate: int) -> None:
    """float32 / int16 PCM を mono int16 WAV として書く。

    StageDumpWriter の WAV 書き出しと同じ規約。
    """
    arr = np.asarray(pcm)
    if arr.ndim > 1:
        arr = arr.mean(axis=tuple(range(1, arr.ndim)))
    if np.issubdtype(arr.dtype, np.floating):
        i16 = (np.clip(arr, -1.0, 1.0) * 32767.0).astype(np.int16)
    elif arr.dtype == np.int16:
        i16 = arr
    else:
        i16 = arr.astype(np.int16, copy=False)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(int(samplerate))
        wf.writeframes(i16.tobytes())


# ============================================================
# JSON IO
# ============================================================
def read_json(path: Path | str) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write_json(path: Path | str, payload: dict[str, Any]) -> None:
    Path(path).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


# ============================================================
# テキスト入力の解決(--text / --input が json か素テキストか)
# ============================================================
def resolve_text_input(*, text: str | None, input_path: Path | None) -> tuple[str, dict[str, Any] | None]:
    """ランナーへのテキスト入力を解決する。

    優先順位:
      1. `--text "..."` が指定されていればそれを使う(meta=None)。
      2. `--input <path>` が指定されている場合:
         - `.json` ならダンプ出力 JSON として読み、`text` / `tgt_text` / `src_text` の
           いずれかを順に試して採用。meta は dict 全体。
         - それ以外(`.txt` 等)はファイル全体を素テキストとして読む。
      3. どちらも無ければ stdin から読む。

    Returns: (text, meta)。meta はソースが JSON のときだけ非 None。
    """
    if text is not None:
        return text, None
    if input_path is not None:
        if input_path.suffix.lower() == ".json":
            data = read_json(input_path)
            for key in ("text", "tgt_text", "src_text"):
                if isinstance(data.get(key), str):
                    return data[key], data
            raise ValueError(
                f"{input_path}: text/tgt_text/src_text のいずれも見つかりません"
            )
        return input_path.read_text(encoding="utf-8").strip(), None
    # fallback: stdin
    data = sys.stdin.read().strip()
    if not data:
        raise ValueError("入力が空です(--text / --input / stdin のいずれかを指定)")
    return data, None


# ============================================================
# 共通 argparse
# ============================================================
def add_common_args(parser: argparse.ArgumentParser) -> None:
    """全ランナー共通の引数を追加する。"""
    parser.add_argument(
        "-v", "--verbose", action="store_true", help="DEBUG ログを出す"
    )


def setup_logger(verbose: bool = False) -> logging.Logger:
    """stderr に出すロガーを返す(stdout は結果出力に使うので避ける)。"""
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="[%(levelname)s] %(name)s: %(message)s",
        stream=sys.stderr,
    )
    return logging.getLogger("voice_translator.dev")


# ============================================================
# 出力先解決
# ============================================================
def emit_json(payload: dict[str, Any], *, output: Path | None) -> None:
    """JSON を出力先に書く。`--output` 未指定なら stdout に整形して出す。"""
    if output is None:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        output.parent.mkdir(parents=True, exist_ok=True)
        write_json(output, payload)
