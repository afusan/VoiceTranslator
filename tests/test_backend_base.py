"""BackendBase / Subscription の単体テスト。

役割: Phase A1 で導入した backend 基底ミックスインの状態管理 / 購読 / エラー履歴を検証する。
個別 backend(faster-whisper 等)経由ではなく、最小サブクラスで I/F を直接確認する。
"""

from __future__ import annotations

import threading

import pytest

from voice_translator.common.backend_base import BackendBase, Subscription
from voice_translator.common.types import ErrorRecord, ModelInfo, ModelStatus


class _DummyBackend(BackendBase):
    """テスト用の最小サブクラス。状態遷移を外から駆動するためのフック付き。"""

    def trigger(self, status: ModelStatus) -> None:
        self._set_status(status)


class _DummyWithModels(BackendBase):
    def list_recommended_models(self) -> list[ModelInfo]:
        return [ModelInfo(name="small", display_name="Small")]


class TestInitialState:
    def test_status_is_init(self) -> None:
        b = _DummyBackend()
        assert b.get_status() == ModelStatus.INIT

    def test_no_errors_initially(self) -> None:
        b = _DummyBackend()
        assert b.get_recent_errors() == []

    def test_default_no_recommended_models(self) -> None:
        b = _DummyBackend()
        assert b.list_recommended_models() == []


class TestStatusTransition:
    def test_set_status_updates_state(self) -> None:
        b = _DummyBackend()
        b.trigger(ModelStatus.LOADING)
        assert b.get_status() == ModelStatus.LOADING

    def test_same_status_skipped(self) -> None:
        """同じ値への遷移は notify を発火しない(冪等)。"""
        b = _DummyBackend()
        b.trigger(ModelStatus.LOADING)
        seen: list[ModelStatus] = []
        b.subscribe(lambda s: seen.append(s))
        b.trigger(ModelStatus.LOADING)  # 同じ
        assert seen == []
        b.trigger(ModelStatus.LOADED)
        assert seen == [ModelStatus.LOADED]


class TestSubscribe:
    def test_callback_invoked_on_change(self) -> None:
        b = _DummyBackend()
        seen: list[ModelStatus] = []
        b.subscribe(lambda s: seen.append(s))
        b.trigger(ModelStatus.LOADING)
        b.trigger(ModelStatus.LOADED)
        assert seen == [ModelStatus.LOADING, ModelStatus.LOADED]

    def test_multiple_subscribers_all_notified(self) -> None:
        b = _DummyBackend()
        seen_a: list[ModelStatus] = []
        seen_b: list[ModelStatus] = []
        b.subscribe(lambda s: seen_a.append(s))
        b.subscribe(lambda s: seen_b.append(s))
        b.trigger(ModelStatus.LOADED)
        assert seen_a == [ModelStatus.LOADED]
        assert seen_b == [ModelStatus.LOADED]

    def test_unsubscribe_stops_notifications(self) -> None:
        b = _DummyBackend()
        seen: list[ModelStatus] = []
        sub = b.subscribe(lambda s: seen.append(s))
        b.trigger(ModelStatus.LOADING)
        sub.unsubscribe()
        b.trigger(ModelStatus.LOADED)
        assert seen == [ModelStatus.LOADING]

    def test_double_unsubscribe_safe(self) -> None:
        b = _DummyBackend()
        sub = b.subscribe(lambda s: None)
        sub.unsubscribe()
        sub.unsubscribe()  # no-op、例外なし
        assert sub.is_active is False

    def test_listener_exception_does_not_break_others(self) -> None:
        """ある listener が例外を投げても他 listener / 本体は止まらない。"""
        b = _DummyBackend()
        seen: list[ModelStatus] = []

        def bad(_s: ModelStatus) -> None:
            raise RuntimeError("listener bug")

        b.subscribe(bad)
        b.subscribe(lambda s: seen.append(s))
        b.trigger(ModelStatus.LOADED)
        assert seen == [ModelStatus.LOADED]
        # 本体の status も正常に更新されている
        assert b.get_status() == ModelStatus.LOADED

    def test_subscription_returns_subscription_object(self) -> None:
        b = _DummyBackend()
        sub = b.subscribe(lambda _: None)
        assert isinstance(sub, Subscription)
        assert sub.is_active is True


class TestErrorHistory:
    def test_record_error_appends(self) -> None:
        b = _DummyBackend()
        b.record_error(RuntimeError("boom"), context="load")
        errors = b.get_recent_errors()
        assert len(errors) == 1
        assert isinstance(errors[0], ErrorRecord)
        assert errors[0].message == "boom"
        assert errors[0].exc_type == "RuntimeError"
        assert errors[0].context == "load"

    def test_record_error_without_context(self) -> None:
        b = _DummyBackend()
        b.record_error(ValueError("x"))
        assert b.get_recent_errors()[0].context is None

    def test_ring_buffer_bounded(self) -> None:
        """直近 5 件のみ保持(古いものから破棄)。"""
        b = _DummyBackend()
        for i in range(7):
            b.record_error(RuntimeError(f"e{i}"))
        msgs = [r.message for r in b.get_recent_errors()]
        # 古い 2 件(e0, e1)は破棄され、e2..e6 が残る
        assert msgs == ["e2", "e3", "e4", "e5", "e6"]


class TestRecommendedModels:
    def test_subclass_overrides_returns_list(self) -> None:
        b = _DummyWithModels()
        models = b.list_recommended_models()
        assert len(models) == 1
        assert models[0].name == "small"


class TestThreadSafety:
    """購読の add/remove と notify の並行操作で例外が出ない最低限の確認。"""

    def test_concurrent_subscribe_and_trigger(self) -> None:
        b = _DummyBackend()
        stop = threading.Event()
        errors: list[BaseException] = []

        def subscribe_loop() -> None:
            try:
                while not stop.is_set():
                    sub = b.subscribe(lambda _: None)
                    sub.unsubscribe()
            except BaseException as e:  # noqa: BLE001
                errors.append(e)

        def trigger_loop() -> None:
            try:
                toggle = True
                while not stop.is_set():
                    b.trigger(ModelStatus.LOADING if toggle else ModelStatus.LOADED)
                    toggle = not toggle
            except BaseException as e:  # noqa: BLE001
                errors.append(e)

        t1 = threading.Thread(target=subscribe_loop)
        t2 = threading.Thread(target=trigger_loop)
        t1.start()
        t2.start()
        # 短時間だけ並行動作
        threading.Event().wait(0.05)
        stop.set()
        t1.join(timeout=1.0)
        t2.join(timeout=1.0)
        assert errors == []
