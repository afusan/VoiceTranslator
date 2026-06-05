"""ProcTapCaptureBackend(段階 2)のテスト。

small:
- capture_kind() == PROCESS
- list_sources() は空(段階 3 まで)
- _convert_pcm の PCM 変換挙動(stereo→mono / 48k→16k / 空入力 / 端数)
- start で source_id を int() に変換
- start で int 変換失敗 → FatalError
- start/stop のライフサイクル(モック ProcessAudioCapture で観察)
- read_chunk が proc-tap の bytes を変換する
- read_chunk(None) → None
- start 前 read_chunk → RuntimeError
- 二重 start → RuntimeError
- stop は冪等

large(`@pytest.mark.large`):
- 実 proc-tap を import して、Python プロセス自身の PID で start → read_chunk → stop
- 本テストは手動実行向け(`pytest -m large`)。CI には載せない方針。
"""

from __future__ import annotations

import importlib
import importlib.util
import sys
from unittest.mock import MagicMock

import numpy as np
import pytest

from voice_translator.common.errors import FatalError
from voice_translator.common.types import CaptureKind


PROCTAP_INSTALLED = importlib.util.find_spec("proctap") is not None


# ============================================================
# small: 振る舞いテスト(モック)
# ============================================================
@pytest.mark.skipif(
    not PROCTAP_INSTALLED,
    reason="proc-tap 未インストール環境では import レベルで FatalError",
)
class TestCaptureKindAndListSources:
    def test_capture_kind_is_process(self) -> None:
        from voice_translator.capture.proctap_backend import ProcTapCaptureBackend

        assert ProcTapCaptureBackend.capture_kind() == CaptureKind.PROCESS

    def test_list_sources_is_empty_until_stage3(self) -> None:
        from voice_translator.capture.proctap_backend import ProcTapCaptureBackend

        backend = ProcTapCaptureBackend()
        assert backend.list_sources() == []


# ============================================================
# small: _convert_pcm(stereo→mono / 48k→16k / 端数 / 空)
# ============================================================
@pytest.mark.skipif(
    not PROCTAP_INSTALLED,
    reason="scipy が依存に含まれる proc-tap が未インストールだとリサンプル不可",
)
class TestConvertPcm:
    def test_empty_bytes_returns_empty_array(self) -> None:
        from voice_translator.capture.proctap_backend import _convert_pcm

        out = _convert_pcm(b"")
        assert isinstance(out, np.ndarray)
        assert out.size == 0
        assert out.dtype == np.float32

    def test_downsample_ratio_is_three_to_one(self) -> None:
        """48kHz → 16kHz は 3:1。入力 N stereo frame → 出力 ~N/3 mono frame。"""
        from voice_translator.capture.proctap_backend import _convert_pcm

        # 3000 stereo frame (=6000 float32 値) = 0.0625 秒分 @48kHz
        n_frames = 3000
        # 全 0 でも OK(リサンプル後の長さは入力比例)
        arr = np.zeros(n_frames * 2, dtype=np.float32)
        out = _convert_pcm(arr.tobytes())
        # resample_poly(up=1, down=3) なので出力 ≈ ceil(N/3)
        # 厳密には scipy のフィルタ長で +α する場合があるが、概ね 1000 ± 10
        assert 990 <= out.size <= 1010
        assert out.dtype == np.float32

    def test_stereo_is_averaged_to_mono(self) -> None:
        """左右で平均化。L=1.0、R=-1.0 を 1500 frame 並べると mono は 0 に近づく。"""
        from voice_translator.capture.proctap_backend import _convert_pcm

        n_frames = 1500
        stereo = np.zeros((n_frames, 2), dtype=np.float32)
        stereo[:, 0] = 1.0
        stereo[:, 1] = -1.0
        out = _convert_pcm(stereo.flatten().tobytes())
        # 平均 0 のはず(リサンプルのフィルタ通すと境界は揺らぐが中央は 0 付近)
        # 全体平均で |x| < 0.05 を期待(過剰な精度は求めない)
        assert abs(out.mean()) < 0.05

    def test_odd_size_buffer_is_truncated(self) -> None:
        """stereo として 1 サンプル余る場合(理論上ありえない)、切り捨てて続行する。"""
        from voice_translator.capture.proctap_backend import _convert_pcm

        # 100 stereo frame + 1 余り = 201 float32
        arr = np.zeros(201, dtype=np.float32)
        out = _convert_pcm(arr.tobytes())
        # 端数切り捨て後でも例外を出さず、ndarray を返す
        assert isinstance(out, np.ndarray)
        assert out.dtype == np.float32


# ============================================================
# small: start / stop / read_chunk のライフサイクル(モック proc-tap)
# ============================================================
@pytest.mark.skipif(
    not PROCTAP_INSTALLED,
    reason="proc-tap 未インストール環境では __init__ で FatalError",
)
class TestLifecycleWithMockedProcTap:
    def _install_fake_proctap(self, monkeypatch) -> MagicMock:
        """`proctap.ProcessAudioCapture` をモック化して install。

        ProcTapCaptureBackend は `from proctap import ProcessAudioCapture` を遅延 import
        するため、`proctap.ProcessAudioCapture` 属性を差し替えれば start 内で拾われる。
        """
        import proctap

        fake_cls = MagicMock(name="ProcessAudioCapture")
        fake_instance = MagicMock(name="instance")
        fake_cls.return_value = fake_instance
        monkeypatch.setattr(proctap, "ProcessAudioCapture", fake_cls, raising=False)
        return fake_instance, fake_cls

    def test_start_passes_int_pid(self, monkeypatch) -> None:
        from voice_translator.capture.proctap_backend import ProcTapCaptureBackend

        instance, fake_cls = self._install_fake_proctap(monkeypatch)
        backend = ProcTapCaptureBackend()
        backend.start("1234")
        # int 化されて pid に渡る
        kwargs = fake_cls.call_args.kwargs
        assert kwargs.get("pid") == 1234
        instance.start.assert_called_once()

    def test_start_with_non_integer_source_id_raises_fatal(self, monkeypatch) -> None:
        from voice_translator.capture.proctap_backend import ProcTapCaptureBackend

        self._install_fake_proctap(monkeypatch)
        backend = ProcTapCaptureBackend()
        with pytest.raises(FatalError):
            backend.start("not-a-pid")

    def test_start_when_proctap_raises_becomes_fatal(self, monkeypatch) -> None:
        from voice_translator.capture.proctap_backend import ProcTapCaptureBackend

        import proctap

        def _broken(*args, **kwargs):
            raise RuntimeError("WASAPI failed")

        monkeypatch.setattr(proctap, "ProcessAudioCapture", _broken, raising=False)
        backend = ProcTapCaptureBackend()
        with pytest.raises(FatalError):
            backend.start("1234")
        # 失敗後 _tap は None に戻る(再 start 可)
        assert backend._tap is None  # noqa: SLF001

    def test_double_start_raises_runtime_error(self, monkeypatch) -> None:
        from voice_translator.capture.proctap_backend import ProcTapCaptureBackend

        self._install_fake_proctap(monkeypatch)
        backend = ProcTapCaptureBackend()
        backend.start("1234")
        with pytest.raises(RuntimeError):
            backend.start("5678")

    def test_read_chunk_before_start_raises_runtime_error(self, monkeypatch) -> None:
        from voice_translator.capture.proctap_backend import ProcTapCaptureBackend

        backend = ProcTapCaptureBackend()
        with pytest.raises(RuntimeError):
            backend.read_chunk(timeout=0.1)

    def test_read_chunk_converts_bytes(self, monkeypatch) -> None:
        """proc-tap が返した 48kHz stereo float32 を 16kHz mono に変換して返す。"""
        from voice_translator.capture.proctap_backend import ProcTapCaptureBackend

        instance, _ = self._install_fake_proctap(monkeypatch)
        # 600 stereo frame の正弦波(0.0125 秒分)
        n_frames = 600
        stereo = np.zeros((n_frames, 2), dtype=np.float32)
        stereo[:, 0] = 0.1
        stereo[:, 1] = 0.1
        instance.read.return_value = stereo.flatten().tobytes()

        backend = ProcTapCaptureBackend()
        backend.start("1234")
        chunk = backend.read_chunk(timeout=0.1)
        assert chunk is not None
        assert chunk.dtype == np.float32
        # 600 → 200 ± α サンプル
        assert 190 <= chunk.size <= 210

    def test_read_chunk_returns_none_on_empty(self, monkeypatch) -> None:
        from voice_translator.capture.proctap_backend import ProcTapCaptureBackend

        instance, _ = self._install_fake_proctap(monkeypatch)
        instance.read.return_value = b""
        backend = ProcTapCaptureBackend()
        backend.start("1234")
        assert backend.read_chunk(timeout=0.1) is None

    def test_read_chunk_raises_fatal_on_proctap_error(self, monkeypatch) -> None:
        from voice_translator.capture.proctap_backend import ProcTapCaptureBackend

        instance, _ = self._install_fake_proctap(monkeypatch)
        instance.read.side_effect = RuntimeError("device lost")
        backend = ProcTapCaptureBackend()
        backend.start("1234")
        with pytest.raises(FatalError):
            backend.read_chunk(timeout=0.1)

    def test_stop_is_idempotent(self, monkeypatch) -> None:
        from voice_translator.capture.proctap_backend import ProcTapCaptureBackend

        instance, _ = self._install_fake_proctap(monkeypatch)
        backend = ProcTapCaptureBackend()
        backend.start("1234")
        backend.stop()
        backend.stop()  # 2 回目も例外なし
        # 1 回目で proc-tap.stop が呼ばれている
        instance.stop.assert_called_once()


# ============================================================
# small: extras 未インストール時の振る舞い
# ============================================================
class TestProctapMissing:
    """`proctap` モジュールが import できないときの挙動。"""

    def test_init_raises_fatal_when_proctap_missing(self, monkeypatch) -> None:
        """`proctap` を import できない状態をシミュレートして FatalError 発生を確認。"""
        # 既存 proctap モジュールがあれば隠す
        monkeypatch.setitem(sys.modules, "proctap", None)
        # キャッシュをクリアして再 import を試みる
        from voice_translator.capture.proctap_backend import ProcTapCaptureBackend

        with pytest.raises(FatalError):
            ProcTapCaptureBackend()


# ============================================================
# large: 実 proc-tap で Python 自身を録音(手動実行のみ)
# ============================================================
@pytest.mark.large
@pytest.mark.skipif(
    not PROCTAP_INSTALLED,
    reason="proc-tap 未インストール環境では large テストもスキップ",
)
class TestProcTapLargeSelfCapture:
    """実 proc-tap で Python 自身の PID から録音を試みる large テスト。

    - 自分自身のプロセスが音を出していなくても、`start` / `read_chunk` / `stop` の
      ライフサイクルが例外なく回ることだけ確認する。
    - 戻り値の chunk は無音(全 0)or None でも OK。フォーマット(dtype/形状)は確認する。
    - 手動実行: `py -m uv run pytest -m large -k ProcTapLargeSelfCapture`
    """

    def test_lifecycle_with_real_proctap(self) -> None:
        import os
        import time

        from voice_translator.capture.proctap_backend import ProcTapCaptureBackend

        backend = ProcTapCaptureBackend()
        backend.start(str(os.getpid()))
        try:
            # 起動直後はバッファが空のことが多いので、少し待ってから read
            time.sleep(0.2)
            got_any_chunk = False
            for _ in range(5):
                chunk = backend.read_chunk(timeout=0.3)
                if chunk is None:
                    continue
                got_any_chunk = True
                assert chunk.dtype == np.float32
                assert chunk.ndim == 1
                break
            # 無音でも例外無く戻ってくることが本テストの目的(got_any_chunk は best-effort)
        finally:
            backend.stop()
