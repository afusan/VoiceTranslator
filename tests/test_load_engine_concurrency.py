"""ロードエンジンの並行性(構築はロック外 / in-flight / 世代)の構造テスト。

UI フリーズ恒久対策(ロック再構成)の契約を固定する:
- モデル構築中でも evict(バックエンド変更の反応系)はブロックされない
  (UI スレッドがロック待ちで固まらない)
- 構築中に設定が変わったら完成品を捨てて最新の選択をロードし直す(last-write-wins。
  構築は中断できないため完走 → 破棄)
- 同一レイヤへの並行ロード要求は二重構築にならない(1 回の構築を共有)
- 構築失敗時は in-flight が解除され、後続のロードが可能

待ち合わせはすべて Event / join のタイムアウト付きで行い、sleep 頼みの不安定な
同期はしない。
"""

from __future__ import annotations

import threading
from time import monotonic, sleep
from unittest.mock import MagicMock

import pytest

from voice_translator.common.app_controller import AppController
from voice_translator.common.backend_registry import BackendRegistry
from voice_translator.common.config_store import ConfigStore
from voice_translator.common.types import LayerKind, ModelStatus


def _make_instance(name: str) -> MagicMock:
    inst = MagicMock(name=name)
    inst.get_status = MagicMock(return_value=ModelStatus.LOADED)
    sub = MagicMock()
    sub.unsubscribe = MagicMock()
    inst.subscribe = MagicMock(return_value=sub)
    inst.get_recent_errors = MagicMock(return_value=[])
    return inst


def _wait_until(cond, *, timeout: float = 5.0, message: str = "条件が時間内に成立しない"):
    deadline = monotonic() + timeout
    while monotonic() < deadline:
        if cond():
            return
        sleep(0.005)
    raise AssertionError(message)


@pytest.fixture()
def ctrl_with_gated_asr(tmp_path):
    """ASR に「gate Event まで構築が終わらない slow_asr」と「即時の alt_asr」を持つ controller。

    made に factory が生成したインスタンスを記録する(破棄/採用の判定に使う)。
    """
    gate = threading.Event()
    made: dict[str, list[MagicMock]] = {"slow": [], "alt": []}

    def slow_factory():
        gate.wait(timeout=10.0)
        inst = _make_instance("slow_asr_inst")
        made["slow"].append(inst)
        return inst

    def alt_factory():
        inst = _make_instance("alt_asr_inst")
        made["alt"].append(inst)
        return inst

    reg = BackendRegistry()
    reg.register(LayerKind.CAPTURE, "soundcard", lambda: _make_instance("cap"))
    reg.register(LayerKind.VAD, "silero", lambda: _make_instance("vad"))
    reg.register(LayerKind.ASR, "slow_asr", slow_factory)
    reg.register(LayerKind.ASR, "alt_asr", alt_factory)
    reg.register(LayerKind.TRANSLATOR, "nllb200", lambda: _make_instance("tr"))
    reg.register(LayerKind.TTS, "sapi", lambda: _make_instance("tts"))
    reg.register(LayerKind.OUTPUT, "soundcard", lambda: _make_instance("out"))
    cfg = ConfigStore(tmp_path / "cfg.yaml")
    cfg.set("backends", "asr", "slow_asr")
    ctrl = AppController(registry=reg, config=cfg)
    yield ctrl, gate, made
    gate.set()  # 後始末: 構築待ちスレッドを必ず解放する


def _start_gated_load(ctrl) -> threading.Thread:
    """ASR のロードをバックグラウンドで開始し、構築区間に入るまで待つ。"""
    loader = threading.Thread(
        target=lambda: ctrl.load_model_layer(LayerKind.ASR), daemon=True,
    )
    loader.start()
    _wait_until(
        lambda: LayerKind.ASR in ctrl._inflight,
        message="ロードが構築区間(in-flight)に入らない",
    )
    return loader


class TestEvictDuringBuild:
    def test_evict_does_not_block_while_building(self, ctrl_with_gated_asr) -> None:
        """構築中の set_setting(evict)が構築完了を待たされない(UI フリーズ相当の検出)。"""
        ctrl, gate, _ = ctrl_with_gated_asr
        loader = _start_gated_load(ctrl)

        changed = threading.Event()

        def change():
            ctrl.set_setting("backends", "asr", "alt_asr")
            changed.set()

        threading.Thread(target=change, daemon=True).start()
        assert changed.wait(timeout=1.0), (
            "構築中の evict がブロックされた(ロックを保持したまま構築している)"
        )

        gate.set()
        loader.join(timeout=5.0)
        assert not loader.is_alive()

    def test_last_write_wins_after_change_during_build(
        self, ctrl_with_gated_asr,
    ) -> None:
        """構築中に変更されたら、完成品は捨てて最新の選択をロードし直す。"""
        ctrl, gate, made = ctrl_with_gated_asr
        loader = _start_gated_load(ctrl)

        ctrl.set_setting("backends", "asr", "alt_asr")
        gate.set()
        loader.join(timeout=5.0)
        assert not loader.is_alive()

        assert len(made["slow"]) == 1, "旧選択の構築は 1 回だけ走る"
        assert len(made["alt"]) == 1, "最新の選択がロードし直されていない"
        assert ctrl._backends[LayerKind.ASR] is made["alt"][0], (
            "旧設定の完成品が残っている(last-write-wins になっていない)"
        )

    def test_discarded_instance_is_not_subscribed(self, ctrl_with_gated_asr) -> None:
        """破棄された完成品は subscribe されない(購読リーク防止)。"""
        ctrl, gate, made = ctrl_with_gated_asr
        loader = _start_gated_load(ctrl)

        ctrl.set_setting("backends", "asr", "alt_asr")
        gate.set()
        loader.join(timeout=5.0)

        made["slow"][0].subscribe.assert_not_called()
        made["alt"][0].subscribe.assert_called_once()

    def test_same_backend_reselect_during_build_rebuilds(
        self, ctrl_with_gated_asr,
    ) -> None:
        """同名への再選択でも世代が進み作り直す(backends_config 反映の既存規則を維持)。"""
        ctrl, gate, made = ctrl_with_gated_asr
        loader = _start_gated_load(ctrl)

        ctrl.set_setting("backends", "asr", "slow_asr")
        gate.set()
        loader.join(timeout=5.0)

        assert len(made["slow"]) == 2, "同名再選択後に作り直されていない"
        assert ctrl._backends[LayerKind.ASR] is made["slow"][1]


class TestConcurrentLoadSharing:
    def test_concurrent_loads_build_once(self, ctrl_with_gated_asr) -> None:
        """同一レイヤへの並行ロードは 1 回の構築を共有する(二重構築防止)。"""
        ctrl, gate, made = ctrl_with_gated_asr
        done: list[bool] = []

        def load():
            ctrl.load_model_layer(LayerKind.ASR)
            done.append(True)

        t1 = threading.Thread(target=load, daemon=True)
        t2 = threading.Thread(target=load, daemon=True)
        t1.start()
        t2.start()
        _wait_until(lambda: LayerKind.ASR in ctrl._inflight)

        gate.set()
        t1.join(timeout=5.0)
        t2.join(timeout=5.0)

        assert done == [True, True]
        assert len(made["slow"]) == 1, "並行ロードで二重構築された"
        assert ctrl._backends[LayerKind.ASR] is made["slow"][0]


class TestFailureReleasesInflight:
    def test_failure_clears_inflight_and_allows_retry(self, tmp_path) -> None:
        """構築失敗で in-flight が残らず、選択を直せば次のロードが通る。"""
        def broken_factory():
            raise RuntimeError("model not found")

        reg = BackendRegistry()
        reg.register(LayerKind.ASR, "broken_asr", broken_factory)
        reg.register(LayerKind.ASR, "alt_asr", lambda: _make_instance("alt"))
        cfg = ConfigStore(tmp_path / "cfg.yaml")
        cfg.set("backends", "asr", "broken_asr")
        ctrl = AppController(registry=reg, config=cfg)

        statuses: list[ModelStatus] = []
        ctrl.add_status_listener(
            lambda l, s: statuses.append(s) if l == LayerKind.ASR else None
        )
        with pytest.raises(RuntimeError):
            ctrl.load_model_layer(LayerKind.ASR)
        assert LayerKind.ASR not in ctrl._inflight, "失敗後に in-flight が残っている"
        assert statuses[-1] == ModelStatus.NOT_DOWNLOADED

        ctrl.set_setting("backends", "asr", "alt_asr")
        ctrl.load_model_layer(LayerKind.ASR)
        assert LayerKind.ASR in ctrl._backends
