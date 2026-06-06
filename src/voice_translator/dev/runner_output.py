"""Output レイヤ単体 CLI ランナー(切り分け用)。

役割: 「翻訳までは出ているのに音が鳴らない」ような環境で、Output レイヤ単体に
原因があるかを切り分けるための独立スクリプト。AppController / PipelineCoordinator
を経由せず、`AudioOutputBackend` を直接構築 → デバイスを開いて → 既知の PCM を
再生する。これで音が鳴れば Output 自体は健全、鳴らなければ Output / デバイス /
soundcard の問題に絞れる。

使い方:
    # 1) まずデバイス一覧を確認(現在の環境で見える出力デバイス + デフォルト)
    py -m voice_translator.dev.runner_output --list-devices

    # 2) デフォルト出力デバイスに 440Hz のサイン波を 1 秒再生
    py -m voice_translator.dev.runner_output --tone

    # 3) 任意デバイスに WAV を再生(device_id は --list-devices の出力から)
    py -m voice_translator.dev.runner_output \\
        --device-id "{0.0.0.00000000}.{...}" --wav some.wav

    # 4) TTS backend と組み合わせて「合成→再生」まで通す
    #    （SAPI → soundcard の経路が壊れていないかの確認）
    py -m voice_translator.dev.runner_output --text "テスト音声です"

    # 5) 出力 backend / TTS backend を明示指定(将来の差し替え時)
    py -m voice_translator.dev.runner_output --backend soundcard --text "hello" --tts sapi

設計メモ:
- Output backend は `BackendRegistry` 経由で取得する(本体の登録と完全に同じ経路を踏む)。
  これにより「runner では動くが本体では動かない」「逆」の差分が出ない。
- デフォルトでは ConfigStore を読まずに動かす(`register_default_backends(registry, config=None)`)。
  config の壊れ方を切り分けたいときに環境変数や config に依存させない方が確実なため。
- 鳴らす内容は (a) sine wave 自前生成 / (b) WAV ファイル / (c) TTS 経由合成 の 3 通り。
  (a) は backend → デバイスのパスだけを単純検証する用、(c) は本番経路に最も近い。
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path
from typing import Any

import numpy as np

from voice_translator.common.backend_registry import BackendRegistry
from voice_translator.common.backend_setup import register_default_backends
from voice_translator.common.types import LayerKind, OutputDevice

from ._common import (
    add_common_args,
    read_wav_as_float32_mono,
    setup_logger,
)


# ============================================================
# 音源生成
# ============================================================
def make_tone(
    *, freq_hz: float = 440.0, duration_sec: float = 1.0,
    samplerate: int = 44100, amplitude: float = 0.3,
) -> tuple[np.ndarray, int]:
    """sine wave を作る(デフォルト 440 Hz / 1 秒 / 44.1 kHz / 振幅 0.3)。

    振幅は 0.3 = -10 dBFS 程度。耳に痛くない範囲で「明確に聞こえる」音量。
    """
    n = int(duration_sec * samplerate)
    t = np.arange(n, dtype=np.float32) / float(samplerate)
    wave = amplitude * np.sin(2.0 * np.pi * float(freq_hz) * t)
    # 軽くフェードイン/フェードアウト(クリック音回避)
    fade_n = min(int(samplerate * 0.01), n // 10)  # 10ms or 全体の 1/10
    if fade_n > 0:
        ramp = np.linspace(0.0, 1.0, fade_n, dtype=np.float32)
        wave[:fade_n] *= ramp
        wave[-fade_n:] *= ramp[::-1]
    return wave.astype(np.float32, copy=False), samplerate


# ============================================================
# Backend / デバイス取得
# ============================================================
def _build_registry() -> BackendRegistry:
    """全 backend を登録した BackendRegistry を作る。

    config=None で渡しているので、各 backend は既定パラメータで登録される。
    runner では ConfigStore に依存させたくない(本番 config の壊れを runner に
    持ち込まないため)。
    """
    registry = BackendRegistry()
    register_default_backends(registry, config=None)
    return registry


def _create_output_backend(registry: BackendRegistry, name: str):
    """指定名の Output backend を生成して返す。"""
    return registry.create(LayerKind.OUTPUT, name)


def _create_tts_backend(registry: BackendRegistry, name: str):
    """指定名の TTS backend を生成して返す。"""
    return registry.create(LayerKind.TTS, name)


def _resolve_device_id(backend, requested: str | None) -> tuple[str, str]:
    """`--device-id` で指定された ID を解決する。

    - 指定あり: 一致するデバイスを返す。なければ list で警告 → ValueError。
    - 指定なし: 一覧の先頭(soundcard なら default speaker が先頭になる慣習)を返す。

    Returns: (device_id, display_name)
    """
    devices: list[OutputDevice] = backend.list_devices()
    if not devices:
        raise ValueError("出力デバイスが 1 つも見つかりません(soundcard が空)。")
    if requested is None or requested == "":
        head = devices[0]
        return head.device_id, head.display_name
    for d in devices:
        if d.device_id == requested:
            return d.device_id, d.display_name
    # 見つからない: 候補を併記してエラー
    names = "\n".join(f"  - {d.device_id}  {d.display_name}" for d in devices)
    raise ValueError(
        f"指定 device_id が見つかりません: {requested!r}\n候補:\n{names}"
    )


def _print_device_list(backend) -> None:
    """list_devices の結果を整形して stdout に出す。"""
    devices: list[OutputDevice] = backend.list_devices()
    if not devices:
        print("(出力デバイスなし)", file=sys.stderr)
        return
    print("# 出力デバイス一覧(先頭がデフォルト想定)")
    for i, d in enumerate(devices):
        marker = "* " if i == 0 else "  "
        print(f"{marker}{d.device_id}\t{d.display_name}")


# ============================================================
# 音源解決(tone / wav / text の三択)
# ============================================================
def _resolve_pcm(
    args: argparse.Namespace, registry: BackendRegistry, logger
) -> tuple[np.ndarray, int]:
    """argparse 引数から再生対象の (pcm, samplerate) を作る。

    優先順位:
      1. --text "..."  : TTS backend で合成
      2. --wav <path>  : WAV を読む
      3. --tone        : sine wave 自前生成(デフォルト)
    """
    if args.text:
        logger.info("TTS 合成: backend=%s text=%r", args.tts, args.text)
        tts = _create_tts_backend(registry, args.tts)
        # 軽く起動 / 入力を吸う backend は load 完了まで触らないのが安全(SAPI は即 LOADED)
        t0 = time.perf_counter()
        pcm, sr = tts.synthesize(args.text, args.tgt_lang)
        ms = (time.perf_counter() - t0) * 1000.0
        logger.info("合成完了: %.0f ms / %d samples @ %d Hz", ms, len(pcm), sr)
        return np.asarray(pcm, dtype=np.float32), int(sr)

    if args.wav is not None:
        logger.info("WAV 読み込み: %s", args.wav)
        pcm, sr = read_wav_as_float32_mono(args.wav)
        logger.info("WAV: %d samples @ %d Hz", len(pcm), sr)
        return pcm, sr

    # 既定: tone
    duration = args.tone_sec
    freq = args.tone_hz
    sr = args.tone_sr
    logger.info(
        "サイン波生成: %.1f Hz / %.2f sec / %d Hz サンプリング", freq, duration, sr
    )
    return make_tone(freq_hz=freq, duration_sec=duration, samplerate=sr)


# ============================================================
# argparse
# ============================================================
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m voice_translator.dev.runner_output",
        description=(
            "Output レイヤ単体ランナー。指定 backend / デバイスで PCM を再生する。"
            " AppController / PipelineCoordinator を介さず Output backend を直接叩く。"
        ),
    )
    p.add_argument(
        "--backend", default="soundcard",
        help="使用する Output backend 名(BackendRegistry に登録された名前)。",
    )
    p.add_argument(
        "--list-devices", action="store_true",
        help="使用 backend で見えるデバイスを列挙して終了する。",
    )
    p.add_argument(
        "--device-id", default=None,
        help=(
            "再生先デバイス ID(--list-devices で表示される左カラム)。"
            "省略時は backend が返す先頭デバイス(soundcard ならデフォルトスピーカ)を使う。"
        ),
    )

    src = p.add_mutually_exclusive_group()
    src.add_argument(
        "--tone", action="store_true",
        help="サイン波を再生する(デフォルト挙動。明示すると意図が分かりやすい)。",
    )
    src.add_argument(
        "--wav", type=Path, default=None,
        help="WAV ファイルを再生する。サンプルレートは WAV のものを使う。",
    )
    src.add_argument(
        "--text", default=None,
        help="指定テキストを TTS backend で合成して再生する(--tts も併用可)。",
    )

    # tone のサブパラメータ
    p.add_argument("--tone-hz", type=float, default=440.0, help="サイン波の周波数 [Hz]")
    p.add_argument(
        "--tone-sec", type=float, default=1.0, help="サイン波の長さ [秒]",
    )
    p.add_argument(
        "--tone-sr", type=int, default=44100, help="サイン波のサンプルレート [Hz]",
    )

    # TTS 経路パラメータ
    p.add_argument(
        "--tts", default="sapi",
        help="--text 指定時に使う TTS backend 名(BackendRegistry に登録された名前)。",
    )
    p.add_argument(
        "--tgt-lang", default="ja",
        help="--text 指定時に TTS へ渡す言語ヒント(SAPI のボイス選択用)。",
    )

    add_common_args(p)
    return p


# ============================================================
# 本体
# ============================================================
def run(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logger = setup_logger(args.verbose)

    # 1) backend 取得
    try:
        registry = _build_registry()
        backend = _create_output_backend(registry, args.backend)
    except Exception as exc:  # noqa: BLE001 - backend 生成失敗は致命扱い、ログだけ出して終了
        logger.error("Output backend の生成に失敗: backend=%s err=%s", args.backend, exc)
        return 2

    # 2) --list-devices ならここで終了
    if args.list_devices:
        try:
            _print_device_list(backend)
        except Exception as exc:  # noqa: BLE001
            logger.error("デバイス列挙に失敗: %s", exc)
            return 2
        return 0

    # 3) デバイス ID の解決
    try:
        device_id, display_name = _resolve_device_id(backend, args.device_id)
    except ValueError as exc:
        logger.error("%s", exc)
        return 2

    logger.info("出力デバイス: id=%s name=%s", device_id, display_name)

    # 4) 音源解決
    try:
        pcm, sr = _resolve_pcm(args, registry, logger)
    except Exception as exc:  # noqa: BLE001
        logger.exception("音源の解決に失敗: %s", exc)
        return 2

    if pcm is None or pcm.size == 0:
        logger.error("再生対象 PCM が空(TTS / WAV / tone のいずれかが 0 サンプル)")
        return 3

    # 5) 再生(start → play → stop)
    try:
        backend.start(device_id)
    except Exception as exc:  # noqa: BLE001
        logger.exception("backend.start に失敗: device_id=%s", device_id)
        return 4

    try:
        t0 = time.perf_counter()
        backend.play(pcm, sr)
        ms = (time.perf_counter() - t0) * 1000.0
        # play は同期再生(soundcard はブロッキング)。経過 ms ≒ 音の長さ。
        logger.info("再生完了: %.0f ms 経過(PCM = %d samples @ %d Hz)", ms, len(pcm), sr)
    except Exception as exc:  # noqa: BLE001
        logger.exception("backend.play に失敗")
        return 5
    finally:
        try:
            backend.stop()
        except Exception:  # noqa: BLE001
            logger.exception("backend.stop に失敗(無視)")

    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(run())
