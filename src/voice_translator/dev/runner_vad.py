"""VAD レイヤの単体 CLI ランナー。

役割: WAV を SileroVadBackend に流し込んで発話区切りを検出、確定した発話を
個別 WAV (`seq_NNNN_vad.wav`) として書き出し、まとめて `index.json` に
タイムスタンプ付きで記録する。発話区切りパラメータ(threshold / min_silence_ms /
max_speech_sec / speech_pad_ms)を CLI から切り替えて試せる。

使い方:
    py -m voice_translator.dev.runner_vad --input long.wav --out-dir vad_out/
    py -m voice_translator.dev.runner_vad --input long.wav --out-dir vad_out/ \
        --threshold 0.3 --min-silence-ms 300 --max-speech-sec 6.0

ダンプデータでもなんでもよい: 16kHz/mono WAV を食わせれば動く(samplerate 不一致は警告)。
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import numpy as np

from voice_translator.common.types import INTERNAL_SAMPLE_RATE
from voice_translator.vad.silero_backend import SILERO_CHUNK_SAMPLES, SileroVadBackend

from ._common import (
    add_common_args,
    read_wav_as_float32_mono,
    setup_logger,
    write_json,
    write_wav_float32,
)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m voice_translator.dev.runner_vad",
        description="silero-vad 単体ランナー(WAV -> 分割 WAV 群 + index.json)",
    )
    p.add_argument("--input", "-i", type=Path, required=True, help="入力 WAV パス")
    p.add_argument(
        "--out-dir", "-O", type=Path, required=True,
        help="分割 WAV と index.json の出力先ディレクトリ",
    )
    p.add_argument("--threshold", type=float, default=0.5)
    p.add_argument("--min-silence-ms", type=int, default=500)
    p.add_argument("--speech-pad-ms", type=int, default=100)
    p.add_argument(
        "--max-speech-sec", type=float, default=8.0,
        help="1 発話の最大長(秒)。0 で無効化(従来通り VAD の end イベントだけが頼り)",
    )
    p.add_argument(
        "--chunk-samples", type=int, default=SILERO_CHUNK_SAMPLES * 4,
        help="入力をこのサンプル数ごとに区切って投入(VAD 内部で 512 単位に再分割される)",
    )
    add_common_args(p)
    return p


def run(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logger = setup_logger(args.verbose)

    if not args.input.is_file():
        logger.error("入力 WAV が存在しません: %s", args.input)
        return 2

    pcm, sr = read_wav_as_float32_mono(args.input)
    if sr != INTERNAL_SAMPLE_RATE:
        logger.warning(
            "WAV のサンプルレート %d Hz は内部標準 %d Hz と一致しません(VAD 結果が崩れる可能性)",
            sr, INTERNAL_SAMPLE_RATE,
        )
    logger.info("入力: %s (%d samples @ %d Hz, %.2f sec)", args.input, pcm.size, sr, pcm.size / sr)

    backend = SileroVadBackend(
        threshold=args.threshold,
        min_silence_ms=args.min_silence_ms,
        speech_pad_ms=args.speech_pad_ms,
        max_speech_sec=args.max_speech_sec,
    )
    backend.reset()

    args.out_dir.mkdir(parents=True, exist_ok=True)

    segments: list[dict[str, Any]] = []
    cursor = 0
    seq_id = 0
    total = pcm.size
    chunk_n = max(args.chunk_samples, 32)
    while cursor < total:
        end = min(cursor + chunk_n, total)
        chunk = pcm[cursor:end]
        cursor = end
        try:
            new_segments = backend.process(chunk)
        except Exception as exc:  # noqa: BLE001
            logger.error("VAD process 失敗 @ sample=%d: %s", cursor, exc)
            return 3
        for seg in new_segments:
            seq_id += 1
            out_wav = args.out_dir / f"seq_{seq_id:04d}_vad.wav"
            write_wav_float32(out_wav, seg.pcm, sr)
            meta = {
                "seq_id": seq_id,
                "file": out_wav.name,
                "samples": int(seg.pcm.size),
                "duration_sec": round(seg.pcm.size / sr, 3),
                "started_at_monotonic": round(seg.started_at_monotonic, 6),
            }
            segments.append(meta)
            logger.info(
                "seg #%d: %d samples (%.2f sec) -> %s",
                seq_id, seg.pcm.size, meta["duration_sec"], out_wav.name,
            )

    index = {
        "input": str(args.input),
        "samplerate": sr,
        "total_samples": int(total),
        "params": {
            "threshold": args.threshold,
            "min_silence_ms": args.min_silence_ms,
            "speech_pad_ms": args.speech_pad_ms,
            "max_speech_sec": args.max_speech_sec,
        },
        "segments": segments,
        "segment_count": len(segments),
    }
    write_json(args.out_dir / "index.json", index)
    logger.info("完了: %d セグメント -> %s", len(segments), args.out_dir)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(run())
