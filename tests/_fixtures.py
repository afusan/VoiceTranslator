"""テスト用の共通ヘルパ。

役割: WAV ファイルから PCM を読んで AudioCaptureBackend として振る舞う
WavReplayCapture 等、E2E テストで使う再現可能な部品を集約する。

Phase D で追加: keyring の test double(`InMemoryKeyring` / `FailKeyring`)。
実 keychain に触らずに credentials のテストを書くために使う。R-5 で「テスト時は
実 keyring を触らない」方針。
"""

from __future__ import annotations

import wave
from pathlib import Path
from time import monotonic
from typing import Iterable

import numpy as np

from voice_translator.capture.backend import AudioCaptureBackend
from voice_translator.common.types import (
    INTERNAL_CHANNELS,
    INTERNAL_SAMPLE_RATE,
    CaptureSource,
    PcmChunk,
)


# ============================================================
# keyring 用 test double(Phase D / R-5)
# ============================================================
# 実際の `KeyringBackend` を import して継承することで `keyring.set_keyring` の型検査を通す。
# import 失敗(keyring 未インストール)時はクラス全体を None にし、テスト側で xfail を出す。
try:
    from keyring.backend import KeyringBackend as _KeyringBackend  # type: ignore
    import keyring.errors as _keyring_errors  # type: ignore
except Exception:  # noqa: BLE001
    _KeyringBackend = None  # type: ignore
    _keyring_errors = None  # type: ignore


if _KeyringBackend is not None:

    class InMemoryKeyring(_KeyringBackend):  # type: ignore[misc]
        """インメモリの keyring 実装(`keyring.set_keyring` に注入して使う)。

        実 OS keychain に触らず、テスト用に独立した password ストアを提供する。
        """

        priority = 1.0  # type: ignore[assignment]

        def __init__(self) -> None:
            super().__init__()
            self._store: dict[tuple[str, str], str] = {}

        def get_password(self, service: str, username: str) -> str | None:
            return self._store.get((service, username))

        def set_password(self, service: str, username: str, password: str) -> None:
            self._store[(service, username)] = password

        def delete_password(self, service: str, username: str) -> None:
            try:
                del self._store[(service, username)]
            except KeyError as e:
                raise _keyring_errors.PasswordDeleteError("no such password") from e


    class FailKeyring(_KeyringBackend):  # type: ignore[misc]
        """全操作が失敗する keyring 実装。

        credentials の例外握り + 平文ファイル fallback が機能するかを検証する。
        """

        priority = 1.0  # type: ignore[assignment]

        def get_password(self, service: str, username: str) -> str | None:  # noqa: ARG002
            raise RuntimeError("keyring unavailable")

        def set_password(self, service: str, username: str, password: str) -> None:  # noqa: ARG002
            raise RuntimeError("keyring unavailable")

        def delete_password(self, service: str, username: str) -> None:  # noqa: ARG002
            raise RuntimeError("keyring unavailable")

else:

    class InMemoryKeyring:  # type: ignore[no-redef]
        """keyring 未インストールのため使えないスタブ。"""

        def __init__(self) -> None:  # pragma: no cover
            raise RuntimeError("keyring がインストールされていません")


    class FailKeyring:  # type: ignore[no-redef]
        def __init__(self) -> None:  # pragma: no cover
            raise RuntimeError("keyring がインストールされていません")


class WavReplayCapture(AudioCaptureBackend):
    """ファイルWAV(または numpy 配列)を順に chunk_size ごとに返す Capture。

    役割: 実機マイク/スピーカに依存せず、決定論的な入力でパイプラインを駆動する。
    内部標準形式 (16kHz/mono/float32) と異なる WAV はリサンプルしない(テスト前提)。
    """

    def __init__(
        self,
        pcm: np.ndarray | None = None,
        *,
        chunk_size: int = 512,
        source_id: str = "wav_replay",
        display_name: str = "WAV Replay",
        loop: bool = False,
    ) -> None:
        """`loop=True` で PCM を使い切ったら先頭から再生し続ける。

        用途: 「動作中に設定を変える」系のテスト。再生は実時間ペーシング無しの
        全速で進むため、有限 PCM だと負荷次第で「設定変更前に全発話が処理済み」
        というレースが起きる。ループ再生なら変更後の発話が必ず存在する。
        """
        if pcm is None:
            pcm = np.zeros(0, dtype=np.float32)
        self._pcm = pcm.astype(np.float32, copy=False)
        self._chunk_size = chunk_size
        self._source_id = source_id
        self._display_name = display_name
        self._loop = loop
        self._pos = 0
        self._started = False

    @classmethod
    def from_wav(cls, path: Path | str, **kwargs) -> "WavReplayCapture":
        """16kHz/mono/16bit のWAVを読み込んで PCM 化する(他フォーマットも一応対応)。"""
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
            raise ValueError(f"未対応のWAVサンプル幅: {sampwidth}")
        if nch == 2 and pcm.size % 2 == 0:
            pcm = pcm.reshape(-1, 2).mean(axis=1)
        if sr != INTERNAL_SAMPLE_RATE:
            # テスト用途では同一レートのWAVを使う前提だが、警告ぐらいは出したい場面もあるかも
            pass
        return cls(pcm, **kwargs)

    # ---- AudioCaptureBackend I/F ----
    def list_sources(self) -> list[CaptureSource]:
        return [CaptureSource(self._source_id, self._display_name)]

    def start(self, source_id: str) -> None:
        if source_id != self._source_id:
            raise RuntimeError(f"unknown source_id: {source_id}")
        self._pos = 0
        self._started = True

    def stop(self) -> None:
        self._started = False

    def read_chunk(self, timeout: float = 0.1) -> PcmChunk | None:
        if not self._started:
            raise RuntimeError("start() を先に呼んでください")
        if self._pos >= self._pcm.size:
            if self._loop and self._pcm.size > 0:
                # ループ再生: 先頭に巻き戻して続行
                self._pos = 0
            else:
                # データが尽きたら待ち時間相当だけ寝て None を返す(Coordinator 側の停止チェックに譲る)
                from time import sleep
                sleep(timeout)
                return None
        end = min(self._pos + self._chunk_size, self._pcm.size)
        chunk = self._pcm[self._pos:end]
        self._pos = end
        # 端数が出ても返す(VAD 側で 512 単位に切られる)
        return chunk
