"""gui/logic/ready_state.py の単体テスト(純関数、GUI 不要)。

移行元: control_panel.py の `_sync_ready_state` / `_sync_load_button_state` /
`_sync_test_button_state` / `_capture_source_required_but_empty` / `_active_layer_statuses`。
表示文言・優先順位が移行元と同一であることを検証する。
"""

from __future__ import annotations

from voice_translator.common.types import CaptureKind, LayerKind, ModelStatus
from voice_translator.gui.logic.ready_state import (
    compute_ready_state,
    filter_active_statuses,
)


def _all_statuses(status: ModelStatus) -> dict[LayerKind, ModelStatus]:
    return {layer: status for layer in LayerKind}


def _compute(statuses, **overrides):
    """既定値(audio / DEVICE / 入出力選択済み)で compute_ready_state を呼ぶ補助。"""
    kwargs = dict(
        output_mode="audio",
        capture_kind=CaptureKind.DEVICE,
        has_input_source=True,
        has_output_device=True,
    )
    kwargs.update(overrides)
    return compute_ready_state(statuses, **kwargs)


class TestToggleAndStatusLabel:
    def test_all_loaded_is_startable(self) -> None:
        rs = _compute(_all_statuses(ModelStatus.LOADED))
        assert rs.toggle.text == "▶ 開始"
        assert rs.toggle.enabled is True
        assert rs.status_text == "停止中"

    def test_missing_credentials_disables_start(self) -> None:
        statuses = _all_statuses(ModelStatus.LOADED)
        statuses[LayerKind.ASR] = ModelStatus.MISSING_CREDENTIALS
        rs = _compute(statuses)
        assert rs.toggle.text == "認証情報未設定"
        assert rs.toggle.enabled is False
        assert "詳細ダイアログ" in rs.status_text

    def test_downloading_disables_start(self) -> None:
        statuses = _all_statuses(ModelStatus.LOADED)
        statuses[LayerKind.ASR] = ModelStatus.DOWNLOADING
        rs = _compute(statuses)
        assert rs.toggle.text == "モデル DL 中…"
        assert rs.toggle.enabled is False
        assert rs.status_text == "モデルダウンロード中…"

    def test_missing_credentials_takes_priority_over_downloading(self) -> None:
        """移行元の分岐順: MISSING_CREDENTIALS が DOWNLOADING より先に判定される。"""
        statuses = _all_statuses(ModelStatus.LOADED)
        statuses[LayerKind.ASR] = ModelStatus.MISSING_CREDENTIALS
        statuses[LayerKind.TRANSLATOR] = ModelStatus.DOWNLOADING
        rs = _compute(statuses)
        assert rs.toggle.text == "認証情報未設定"

    def test_process_kind_without_input_blocks_start(self) -> None:
        rs = _compute(
            _all_statuses(ModelStatus.LOADED),
            capture_kind=CaptureKind.PROCESS,
            has_input_source=False,
        )
        assert rs.toggle.text == "プロセス未選択"
        assert rs.toggle.enabled is False
        assert "プロセス選択" in rs.status_text

    def test_process_kind_with_input_is_startable(self) -> None:
        rs = _compute(
            _all_statuses(ModelStatus.LOADED),
            capture_kind=CaptureKind.PROCESS,
            has_input_source=True,
        )
        assert rs.toggle.text == "▶ 開始"
        assert rs.toggle.enabled is True

    def test_init_remaining_shows_lazy_load_label(self) -> None:
        statuses = _all_statuses(ModelStatus.LOADED)
        statuses[LayerKind.ASR] = ModelStatus.INIT
        rs = _compute(statuses)
        assert rs.toggle.enabled is True
        assert rs.status_text == "停止中(押下時にロードします)"

    def test_not_downloaded_remaining_shows_lazy_load_label(self) -> None:
        statuses = _all_statuses(ModelStatus.LOADED)
        statuses[LayerKind.TTS] = ModelStatus.NOT_DOWNLOADED
        rs = _compute(statuses)
        assert rs.status_text == "停止中(押下時にロードします)"

    def test_loading_remaining_shows_loading_label(self) -> None:
        statuses = _all_statuses(ModelStatus.LOADED)
        statuses[LayerKind.ASR] = ModelStatus.LOADING
        rs = _compute(statuses)
        assert rs.toggle.enabled is True
        assert rs.status_text == "停止中(ロード中)"

    def test_init_takes_priority_over_loading_in_label(self) -> None:
        """INIT と LOADING が混在 → 「押下時にロードします」(移行元の分岐順)。"""
        statuses = _all_statuses(ModelStatus.LOADED)
        statuses[LayerKind.ASR] = ModelStatus.INIT
        statuses[LayerKind.VAD] = ModelStatus.LOADING
        rs = _compute(statuses)
        assert rs.status_text == "停止中(押下時にロードします)"


class TestTextOnlyFiltering:
    def test_text_only_ignores_tts_output_missing_credentials(self) -> None:
        """text_only では TTS / OUTPUT が MISSING_CREDENTIALS でも Start 可能。"""
        statuses = _all_statuses(ModelStatus.LOADED)
        statuses[LayerKind.TTS] = ModelStatus.MISSING_CREDENTIALS
        statuses[LayerKind.OUTPUT] = ModelStatus.MISSING_CREDENTIALS
        rs = _compute(statuses, output_mode="text_only")
        assert rs.toggle.text == "▶ 開始"
        assert rs.toggle.enabled is True

    def test_filter_active_statuses_audio_keeps_all(self) -> None:
        active = filter_active_statuses(_all_statuses(ModelStatus.INIT), "audio")
        assert set(active.keys()) == set(LayerKind)

    def test_filter_active_statuses_text_only_drops_tts_output(self) -> None:
        active = filter_active_statuses(_all_statuses(ModelStatus.INIT), "text_only")
        assert LayerKind.TTS not in active
        assert LayerKind.OUTPUT not in active
        assert len(active) == len(LayerKind) - 2

    def test_empty_statuses_returns_none(self) -> None:
        """対象レイヤが空 → None(View は何もしない。移行元の早期 return 相当)。"""
        assert _compute({}) is None


class TestLoadButton:
    def test_all_loaded_disables_load_button(self) -> None:
        rs = _compute(_all_statuses(ModelStatus.LOADED))
        assert rs.load.text == "ロード済み"
        assert rs.load.enabled is False

    def test_loading_in_progress_disables_load_button(self) -> None:
        statuses = _all_statuses(ModelStatus.LOADED)
        statuses[LayerKind.ASR] = ModelStatus.LOADING
        rs = _compute(statuses)
        assert rs.load.text == "ロード中…"
        assert rs.load.enabled is False

    def test_mixed_init_enables_load_button(self) -> None:
        statuses = _all_statuses(ModelStatus.LOADED)
        statuses[LayerKind.ASR] = ModelStatus.INIT
        rs = _compute(statuses)
        assert rs.load.text == "↻ ロード"
        assert rs.load.enabled is True

    def test_missing_credentials_keeps_load_button_enabled(self) -> None:
        """MISSING_CREDENTIALS があっても部分 load は許す(移行元の挙動)。"""
        statuses = _all_statuses(ModelStatus.INIT)
        statuses[LayerKind.ASR] = ModelStatus.MISSING_CREDENTIALS
        rs = _compute(statuses)
        assert rs.load.text == "↻ ロード"
        assert rs.load.enabled is True


class TestTestButton:
    def test_text_only_disables_test_button(self) -> None:
        rs = _compute(_all_statuses(ModelStatus.LOADED), output_mode="text_only")
        assert rs.test.text == "🔊 (TTS なし)"
        assert rs.test.enabled is False

    def test_no_output_device_disables_test_button(self) -> None:
        rs = _compute(_all_statuses(ModelStatus.LOADED), has_output_device=False)
        assert rs.test.text == "🔊 出力未選択"
        assert rs.test.enabled is False

    def test_audio_with_output_enables_test_button(self) -> None:
        rs = _compute(_all_statuses(ModelStatus.INIT))
        assert rs.test.text == "🔊 出力テスト"
        assert rs.test.enabled is True

    def test_text_only_takes_priority_over_missing_output(self) -> None:
        """text_only と出力未選択が同時 → 「(TTS なし)」表示(移行元の分岐順)。"""
        rs = _compute(
            _all_statuses(ModelStatus.LOADED),
            output_mode="text_only",
            has_output_device=False,
        )
        assert rs.test.text == "🔊 (TTS なし)"


class TestAbsorbedRoleFiltering:
    """複合 backend に吸収されたロールは ready 判定から除外される。"""

    def test_filter_excludes_absorbed(self) -> None:
        statuses = _all_statuses(ModelStatus.LOADED)
        statuses[LayerKind.TRANSLATOR] = ModelStatus.INIT  # 吸収中はロードされない
        active = filter_active_statuses(
            statuses, "audio", absorbed=(LayerKind.TRANSLATOR,)
        )
        assert LayerKind.TRANSLATOR not in active
        assert LayerKind.ASR in active

    def test_absorbed_init_does_not_block_loaded_view(self) -> None:
        """複合がロード済みなら、吸収ロールの INIT が残っていても「ロード済み」表示。"""
        statuses = _all_statuses(ModelStatus.LOADED)
        statuses[LayerKind.TRANSLATOR] = ModelStatus.INIT
        rs = _compute(statuses, absorbed=(LayerKind.TRANSLATOR,))
        assert rs.load.text == "ロード済み"
        assert rs.load.enabled is False
        assert rs.status_text == "停止中"

    def test_without_absorbed_init_blocks_loaded_view(self) -> None:
        """absorbed 指定なしなら INIT レイヤは従来どおり判定に含まれる。"""
        statuses = _all_statuses(ModelStatus.LOADED)
        statuses[LayerKind.TRANSLATOR] = ModelStatus.INIT
        rs = _compute(statuses)
        assert rs.load.text == "↻ ロード"
        assert rs.status_text == "停止中(押下時にロードします)"
