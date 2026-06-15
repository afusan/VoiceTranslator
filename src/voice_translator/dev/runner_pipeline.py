"""部分パイプラインの単体 CLI ランナー。

役割: VAD / ASR / Translator / TTS の任意の連続部分を **メモリ内で連結** して一気に流す。
本体の PipelineCoordinator は使わない(スレッド/キューを介さない順次実行)。
バックエンドは 1 度だけロードされるので、複数発話を含む WAV をバッチ処理するときに
1 件ずつランナーを呼ぶより速い。

使い方:
    # 長尺 WAV を VAD で区切って ASR まで一気に
    py -m voice_translator.dev.runner_pipeline --from vad --to asr \
        --input long.wav --out-dir out/ --model small --device auto

    # ダンプ済み WAV を ASR から TTS まで(VAD はスキップ)
    py -m voice_translator.dev.runner_pipeline --from asr --to tts \
        --input logs/dumps/<run>/seq_0042_vad.wav --out-dir out/

    # ASR ダンプ JSON を翻訳 → 合成(degenerate 再現)
    py -m voice_translator.dev.runner_pipeline --from translate --to tts \
        --input logs/dumps/<run>/seq_0042_asr.json --out-dir out/ \
        --num-beams 4 --no-repeat-ngram-size 3

出力先には StageDumpWriter と同じ命名規約で seq_NNNN_<stage>.{wav,json} を書き、
index.json に処理時刻・使用パラメータ・件数を残す。
"""

from __future__ import annotations

import argparse
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np

from voice_translator.common.types import INTERNAL_SAMPLE_RATE
from voice_translator.vad.silero_backend import (
    SILERO_CHUNK_SAMPLES,
    SileroVadBackend,
)

from ._common import (
    add_common_args,
    read_wav_as_float32_mono,
    resolve_text_input,
    setup_logger,
    write_json,
    write_wav_float32,
)


STAGES = ("vad", "asr", "translate", "tts")
_STAGE_IDX = {s: i for i, s in enumerate(STAGES)}


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m voice_translator.dev.runner_pipeline",
        description="部分パイプライン(VAD/ASR/Translator/TTS の連続部分)",
    )
    p.add_argument("--from", dest="frm", choices=STAGES, required=True, help="開始ステージ")
    p.add_argument("--to", dest="to", choices=STAGES, required=True, help="終了ステージ")
    p.add_argument(
        "--input", "-i", type=Path, required=True,
        help="入力ファイル(vad/asr 開始は WAV、translate/tts 開始は txt または json)",
    )
    p.add_argument("--out-dir", "-O", type=Path, required=True, help="出力ディレクトリ")
    p.add_argument("--src-lang", default="eng", help="翻訳元言語(ISO 639-3)")
    p.add_argument("--tgt-lang", default="jpn", help="翻訳先言語(ISO 639-3)")
    # VAD
    p.add_argument("--vad-threshold", type=float, default=0.5)
    p.add_argument("--vad-min-silence-ms", type=int, default=500)
    p.add_argument("--vad-speech-pad-ms", type=int, default=100)
    p.add_argument("--vad-max-speech-sec", type=float, default=8.0)
    p.add_argument(
        "--vad-chunk-samples", type=int, default=SILERO_CHUNK_SAMPLES * 4,
    )
    # ASR
    p.add_argument("--model", "-m", default="small")
    p.add_argument("--device", "-d", default="auto", help="ASR と Translator 共通")
    p.add_argument("--compute-type", "-c", default="auto", help="ASR (faster-whisper)")
    p.add_argument("--beam-size", "-b", type=int, default=1)
    # Translator
    p.add_argument("--num-beams", type=int, default=4)
    p.add_argument("--no-repeat-ngram-size", type=int, default=3)
    p.add_argument("--repetition-penalty", type=float, default=1.1)
    p.add_argument("--max-length", type=int, default=512)
    p.add_argument("--no-early-stopping", action="store_true")
    p.add_argument(
        "--model-name", default="facebook/nllb-200-distilled-600M",
        help="Translator HuggingFace モデル名",
    )
    # TTS
    p.add_argument("--rate", type=int, default=180)
    p.add_argument("--flush-delay-sec", type=float, default=0.1)
    add_common_args(p)
    return p


def run(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logger = setup_logger(args.verbose)

    if _STAGE_IDX[args.frm] > _STAGE_IDX[args.to]:
        logger.error("--from は --to 以前のステージである必要があります: %s -> %s", args.frm, args.to)
        return 2
    if not args.input.exists():
        logger.error("入力が存在しません: %s", args.input)
        return 2

    args.out_dir.mkdir(parents=True, exist_ok=True)

    # 入力 → 「発話単位の作業ユニット」のリストに変換する
    # work_units は各要素が dict {"seq_id", "pcm"|"asr_text"|"translated_text", "src_lang", "tgt_lang"} の流動的な状態
    work_units = _build_initial_units(args, logger)
    if work_units is None:
        return 3

    # 各ステージを順に走らせる
    # VAD は work_units を「単一 WAV → 複数セグメント」に展開する特殊ケース
    stages_to_run = [
        s for s in STAGES
        if _STAGE_IDX[args.frm] <= _STAGE_IDX[s] <= _STAGE_IDX[args.to]
    ]

    summary: dict[str, Any] = {
        "from": args.frm, "to": args.to,
        "input": str(args.input), "out_dir": str(args.out_dir),
        "started_at": datetime.now().isoformat(timespec="seconds"),
        "params": vars(args).copy(),
        "stages": stages_to_run,
        "units": [],
    }
    summary["params"]["input"] = str(args.input)
    summary["params"]["out_dir"] = str(args.out_dir)

    try:
        for stage in stages_to_run:
            t0 = time.perf_counter()
            if stage == "vad":
                work_units = _stage_vad(args, work_units, logger)
            elif stage == "asr":
                work_units = _stage_asr(args, work_units, logger)
            elif stage == "translate":
                work_units = _stage_translate(args, work_units, logger)
            elif stage == "tts":
                work_units = _stage_tts(args, work_units, logger)
            logger.info(
                "stage %s 完了: %d unit / %.0f ms",
                stage, len(work_units), (time.perf_counter() - t0) * 1000.0,
            )
    except Exception as exc:  # noqa: BLE001
        logger.exception("パイプライン実行中に例外: %s", exc)
        return 4

    # index.json に結果サマリを残す
    for u in work_units:
        summary["units"].append({
            "seq_id": u["seq_id"],
            "files": u.get("files", []),
            "src_text": u.get("asr_text"),
            "tgt_text": u.get("translated_text"),
        })
    summary["finished_at"] = datetime.now().isoformat(timespec="seconds")
    write_json(args.out_dir / "index.json", summary)
    logger.info("完了: %d unit -> %s", len(work_units), args.out_dir)
    return 0


# ============================================================
# 初期 work units の組み立て
# ============================================================
def _build_initial_units(args, logger) -> list[dict[str, Any]] | None:
    """`--from` に応じて入力から最初の work_units を作る。"""
    if args.frm in ("vad", "asr"):
        pcm, sr = read_wav_as_float32_mono(args.input)
        if sr != INTERNAL_SAMPLE_RATE:
            logger.warning(
                "入力 WAV のサンプルレート %d Hz は内部標準 %d Hz と一致しません", sr, INTERNAL_SAMPLE_RATE
            )
        if args.frm == "vad":
            return [{"seq_id": 0, "_long_pcm": pcm, "_long_sr": sr, "files": []}]
        # asr から: 1 つのセグメントとして扱う
        return [{
            "seq_id": 1, "pcm": pcm, "samplerate": sr,
            "src_lang": args.src_lang, "tgt_lang": args.tgt_lang, "files": [],
        }]

    # translate / tts 開始: テキスト入力
    try:
        text, meta = resolve_text_input(text=None, input_path=args.input)
    except (ValueError, OSError) as exc:
        logger.error("入力の解決に失敗: %s", exc)
        return None
    src_lang = args.src_lang
    tgt_lang = args.tgt_lang
    if meta:
        src_lang = meta.get("src_lang", src_lang)
        tgt_lang = meta.get("tgt_lang", tgt_lang)
    if args.frm == "translate":
        return [{
            "seq_id": 1, "asr_text": text,
            "src_lang": src_lang, "tgt_lang": tgt_lang, "files": [],
        }]
    # tts 開始
    return [{
        "seq_id": 1, "translated_text": text,
        "src_lang": src_lang, "tgt_lang": tgt_lang, "files": [],
    }]


# ============================================================
# 各ステージ
# ============================================================
def _stage_vad(args, units, logger) -> list[dict[str, Any]]:
    """units は 1 要素(_long_pcm/_long_sr 入り) を想定し、複数 unit に展開する。"""
    backend = SileroVadBackend(
        threshold=args.vad_threshold,
        min_silence_ms=args.vad_min_silence_ms,
        speech_pad_ms=args.vad_speech_pad_ms,
        max_speech_sec=args.vad_max_speech_sec,
    )
    backend.reset()
    out: list[dict[str, Any]] = []
    next_seq = 1
    for u in units:
        pcm: np.ndarray = u["_long_pcm"]
        sr: int = u["_long_sr"]
        cursor = 0
        chunk_n = max(args.vad_chunk_samples, 32)
        while cursor < pcm.size:
            end = min(cursor + chunk_n, pcm.size)
            chunk = pcm[cursor:end]
            cursor = end
            for seg in backend.process(chunk):
                wav_path = args.out_dir / f"seq_{next_seq:04d}_vad.wav"
                write_wav_float32(wav_path, seg.pcm, sr)
                out.append({
                    "seq_id": next_seq,
                    "pcm": np.asarray(seg.pcm, dtype=np.float32),
                    "samplerate": sr,
                    "src_lang": args.src_lang,
                    "tgt_lang": args.tgt_lang,
                    "files": [wav_path.name],
                })
                next_seq += 1
    logger.info("VAD で %d セグメントに分割", len(out))
    return out


def _stage_asr(args, units, logger) -> list[dict[str, Any]]:
    from voice_translator.asr.faster_whisper_backend import FasterWhisperAsrBackend
    backend = FasterWhisperAsrBackend(
        model_size=args.model,
        device=args.device,
        compute_type=args.compute_type,
        beam_size=args.beam_size,
    )
    logger.info("ASR backend: device=%s compute_type=%s", backend.device, backend.compute_type)
    for u in units:
        text, lang = backend.transcribe(u["pcm"], src_lang_hint=u.get("src_lang", "auto"))
        u["asr_text"] = text
        u["src_lang"] = lang or u.get("src_lang", "auto")
        json_path = args.out_dir / f"seq_{u['seq_id']:04d}_asr.json"
        write_json(json_path, {
            "seq_id": u["seq_id"], "stage": "asr",
            "src_lang": u["src_lang"], "text": text,
        })
        u.setdefault("files", []).append(json_path.name)
    return units


def _stage_translate(args, units, logger) -> list[dict[str, Any]]:
    from voice_translator.translator.nllb200_backend import Nllb200TranslatorBackend
    backend = Nllb200TranslatorBackend(
        model_name=args.model_name,
        device=args.device,
        num_beams=args.num_beams,
        no_repeat_ngram_size=args.no_repeat_ngram_size,
        repetition_penalty=args.repetition_penalty,
        max_length=args.max_length,
        early_stopping=not args.no_early_stopping,
    )
    logger.info("Translator backend: device=%s", backend.device)
    for u in units:
        src = u.get("asr_text", "")
        out = backend.translate(src, u.get("src_lang", args.src_lang), u.get("tgt_lang", args.tgt_lang))
        u["translated_text"] = out
        json_path = args.out_dir / f"seq_{u['seq_id']:04d}_translate.json"
        write_json(json_path, {
            "seq_id": u["seq_id"], "stage": "translate",
            "src_lang": u.get("src_lang"), "tgt_lang": u.get("tgt_lang", args.tgt_lang),
            "src_text": src, "tgt_text": out,
        })
        u.setdefault("files", []).append(json_path.name)
    return units


def _stage_tts(args, units, logger) -> list[dict[str, Any]]:
    from voice_translator.tts.sapi_backend import SapiTtsBackend
    backend = SapiTtsBackend(
        rate=args.rate,
        voice_lang_hint=args.tgt_lang,
        flush_delay_sec=args.flush_delay_sec,
    )
    for u in units:
        text = u.get("translated_text") or u.get("asr_text") or ""
        if not text:
            logger.warning("seq=%s: TTS 入力テキストが空", u["seq_id"])
            continue
        pcm, sr = backend.synthesize(text, u.get("tgt_lang", args.tgt_lang))
        wav_path = args.out_dir / f"seq_{u['seq_id']:04d}_tts.wav"
        write_wav_float32(wav_path, pcm, sr)
        u.setdefault("files", []).append(wav_path.name)
    return units


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(run())
