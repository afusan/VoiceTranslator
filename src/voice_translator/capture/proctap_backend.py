"""ProcTapCaptureBackend: proc-tap を使ったプロセス単位の音声取得。

役割: 指定されたプロセス(PID)から **WASAPI Process Loopback(Windows)** で音声を
取り込み、VoiceTranslator 内部標準フォーマット(16kHz/mono/float32)に変換して
チャンクストリームを供給する。

proc-tap 側の出力フォーマットは **48000 Hz / stereo / float32 固定**(`STANDARD_*`)。
本 backend は以下の 2 段で内部標準に変換する:
1. stereo → mono(`np.mean(axis=1)`)
2. 48 kHz → 16 kHz(`scipy.signal.resample_poly(up=1, down=3)`)

`list_sources()` は段階 3 で `process_enumerator` に委譲する本実装に切り替えた。
WASAPI AudioSession の列挙ロジックは `capture/process_enumerator.py` に独立化して
おり、本 backend からはそれを呼ぶだけ(役割分離)。
"""

from __future__ import annotations

import logging

import numpy as np

from voice_translator.common.errors import FatalError
from voice_translator.common.types import (
    INTERNAL_SAMPLE_RATE,
    BackendCapabilities,
    CaptureKind,
    CaptureSource,
    ModelStatus,
    PcmChunk,
)

from .backend import AudioCaptureBackend


# proc-tap の出力は 48kHz stereo 固定。16kHz mono への変換比は ↓ 1/3。
_PROCTAP_RATE = 48_000
_PROCTAP_CHANNELS = 2
_RESAMPLE_UP = 1
_RESAMPLE_DOWN = _PROCTAP_RATE // INTERNAL_SAMPLE_RATE  # 48000 // 16000 = 3

logger = logging.getLogger(__name__)


def _convert_pcm(data: bytes) -> PcmChunk:
    """proc-tap の bytes(48kHz/2ch/float32) を 16kHz/mono/float32 ndarray に変換する。

    - 空バイトは長さ 0 の ndarray を返す(VAD 側で空チャンク扱いされる)。
    - stereo frame 単位での端数(1 サンプルだけ余る)はあり得ないが、防衛として切り捨てる。
    - リサンプルは `scipy.signal.resample_poly`(polyphase filter)。シンプルだが
      チャンクごとの境界アーチファクトは僅か残る。チャンクが充分大きければ実用上問題ない。
    """
    if not data:
        return np.zeros(0, dtype=np.float32)
    arr = np.frombuffer(data, dtype=np.float32)
    # stereo 整合: 2 で割り切れない端数があれば切り捨てる(防衛)
    n_stereo_samples = (arr.size // _PROCTAP_CHANNELS) * _PROCTAP_CHANNELS
    if n_stereo_samples == 0:
        return np.zeros(0, dtype=np.float32)
    stereo = arr[:n_stereo_samples].reshape(-1, _PROCTAP_CHANNELS)
    mono = stereo.mean(axis=1)
    # scipy は遅延 import(モジュール読み込み時の重さを避ける)
    from scipy.signal import resample_poly  # type: ignore[import-not-found]

    resampled = resample_poly(mono, up=_RESAMPLE_UP, down=_RESAMPLE_DOWN)
    return resampled.astype(np.float32, copy=False)


class ProcTapCaptureBackend(AudioCaptureBackend):
    """proc-tap ベースのプロセス単位 AudioCaptureBackend。

    取得単位は `CaptureKind.PROCESS`。`source_id` は **PID を文字列化した値**
    (例: `"1234"`)。`start()` 内で `int(source_id)` で PID を取り出して
    `ProcessAudioCapture` を構築する。

    段階 2 では `list_sources()` は空リスト仮実装(段階 3 で `pycaw` 連携で
    「音声出力中のプロセス」を列挙する)。
    """

    @classmethod
    def capture_kind(cls) -> CaptureKind:
        return CaptureKind.PROCESS

    def __init__(
        self,
        *,
        resample_quality: str = "best",
    ) -> None:
        """コンストラクタ。proc-tap は遅延 import で依存未インストール環境でも
        backend クラス自体は import 可能にする(`capture_kind` 等を問い合わせるため)。

        実 capture を始めるには `start(pid_str)` を呼ぶ必要がある。
        """
        super().__init__()
        # `resample_quality` は proc-tap 内部のリサンプル(WASAPI が int16 で返す稀な
        # ケースの float32 変換時に使われる)。本 backend は最終的に 48k→16k を自前で
        # 実装するため、ここは proc-tap 側の品質指定のみ。
        self._resample_quality = resample_quality
        self._tap = None  # 型: proctap.ProcessAudioCapture | None。実体は遅延構築
        # 起動可否は proc-tap が import できるかで決まる
        try:
            import proctap  # noqa: F401 - 存在確認のみ
        except ImportError as e:
            self._set_status(ModelStatus.NOT_DOWNLOADED)
            raise FatalError(
                "proc-tap がインストールされていません。"
                "`uv sync --extra capture-proctap` で追加してください。",
                cause=e,
            ) from e
        # 依存 OK。デバイス/プロセスは start() で開く。
        self._set_status(ModelStatus.LOADED)

    # ----------------------------------------------------------
    def list_sources(self) -> list[CaptureSource]:
        """音声出力中のプロセスを列挙して `CaptureSource` のリストを返す(段階 3)。

        列挙ロジック自体は `capture/process_enumerator.py` に独立化されている
        (WASAPI セッション列挙は ProcTap 固有でなく Windows 共通機能のため)。
        本 backend はそれを呼ぶだけ。

        ※ ConfigStore に PID を残しても再起動で無効化される(A-7 確定方針)ので、
        UI 側は毎回プロセス選択ダイアログから選び直す前提。本メソッドは GUI から
        「現在使えるプロセス」を見せるためのソース。
        """
        try:
            from . import process_enumerator as pe
            return pe.enumerate_active_processes()
        except ImportError:
            # pycaw / psutil 未インストール時(extras 未導入で proc-tap だけがある等の
            # 異常パス)。本クラスの __init__ で proc-tap の有無は確認済みだが、
            # pycaw / psutil は process_enumerator が遅延 import するため、ここで吸収。
            logger.exception("process_enumerator の依存(pycaw / psutil)が未インストール")
            return []
        except Exception:
            # WASAPI 列挙時の COM 例外等。UI を壊さないため空リストにフォールバック。
            logger.exception("list_sources() の列挙中に例外(空リストで縮退)")
            return []

    # ----------------------------------------------------------
    def start(self, source_id: str) -> None:
        """指定 PID(文字列)からのキャプチャを開始する。

        - source_id は PID の文字列化。`int(source_id)` で整数に変換する。
        - 変換失敗 / proc-tap の起動失敗は FatalError。
        """
        if self._tap is not None:
            raise RuntimeError("既に start 済みです。先に stop してください。")

        try:
            pid = int(str(source_id).strip())
        except (TypeError, ValueError) as e:
            raise FatalError(
                f"プロセス ID として解釈できません: {source_id!r}", cause=e,
            ) from e

        try:
            from proctap import ProcessAudioCapture  # 遅延 import

            self._tap = ProcessAudioCapture(
                pid=pid, resample_quality=self._resample_quality,
            )
            self._tap.start()
        except FatalError:
            raise
        except Exception as e:  # noqa: BLE001 - WASAPI 起動失敗等は FATAL に分類
            self._tap = None
            raise FatalError(
                f"プロセス {pid} の音声取得開始に失敗: {e}", cause=e,
            ) from e

    # ----------------------------------------------------------
    def read_chunk(self, timeout: float = 0.1) -> PcmChunk | None:
        """proc-tap から bytes を取得し、16kHz/mono/float32 に変換して返す。

        - データ無し(timeout)時は None を返す(soundcard_backend と同じ規約)。
        - 空 bytes が返ってきた場合も None 扱い。
        - read / 変換時の例外は FatalError(デバイス消失や ProcTap 内部例外)。
        """
        if self._tap is None:
            raise RuntimeError("start() を呼んでから read_chunk() してください")
        try:
            data = self._tap.read(timeout=timeout)
        except Exception as e:  # noqa: BLE001
            raise FatalError(f"プロセス音声取得に失敗: {e}", cause=e) from e
        if not data:
            return None
        try:
            return _convert_pcm(data)
        except Exception as e:  # noqa: BLE001 - 変換失敗は致命扱い(継続不能)
            raise FatalError(
                f"音声フォーマット変換に失敗: {e}", cause=e,
            ) from e

    # ----------------------------------------------------------
    def stop(self) -> None:
        """capture を閉じる。複数回呼ばれても安全。"""
        if self._tap is not None:
            try:
                self._tap.stop()
            except Exception:  # noqa: BLE001 - クローズ時の例外は握りつぶす
                logger.exception("ProcTap.stop で例外(無視)")
        self._tap = None

    # ----------------------------------------------------------
    def capabilities(self) -> BackendCapabilities:
        return BackendCapabilities(
            supported_languages=(),
            requires_gpu=False,
            is_cloud=False,
            requires_credentials=False,
            notes=(
                "proc-tap (per-process audio capture)。Windows = WASAPI Process Loopback。"
                "出力 48kHz/2ch/float32 を内部で 16kHz/mono に変換する。"
            ),
        )
