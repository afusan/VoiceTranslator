"""StageDumpWriter: パイプラインのステージ間データをディスクに書き出すフック。

役割: `pipeline.dump.enabled=true` のとき PipelineCoordinator から呼ばれ、
各ステージ(vad / asr / translate / tts)の出力を
`<dump_dir>/<run_id>/seq_NNNN_<stage>.{wav,json}` に書き出す。
書き込みは内部の単一ワーカスレッドで非同期に行い、本体パイプラインを止めない。

無効時は `NullStageDumpWriter`(全メソッド no-op)を注入することで、
PipelineCoordinator 側の分岐を増やさずに済む(Null Object パターン)。

ダンプの目的:
- 実機動作で発生した問題のあるデータ(ASRが崩れた発話、TTSが暴走した翻訳テキスト 等)を
  後から `voice_translator.dev` の単体ランナーで再現・突き合わせる入力にする。

データ形式の規約は docs/design/feature-dev-runners-and-dump/Plan.md §6 を参照。
"""

from __future__ import annotations

import json
import logging
import queue
import threading
import time
import wave
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Iterable

import numpy as np


# 書き出し対象のステージ識別子。Plan.md §6 の規約と一致させる。
_VALID_STAGES: frozenset[str] = frozenset({"vad", "asr", "translate", "tts"})

# ワーカスレッドへの停止合図
_SENTINEL: object = object()


class NullStageDumpWriter:
    """役割: StageDumpWriter の no-op 実装(Null Object)。

    `pipeline.dump.enabled=false` のときに PipelineCoordinator に注入する。
    全メソッドが即 return するので、本体パイプラインのオーバーヘッドはほぼゼロ。
    """

    def start_run(self, meta: dict[str, Any] | None = None) -> None:  # noqa: D401
        return

    def stop_run(self, *, join_timeout: float = 2.0) -> None:
        return

    def on_vad(self, seq_id: int, pcm: Any, samplerate: int) -> None:
        return

    def on_asr(self, seq_id: int, text: str, src_lang: str) -> None:
        return

    def on_translate(
        self,
        seq_id: int,
        src_text: str,
        src_lang: str,
        tgt_text: str,
        tgt_lang: str,
    ) -> None:
        return

    def on_tts(self, seq_id: int, pcm: Any, samplerate: int) -> None:
        return


class StageDumpWriter:
    """役割: 各ステージのデータを `<dump_dir>/<run_id>/` に書き出す。

    使い方:
        writer = StageDumpWriter(dump_dir="./logs/dumps", stages={"asr", "translate"})
        writer.start_run({"backends": {...}, ...})
        # ... PipelineCoordinator が on_* を呼ぶ ...
        writer.stop_run()
    """

    def __init__(
        self,
        *,
        dump_dir: Path | str,
        stages: Iterable[str] = ("vad", "asr", "translate", "tts"),
        max_runs: int = 20,
        logger: logging.Logger | None = None,
        run_id_factory: Callable[[], str] | None = None,
    ) -> None:
        self._dump_dir = Path(dump_dir)
        self._stages: set[str] = {s for s in stages if s in _VALID_STAGES}
        self._max_runs = max(0, int(max_runs))
        self._logger = logger or logging.getLogger("voice_translator")
        self._run_id_factory = run_id_factory or _default_run_id

        # run スコープの状態(start_run でセット、stop_run でクリア)
        self._run_dir: Path | None = None
        self._queue: queue.Queue[Any] = queue.Queue()
        self._worker: threading.Thread | None = None
        self._lock = threading.Lock()

    @property
    def stages(self) -> frozenset[str]:
        """このライタが書き出し対象とするステージ(診断/テスト用)。"""
        return frozenset(self._stages)

    @property
    def run_dir(self) -> Path | None:
        """現在の run ディレクトリ(start_run 〜 stop_run の間だけ非 None)。"""
        return self._run_dir

    # ============================================================
    # ライフサイクル
    # ============================================================
    def start_run(self, meta: dict[str, Any] | None = None) -> None:
        """新しい run_id ディレクトリを作成し、ワーカスレッドを起動する。

        多重 start は許容(既に走行中なら no-op + 警告ログ)。
        """
        with self._lock:
            if self._worker is not None and self._worker.is_alive():
                self._logger.warning("StageDumpWriter は既に走行中です(start_run 二重呼び)")
                return
            self._dump_dir.mkdir(parents=True, exist_ok=True)
            self._prune_old_runs()

            run_id = self._run_id_factory()
            run_dir = self._dump_dir / run_id
            run_dir.mkdir(parents=True, exist_ok=True)
            self._run_dir = run_dir

            run_meta = {
                "run_id": run_id,
                "started_at": _iso_now(),
                **(meta or {}),
            }
            try:
                (run_dir / "run.json").write_text(
                    json.dumps(run_meta, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
            except OSError:
                self._logger.exception("run.json の書き出しに失敗")

            # 残骸キューをクリアしてワーカ起動
            while True:
                try:
                    self._queue.get_nowait()
                except queue.Empty:
                    break
            self._worker = threading.Thread(
                target=self._worker_loop, name="vt_stage_dump", daemon=True
            )
            self._worker.start()

    def stop_run(self, *, join_timeout: float = 2.0) -> None:
        """ワーカに sentinel を投入して join し、run スコープを終了する。"""
        with self._lock:
            worker = self._worker
            if worker is None:
                return
            self._queue.put(_SENTINEL)
        worker.join(timeout=join_timeout)
        with self._lock:
            self._worker = None
            self._run_dir = None

    # ============================================================
    # フック(PipelineCoordinator から呼ばれる)
    # ============================================================
    def on_vad(self, seq_id: int, pcm: Any, samplerate: int) -> None:
        if "vad" not in self._stages or self._run_dir is None:
            return
        path = self._run_dir / f"seq_{seq_id:04d}_vad.wav"
        self._enqueue(lambda: _write_wav(path, pcm, samplerate))

    def on_asr(self, seq_id: int, text: str, src_lang: str) -> None:
        if "asr" not in self._stages or self._run_dir is None:
            return
        path = self._run_dir / f"seq_{seq_id:04d}_asr.json"
        payload = {
            "seq_id": seq_id,
            "stage": "asr",
            "produced_at": _iso_now(),
            "src_lang": src_lang,
            "text": text,
        }
        self._enqueue(lambda: _write_json(path, payload))

    def on_translate(
        self,
        seq_id: int,
        src_text: str,
        src_lang: str,
        tgt_text: str,
        tgt_lang: str,
    ) -> None:
        if "translate" not in self._stages or self._run_dir is None:
            return
        path = self._run_dir / f"seq_{seq_id:04d}_translate.json"
        payload = {
            "seq_id": seq_id,
            "stage": "translate",
            "produced_at": _iso_now(),
            "src_lang": src_lang,
            "tgt_lang": tgt_lang,
            "src_text": src_text,
            "tgt_text": tgt_text,
        }
        self._enqueue(lambda: _write_json(path, payload))

    def on_tts(self, seq_id: int, pcm: Any, samplerate: int) -> None:
        if "tts" not in self._stages or self._run_dir is None:
            return
        path = self._run_dir / f"seq_{seq_id:04d}_tts.wav"
        self._enqueue(lambda: _write_wav(path, pcm, samplerate))

    # ============================================================
    # 内部
    # ============================================================
    def _enqueue(self, task: Callable[[], None]) -> None:
        if self._worker is None or not self._worker.is_alive():
            # start_run 呼び忘れ。WARN を出すが本体は止めない。
            self._logger.warning(
                "StageDumpWriter: worker not running, dropping a write task"
            )
            return
        self._queue.put(task)

    def _worker_loop(self) -> None:
        while True:
            try:
                item = self._queue.get(timeout=0.5)
            except queue.Empty:
                continue
            if item is _SENTINEL:
                return
            try:
                item()
            except Exception:  # noqa: BLE001 - 書き出し失敗で本体を止めない
                self._logger.exception("stage dump 書き出しに失敗")

    def _prune_old_runs(self) -> None:
        """`max_runs` を超えた古い run_id ディレクトリを削除する。

        既存ディレクトリのうち `seq_*` または `run.json` を含むものを「過去の run」とみなす。
        ファイル名(タイムスタンプ)順で並べ替えて古い方から削除。`max_runs=0` で無効。
        """
        if self._max_runs <= 0:
            return
        try:
            children = sorted(
                [p for p in self._dump_dir.iterdir() if p.is_dir()],
                key=lambda p: p.name,
            )
        except OSError:
            return
        runs = [p for p in children if _looks_like_run_dir(p)]
        # これから 1 つ作るので max_runs - 1 まで残す
        excess = len(runs) - (self._max_runs - 1)
        if excess <= 0:
            return
        for old in runs[:excess]:
            try:
                _rmtree(old)
            except OSError:
                self._logger.exception("古い dump ディレクトリの削除に失敗: %s", old)


# ============================================================
# モジュール内ユーティリティ
# ============================================================
def _iso_now() -> str:
    return datetime.now().isoformat(timespec="milliseconds")


def _default_run_id() -> str:
    # 秒精度 + ナノ秒の下 4 桁で衝突回避
    now = datetime.now()
    suffix = f"{time.perf_counter_ns() % 10000:04d}"
    return now.strftime("%Y%m%d-%H%M%S") + "-" + suffix


def _looks_like_run_dir(p: Path) -> bool:
    try:
        if (p / "run.json").is_file():
            return True
        for child in p.iterdir():
            if child.name.startswith("seq_"):
                return True
    except OSError:
        return False
    return False


def _rmtree(path: Path) -> None:
    """`pathlib` ベースの簡易 rmtree(古い run ディレクトリ削除専用)。"""
    if path.is_file() or path.is_symlink():
        path.unlink(missing_ok=True)
        return
    for child in path.iterdir():
        _rmtree(child)
    path.rmdir()


def _write_wav(path: Path, pcm: Any, samplerate: int) -> None:
    """float32 / int16 / その他 numpy 配列を mono int16 WAV として書き出す。

    プロジェクト内部標準(16kHz/mono/float32)に従いつつ、WAV ファイルとしては
    int16 で保存する(stdlib `wave` は float32 未サポート、互換性も int16 が無難)。
    リプレイ時は `WavReplayCapture.from_wav` 等で float32 に戻る。
    """
    arr = np.asarray(pcm)
    if arr.ndim > 1:
        # マルチチャネルが来たらモノラルに畳む(平均)。
        arr = arr.mean(axis=tuple(range(1, arr.ndim)))
    if np.issubdtype(arr.dtype, np.floating):
        clipped = np.clip(arr, -1.0, 1.0)
        i16 = (clipped * 32767.0).astype(np.int16)
    elif arr.dtype == np.int16:
        i16 = arr
    else:
        i16 = arr.astype(np.int16, copy=False)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(int(samplerate))
        wf.writeframes(i16.tobytes())


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
