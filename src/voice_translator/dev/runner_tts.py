"""TTS レイヤの単体 CLI ランナー。

役割: テキスト(--text / --input <txt|json> / stdin)を SAPI で合成して WAV に書く。
SAPI 固有の rate などを CLI から切り替えて、合成結果をファイルで残せる。

使い方:
    py -m voice_translator.dev.runner_tts --text "こんにちは" --output hello.wav
    py -m voice_translator.dev.runner_tts --input dumped_translate.json --output out.wav
    echo "おはようございます" | py -m voice_translator.dev.runner_tts -o morning.wav

出力 WAV は StageDumpWriter の seq_NNNN_tts.wav と同じ形式(mono int16)。
"""

from __future__ import annotations

import argparse
import time
from pathlib import Path

from voice_translator.tts.sapi_backend import SapiTtsBackend

from ._common import (
    add_common_args,
    resolve_text_input,
    setup_logger,
    write_wav_float32,
)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m voice_translator.dev.runner_tts",
        description="SAPI TTS 単体ランナー(text -> WAV)",
    )
    src = p.add_mutually_exclusive_group()
    src.add_argument("--text", "-t", default=None, help="合成対象テキストを直接指定")
    src.add_argument(
        "--input", "-i", type=Path, default=None,
        help="入力ファイル(.json なら tgt_text/text を採用、それ以外は素テキスト)",
    )
    p.add_argument(
        "--output", "-o", type=Path, required=True,
        help="出力 WAV パス(必須 — 再生したい場合は runner_output を別途使う)",
    )
    p.add_argument("--tgt-lang", default="jpn", help="合成言語ヒント(ISO 639-3。voice 選択用)")
    p.add_argument("--rate", type=int, default=180, help="読み上げ速度(WPM 相当)")
    p.add_argument(
        "--flush-delay-sec", type=float, default=0.1,
        help="runAndWait 後の待機(SAPI flush 不整合の暫定対処)",
    )
    add_common_args(p)
    return p


def run(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logger = setup_logger(args.verbose)

    try:
        text, meta = resolve_text_input(text=args.text, input_path=args.input)
    except (ValueError, OSError) as exc:
        logger.error("入力の解決に失敗: %s", exc)
        return 2

    tgt_lang = args.tgt_lang
    if meta and isinstance(meta.get("tgt_lang"), str):
        tgt_lang = meta["tgt_lang"]

    logger.info("入力: %d chars / tgt=%s", len(text), tgt_lang)
    backend = SapiTtsBackend(
        rate=args.rate,
        voice_lang_hint=tgt_lang,
        flush_delay_sec=args.flush_delay_sec,
    )

    t0 = time.perf_counter()
    pcm, sr = backend.synthesize(text, tgt_lang)
    elapsed_ms = (time.perf_counter() - t0) * 1000.0
    logger.info("synthesize 完了: %.0f ms / %d samples @ %d Hz", elapsed_ms, len(pcm), sr)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    write_wav_float32(args.output, pcm, sr)
    logger.info("出力: %s", args.output)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(run())
