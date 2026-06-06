"""runner_output の単体テスト。

実 backend(soundcard / SAPI)は触らず、`_create_output_backend` /
`_create_tts_backend` を Fake に差し替えて runner の制御フロー(引数解釈・デバイス
解決・音源生成・start/play/stop 順序)だけを検証する。
"""

from __future__ import annotations

import wave
from pathlib import Path

import numpy as np
import pytest

from voice_translator.common.types import OutputDevice
from voice_translator.dev import runner_output


class FakeOutputBackend:
    """list_devices / start / play / stop を記録するだけの Fake。"""

    def __init__(self, *, devices: list[OutputDevice] | None = None) -> None:
        self._devices = devices if devices is not None else [
            OutputDevice(device_id="dev-default", display_name="Default Speaker"),
            OutputDevice(device_id="dev-2", display_name="Headphones"),
        ]
        self.started_with: str | None = None
        self.played: list[tuple[np.ndarray, int]] = []
        self.stopped: int = 0

    def list_devices(self) -> list[OutputDevice]:
        return list(self._devices)

    def start(self, device_id: str) -> None:
        self.started_with = device_id

    def play(self, pcm, samplerate: int) -> None:
        self.played.append((np.asarray(pcm), int(samplerate)))

    def stop(self) -> None:
        self.stopped += 1


class FakeTtsBackend:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def synthesize(self, text: str, tgt_lang: str):
        self.calls.append((text, tgt_lang))
        sr = 22050
        t = np.linspace(0, 0.1, sr // 10, endpoint=False)
        pcm = (0.3 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)
        return pcm, sr


# ============================================================
# 共通 fixture
# ============================================================
@pytest.fixture
def fake_output(monkeypatch: pytest.MonkeyPatch) -> FakeOutputBackend:
    """`_create_output_backend` を Fake で置き換える(`_build_registry` は no-op)。"""
    inst = FakeOutputBackend()

    # registry 構築は重いので空オブジェクトを返す
    monkeypatch.setattr(runner_output, "_build_registry", lambda: object())
    monkeypatch.setattr(
        runner_output,
        "_create_output_backend",
        lambda _registry, _name: inst,
    )
    return inst


@pytest.fixture
def fake_tts(monkeypatch: pytest.MonkeyPatch) -> FakeTtsBackend:
    """`_create_tts_backend` を Fake で置き換える。"""
    inst = FakeTtsBackend()
    monkeypatch.setattr(
        runner_output,
        "_create_tts_backend",
        lambda _registry, _name: inst,
    )
    return inst


# ============================================================
# テスト
# ============================================================
def test_list_devices_prints_and_returns_zero(
    fake_output: FakeOutputBackend, capsys: pytest.CaptureFixture
) -> None:
    rc = runner_output.run(["--list-devices"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "dev-default" in out
    assert "Default Speaker" in out
    assert "dev-2" in out
    # 一覧モードでは start/play/stop は呼ばれない
    assert fake_output.started_with is None
    assert fake_output.played == []
    assert fake_output.stopped == 0


def test_tone_default_uses_first_device(fake_output: FakeOutputBackend) -> None:
    rc = runner_output.run(["--tone", "--tone-sec", "0.05"])
    assert rc == 0
    # 先頭デバイス(= default)が選ばれる
    assert fake_output.started_with == "dev-default"
    assert len(fake_output.played) == 1
    pcm, sr = fake_output.played[0]
    assert sr == 44100  # tone のデフォルトサンプルレート
    assert pcm.dtype == np.float32
    # 0.05 秒 = 2205 samples
    assert pcm.shape[0] == int(0.05 * 44100)
    assert fake_output.stopped == 1


def test_device_id_resolves_specific_device(fake_output: FakeOutputBackend) -> None:
    rc = runner_output.run(
        ["--device-id", "dev-2", "--tone", "--tone-sec", "0.05"]
    )
    assert rc == 0
    assert fake_output.started_with == "dev-2"


def test_device_id_not_found_returns_error(
    fake_output: FakeOutputBackend, caplog: pytest.LogCaptureFixture
) -> None:
    import logging

    caplog.set_level(logging.ERROR, logger="voice_translator.dev")
    rc = runner_output.run(["--device-id", "no-such-id", "--tone"])
    assert rc == 2
    # start / play は呼ばれていないこと
    assert fake_output.started_with is None
    assert fake_output.played == []
    # 候補がエラーログに含まれる(指定 ID と候補一覧の両方)
    messages = " ".join(rec.getMessage() for rec in caplog.records)
    assert "no-such-id" in messages
    assert "dev-default" in messages


def test_wav_source_uses_wav_samplerate(
    fake_output: FakeOutputBackend, tmp_path: Path
) -> None:
    # 22050 Hz / 0.1 秒 の WAV を用意
    sr = 22050
    samples = (0.2 * np.sin(2 * np.pi * 220 * np.arange(sr // 10) / sr)).astype(np.float32)
    i16 = (samples * 32767).astype(np.int16)
    wav_path = tmp_path / "input.wav"
    with wave.open(str(wav_path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(i16.tobytes())

    rc = runner_output.run(["--wav", str(wav_path)])
    assert rc == 0
    assert len(fake_output.played) == 1
    _, played_sr = fake_output.played[0]
    assert played_sr == sr


def test_text_source_calls_tts_backend(
    fake_output: FakeOutputBackend, fake_tts: FakeTtsBackend,
) -> None:
    rc = runner_output.run(["--text", "テスト音声", "--tgt-lang", "ja"])
    assert rc == 0
    assert fake_tts.calls == [("テスト音声", "ja")]
    assert len(fake_output.played) == 1
    # SAPI Fake は 22050 Hz を返す
    _, played_sr = fake_output.played[0]
    assert played_sr == 22050


def test_stop_called_even_when_play_fails(
    fake_output: FakeOutputBackend, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """play で例外が出ても stop は呼ばれる(リソース解放保証)。"""
    def boom(*_a, **_kw):
        raise RuntimeError("simulated play failure")

    monkeypatch.setattr(fake_output, "play", boom)
    rc = runner_output.run(["--tone", "--tone-sec", "0.05"])
    assert rc == 5  # play 失敗の終了コード
    assert fake_output.started_with == "dev-default"
    assert fake_output.stopped == 1  # finally で stop 済


def test_empty_pcm_returns_error_without_calling_backend(
    fake_output: FakeOutputBackend, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """音源が 0 サンプルだったら start すら呼ばずに失敗する。"""
    # tone を強制的に空 ndarray にする
    monkeypatch.setattr(
        runner_output, "make_tone",
        lambda **_kw: (np.zeros(0, dtype=np.float32), 44100),
    )
    rc = runner_output.run(["--tone"])
    assert rc == 3
    assert fake_output.started_with is None
    assert fake_output.played == []


def test_mutually_exclusive_sources(
    fake_output: FakeOutputBackend, capsys: pytest.CaptureFixture
) -> None:
    """--tone / --wav / --text は同時指定不可(argparse mutually-exclusive)。"""
    with pytest.raises(SystemExit):
        runner_output.run(["--tone", "--text", "hi"])


def test_make_tone_shape_and_fade() -> None:
    """make_tone は指定長 + フェードイン/アウトを生成する。"""
    pcm, sr = runner_output.make_tone(
        freq_hz=440.0, duration_sec=0.1, samplerate=48000, amplitude=0.5,
    )
    assert sr == 48000
    assert pcm.shape == (4800,)
    assert pcm.dtype == np.float32
    # 先頭と末尾はフェードで 0 から始まり 0 で終わる(振幅 0.5 より小さいはず)
    assert abs(pcm[0]) < 0.05
    assert abs(pcm[-1]) < 0.05
    # 中央付近は振幅近くまで届く
    assert pcm.max() > 0.4
    assert pcm.min() < -0.4
