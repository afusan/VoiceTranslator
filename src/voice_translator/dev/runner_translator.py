"""Translator レイヤの単体 CLI ランナー。

役割: テキスト(`--text` / `--input <txt|json>` / stdin)を NLLB-200 で翻訳して JSON で出す。
生成パラメータ(num_beams / no_repeat_ngram_size / repetition_penalty 等)を CLI から
切り替えられるので、degenerate 出力の再現 → 設定変更の効果検証に使う
(pendList「翻訳バックエンドの生成パラメータを設定可能にする」)。

使い方:
    py -m voice_translator.dev.runner_translator --text "hello world"
    py -m voice_translator.dev.runner_translator --input dumped_asr.json --tgt-lang ja
    py -m voice_translator.dev.runner_translator --input long.txt \
        --num-beams 4 --no-repeat-ngram-size 3 --repetition-penalty 1.1
    echo "hello" | py -m voice_translator.dev.runner_translator --src-lang en --tgt-lang ja

戻り値の JSON は StageDumpWriter の seq_NNNN_translate.json と同じスキーマ。
"""

from __future__ import annotations

import argparse
import time
from pathlib import Path
from typing import Any

from voice_translator.translator.nllb200_backend import Nllb200TranslatorBackend

from ._common import (
    add_common_args,
    emit_json,
    resolve_text_input,
    setup_logger,
)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m voice_translator.dev.runner_translator",
        description="NLLB-200 単体ランナー(text → JSON)",
    )
    src = p.add_mutually_exclusive_group()
    src.add_argument("--text", "-t", default=None, help="翻訳対象テキストを直接指定")
    src.add_argument(
        "--input", "-i", type=Path, default=None,
        help="入力ファイル(.json なら text/src_text/tgt_text フィールド、それ以外は素テキスト)",
    )
    p.add_argument(
        "--output", "-o", type=Path, default=None,
        help="出力 JSON パス(省略時は stdout)",
    )
    p.add_argument(
        "--src-lang", default="en",
        help="翻訳元言語(ISO 639-1)。.json 入力に src_lang があれば上書き可",
    )
    p.add_argument("--tgt-lang", default="ja", help="翻訳先言語(ISO 639-1)")
    p.add_argument("--device", "-d", default="auto", help='"auto"/"cuda"/"mps"/"cpu"')
    p.add_argument(
        "--model-name", default="facebook/nllb-200-distilled-600M",
        help="HuggingFace モデル名(差し替え検証用)",
    )
    # 生成パラメータ — degenerate 検証の主軸
    p.add_argument("--num-beams", type=int, default=4)
    p.add_argument("--no-repeat-ngram-size", type=int, default=3)
    p.add_argument("--repetition-penalty", type=float, default=1.1)
    p.add_argument("--max-length", type=int, default=512)
    p.add_argument(
        "--no-early-stopping", action="store_true",
        help="早期停止を OFF にする(既定 ON)",
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

    try:
        text, meta = resolve_text_input(text=args.text, input_path=args.input)
    except (ValueError, OSError) as exc:
        logger.error("入力の解決に失敗: %s", exc)
        return 2

    # 入力 JSON に src_lang が乗っていれば優先(ダンプデータの再生で便利)
    src_lang = args.src_lang
    if meta and isinstance(meta.get("src_lang"), str):
        src_lang = meta["src_lang"]
    tgt_lang = args.tgt_lang

    logger.info("入力: %d chars / src=%s tgt=%s", len(text), src_lang, tgt_lang)
    logger.info(
        "Nllb200TranslatorBackend 構築: model=%s device=%s num_beams=%d "
        "no_repeat_ngram_size=%d repetition_penalty=%.2f",
        args.model_name, args.device, args.num_beams,
        args.no_repeat_ngram_size, args.repetition_penalty,
    )
    backend = Nllb200TranslatorBackend(
        model_name=args.model_name,
        device=args.device,
        num_beams=args.num_beams,
        no_repeat_ngram_size=args.no_repeat_ngram_size,
        repetition_penalty=args.repetition_penalty,
        max_length=args.max_length,
        early_stopping=not args.no_early_stopping,
    )
    logger.info("解決後: device=%s", backend.device)

    t0 = time.perf_counter()
    tgt_text = backend.translate(text, src_lang, tgt_lang)
    elapsed_ms = (time.perf_counter() - t0) * 1000.0
    logger.info("translate 完了: %.0f ms / out_chars=%d", elapsed_ms, len(tgt_text))

    payload: dict[str, Any] = {
        "seq_id": int(args.seq_id),
        "stage": "translate",
        "src_lang": src_lang,
        "tgt_lang": tgt_lang,
        "src_text": text,
        "tgt_text": tgt_text,
        "runner": {
            "name": "runner_translator",
            "model_name": args.model_name,
            "device_requested": args.device,
            "device_resolved": backend.device,
            "num_beams": args.num_beams,
            "no_repeat_ngram_size": args.no_repeat_ngram_size,
            "repetition_penalty": args.repetition_penalty,
            "max_length": args.max_length,
            "early_stopping": not args.no_early_stopping,
            "elapsed_ms": round(elapsed_ms, 1),
        },
    }
    emit_json(payload, output=args.output)
    return 0


if __name__ == "__main__":  # pragma: no cover - エントリポイント
    raise SystemExit(run())
