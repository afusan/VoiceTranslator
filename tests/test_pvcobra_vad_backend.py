"""PvcobraVadBackend の単体テスト。pvcobra モジュールを完全モック化。

ヒステリシス検出 + credential_spec + verify_credentials + access_key 無し時の MISSING_CREDENTIALS
状態を検証。
"""

from __future__ import annotations

import sys
from unittest.mock import MagicMock

import numpy as np
import pytest

from voice_translator.common.errors import FatalError
from voice_translator.common.types import ModelStatus
from voice_translator.vad.backend import VadSegment


@pytest.fixture()
def fake_pvcobra(monkeypatch):
    """`pvcobra` モジュールを差し替える。

    `create(access_key=...)` が `process(pcm: list[int]) -> float` を持つインスタンスを返す。
    """
    fake_module = MagicMock(name="pvcobra")
    fake_inst = MagicMock(name="cobra_inst")
    fake_inst.process = MagicMock(return_value=0.0)
    fake_inst.delete = MagicMock()
    fake_module.create = MagicMock(return_value=fake_inst)
    monkeypatch.setitem(sys.modules, "pvcobra", fake_module)
    return fake_module, fake_inst


# ============================================================
# 初期化 / 認証
# ============================================================
class TestInitialization:
    def test_missing_access_key_sets_missing_credentials(self) -> None:
        from voice_translator.vad.pvcobra_backend import PvcobraVadBackend

        backend = PvcobraVadBackend(access_key=None)
        assert backend.get_status() == ModelStatus.MISSING_CREDENTIALS

    def test_with_access_key_loads(self, fake_pvcobra) -> None:
        from voice_translator.vad.pvcobra_backend import PvcobraVadBackend

        fake_module, _ = fake_pvcobra
        backend = PvcobraVadBackend(access_key="ak-good")
        assert backend.get_status() == ModelStatus.LOADED
        fake_module.create.assert_called_once_with(access_key="ak-good")

    def test_create_failure_raises_fatal(self, fake_pvcobra) -> None:
        from voice_translator.vad.pvcobra_backend import PvcobraVadBackend

        fake_module, _ = fake_pvcobra
        fake_module.create.side_effect = RuntimeError("invalid key")
        with pytest.raises(FatalError, match="access_key"):
            PvcobraVadBackend(access_key="ak-bad")

    def test_import_failure_raises_with_vad_extra_hint(self, monkeypatch) -> None:
        monkeypatch.setitem(sys.modules, "pvcobra", None)
        from voice_translator.vad.pvcobra_backend import PvcobraVadBackend

        with pytest.raises(FatalError, match="vad-extra"):
            PvcobraVadBackend(access_key="ak")


# ============================================================
# credential_spec / verify_credentials
# ============================================================
class TestCredentials:
    def test_credential_spec_has_access_key(self) -> None:
        from voice_translator.vad.pvcobra_backend import PvcobraVadBackend

        spec = PvcobraVadBackend.credential_spec()
        assert [f.key_name for f in spec] == ["access_key"]
        assert spec[0].secret is True

    def test_verify_empty_fails(self) -> None:
        from voice_translator.vad.pvcobra_backend import PvcobraVadBackend

        result = PvcobraVadBackend.verify_credentials({"access_key": ""})
        assert result.ok is False
        assert "未入力" in result.message

    def test_verify_success(self, fake_pvcobra) -> None:
        from voice_translator.vad.pvcobra_backend import PvcobraVadBackend

        result = PvcobraVadBackend.verify_credentials({"access_key": "ak"})
        assert result.ok is True
        # 即時 delete されている(リーク防止)
        _, fake_inst = fake_pvcobra
        fake_inst.delete.assert_called_once()

    def test_verify_invalid_key_returns_failure(self, fake_pvcobra) -> None:
        from voice_translator.vad.pvcobra_backend import PvcobraVadBackend

        fake_module, _ = fake_pvcobra
        fake_module.create.side_effect = RuntimeError("invalid key")
        result = PvcobraVadBackend.verify_credentials({"access_key": "bad"})
        assert result.ok is False
        assert "無効" in result.message


# ============================================================
# フレーム判定
# ============================================================
class TestFrameDetection:
    def test_empty_chunk_returns_empty(self, fake_pvcobra) -> None:
        from voice_translator.vad.pvcobra_backend import PvcobraVadBackend

        backend = PvcobraVadBackend(access_key="ak")
        assert backend.process(np.zeros(0, dtype=np.float32)) == []

    def test_buffer_under_frame_not_called(self, fake_pvcobra) -> None:
        from voice_translator.vad.pvcobra_backend import PvcobraVadBackend

        _, fake_inst = fake_pvcobra
        backend = PvcobraVadBackend(access_key="ak")
        backend.process(np.zeros(100, dtype=np.float32))
        fake_inst.process.assert_not_called()

    def test_full_speech_cycle_emits_segment(self, fake_pvcobra) -> None:
        """speech 検出 → silence で発話終了 → VadSegment 1 件。

        既定 min_speech_ms=64 / frame=32ms → 2 連続で開始。
        min_silence_ms=500 / frame=32ms → ceil(500/32)=16 連続で終了。
        """
        from voice_translator.vad.pvcobra_backend import PvcobraVadBackend

        _, fake_inst = fake_pvcobra
        # speech 5 + silence 16 = 21 frames
        fake_inst.process.side_effect = [0.9] * 5 + [0.1] * 16
        backend = PvcobraVadBackend(
            access_key="ak",
            threshold=0.5,
            speech_pad_ms=0,
            max_speech_sec=0,
        )
        # 512 サンプル × 21 frame
        result = backend.process(np.zeros(512 * 21, dtype=np.float32))
        assert len(result) == 1
        assert isinstance(result[0], VadSegment)


# ============================================================
# max_speech_sec
# ============================================================
class TestMaxSpeechCutoff:
    def test_force_cut(self, fake_pvcobra) -> None:
        from voice_translator.vad.pvcobra_backend import PvcobraVadBackend

        _, fake_inst = fake_pvcobra
        fake_inst.process.return_value = 0.9  # 常に speech
        backend = PvcobraVadBackend(
            access_key="ak",
            threshold=0.5,
            min_speech_ms=32,  # 1 frame で開始
            speech_pad_ms=0,
            max_speech_sec=1024 / 16000,  # 2 フレーム超で強制
        )
        result = backend.process(np.ones(512 * 5, dtype=np.float32))
        assert len(result) >= 1


# ============================================================
# reset
# ============================================================
class TestReset:
    def test_reset_clears_state(self, fake_pvcobra) -> None:
        from voice_translator.vad.pvcobra_backend import PvcobraVadBackend

        _, fake_inst = fake_pvcobra
        fake_inst.process.return_value = 0.9
        backend = PvcobraVadBackend(
            access_key="ak", threshold=0.5, min_speech_ms=32,
            speech_pad_ms=0, max_speech_sec=0,
        )
        backend.process(np.ones(512 * 3, dtype=np.float32))  # speech 中
        backend.reset()
        assert backend._buffer.size == 0
        assert backend._in_speech is False
        assert backend._speech_accumulated_samples == 0
