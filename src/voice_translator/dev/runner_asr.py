"""ASR レイヤの単体 CLI ランナー。

役割: WAV ファイル + パラメータ(モデル/device/compute_type/beam_size)を
指定して faster-whisper 単体で書き起こし、(text, lang) を JSON で出力する。
本体パイプラインの ASR 部分だけを再現するため、Coordinator / Ledger / GUI は
一切使わない。

使い方:
    py -m voice_translator.dev.runner_asr --input sample.wav --model small
    py -m voice_translator.dev.runner_asr --input sample.wav --model medium \
        --device cuda --compute-type int8_float16 --output result.json

ダンプデータの再生(`./logs/dumps/<run_id>/seq_NNNN_vad.wav` を直接食わせる):
    py -m voice_translator.dev.runner_asr --input logs/dumps/<run>/seq_0042_vad.wav

戻り値の JSON は StageDumpWriter の seq_NNNN_asr.json と同じスキーマ。
"""

from __future__ import annotations

import argparse
import time
from pathlib import Path
from typing import Any

from voice_translator.asr.faster_whisper_backend import FasterWhisperAsrBackend

from ._common import (
    add_common_args,
    emit_json,
    read_wav_as_float32_mono,
    setup_logger,
)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m voice_translator.dev.runner_asr",
        description="faster-whisper 単体ランナー(WAV → JSON)",
    )
    p.add_argument("--input", "-i", type=Path, required=True, help="入力 WAV パス")
    p.add_argument(
        "--output", "-o", type=Path, default=None,
        help="出力 JSON パス(省略時は stdout)",
    )
    p.add_argument(
        "--model", "-m", default="small",
        help="Whisper モデル名(tiny/base/small/medium/large-v2/large-v3 等)",
    )
    p.add_argument(
        "--device", "-d", default="auto",
        help='device 指定: "auto"/"cuda"/"cpu"',
    )
    p.add_argument(
        "--compute-type", "-c", default="auto",
        help='compute_type: "auto"/"int8"/"float16"/"int8_float16" 等',
    )
    p.add_argument(
        "--beam-size", "-b", type=int, default=1,
        help="ビーム幅(>1 で精度↑/遅延↑)",
    )
    p.add_argument(
        "--lang-hint", "-l", default="auto",
        help='言語ヒント(ISO 639-1)。"auto" / "" で自動検出',
    )
    p.add_argument(
        "--seq-id", type=int, default=1,
        help="出力 JSON に載せる seq_id(ダミー値で OK)",
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
    logger.info(
        "入力: %s (%d samples @ %d Hz, %.2f sec)",
        args.input, pcm.size, sr, pcm.size / sr,
    )

    logger.info(
        "FasterWhisperAsrBackend を構築: model=%s device=%s compute_type=%s beam_size=%d",
        args.model, args.device, args.compute_type, args.beam_size,
    )
    backend = FasterWhisperAsrBackend(
        model_size=args.model,
        device=args.device,
        compute_type=args.compute_type,
        beam_size=args.beam_size,
    )
    logger.info("解決後: device=%s compute_type=%s", backend.device, backend.compute_type)

    t0 = time.perf_counter()
    text, lang = backend.transcribe(pcm, src_lang_hint=args.lang_hint)
    elapsed_ms = (time.perf_counter() - t0) * 1000.0
    logger.info("transcribe 完了: %.0f ms / lang=%s / chars=%d", elapsed_ms, lang, len(text))

    payload: dict[str, Any] = {
        "seq_id": int(args.seq_id),
        "stage": "asr",
        "src_lang": lang,
        "text": text,
        # 検証用に実行コンテキストも載せる(将来の比較で役立つ)
        "runner": {
            "name": "runner_asr",
            "model": args.model,
            "device_requested": args.device,
            "device_resolved": backend.device,
            "compute_type_requested": args.compute_type,
            "compute_type_resolved": backend.compute_type,
            "beam_size": args.beam_size,
            "input": str(args.input),
            "input_samplerate": sr,
            "input_samples": int(pcm.size),
            "elapsed_ms": round(elapsed_ms, 1),
        },
    }
    emit_json(payload, output=args.output)
    return 0


if __name__ == "__main__":  # pragma: no cover - エントリポイント
    raise SystemExit(run())
