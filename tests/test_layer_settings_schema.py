"""layer_settings_schema の単体テスト。

GUI ダイアログ本体(customtkinter)はメインスレッド/Tk が必要なため、
ここではスキーマ層(値変換・条件付き表示)のみをテストする。
"""

from __future__ import annotations

import pytest

from unittest.mock import MagicMock

from voice_translator.common.hw_info import HwInfo
from voice_translator.common.types import LayerKind, ModelInfo
from voice_translator.gui.layer_settings_schema import (
    ALL_FIELD_TYPES,
    LAYER_SETTINGS,
    SettingField,
    format_model_option,
    load_model_action,
    parse_field_value,
    recent_durations_text,
    visible_fields,
)


class TestSchemaIntegrity:
    def test_all_layers_have_entry(self) -> None:
        """全レイヤがスキーマに登録されている(空でも entry がある)。"""
        for layer in LayerKind:
            assert layer in LAYER_SETTINGS, f"{layer} がスキーマに無い"

    def test_setting_field_keys_non_empty(self) -> None:
        for layer, fields in LAYER_SETTINGS.items():
            for f in fields:
                assert isinstance(f, SettingField)
                assert f.label.strip(), f"{layer}: label が空"
                assert f.field_type in ALL_FIELD_TYPES, f"{layer}: 未対応 field_type"
                # button は値を持たないので keys が空でも可、それ以外は最低 2 階層
                if f.field_type != "button":
                    assert len(f.keys) >= 2, f"{layer}: keys は最低2階層必要({f.keys})"


class TestParseFieldValue:
    def test_int_basic(self) -> None:
        assert parse_field_value("int", "42") == 42
        assert parse_field_value("int", "  100 ") == 100
        assert parse_field_value("int", "-3") == -3

    def test_int_invalid(self) -> None:
        with pytest.raises(ValueError):
            parse_field_value("int", "abc")
        with pytest.raises(ValueError):
            parse_field_value("int", "3.14")

    def test_float_basic(self) -> None:
        assert parse_field_value("float", "3.14") == 3.14
        assert parse_field_value("float", "0") == 0.0

    def test_float_invalid(self) -> None:
        with pytest.raises(ValueError):
            parse_field_value("float", "not-a-number")

    def test_str_passes_through(self) -> None:
        assert parse_field_value("str", "hello") == "hello"
        assert parse_field_value("str", " spaces preserved ") == " spaces preserved "

    def test_bool_truthy(self) -> None:
        assert parse_field_value("bool", "1") is True
        assert parse_field_value("bool", "true") is True
        assert parse_field_value("bool", "TRUE") is True
        assert parse_field_value("bool", "on") is True

    def test_bool_falsy(self) -> None:
        assert parse_field_value("bool", "0") is False
        assert parse_field_value("bool", "false") is False
        assert parse_field_value("bool", "no") is False
        assert parse_field_value("bool", "") is False

    def test_unsupported_type_raises(self) -> None:
        with pytest.raises(ValueError):
            parse_field_value("complex", "1+2j")

    def test_toggle_parses_like_bool(self) -> None:
        """Phase C1: "toggle" は bool と同じパーサ(ON/OFF スイッチ用)。"""
        assert parse_field_value("toggle", "true") is True
        assert parse_field_value("toggle", "false") is False
        assert parse_field_value("toggle", "1") is True
        assert parse_field_value("toggle", "0") is False

    def test_dropdown_passes_through_string(self) -> None:
        """Phase C1: "dropdown" は str をそのまま返す(モデル名等の選択肢)。"""
        assert parse_field_value("dropdown", "small") == "small"
        assert parse_field_value("dropdown", "facebook/nllb-200") == "facebook/nllb-200"


class TestSettingFieldNewAttrs:
    """Phase C1 で追加した options_fn / action_fn / reactive_to の基本動作。"""

    def test_defaults_none_and_empty(self) -> None:
        f = SettingField(keys=("a", "b"), label="x", field_type="int")
        assert f.options_fn is None
        assert f.action_fn is None
        assert f.reactive_to == ()

    def test_dropdown_with_options_fn(self) -> None:
        captured: list = []

        def opts(ctrl, layer):
            captured.append((ctrl, layer))
            return ["a", "b", "c"]

        f = SettingField(
            keys=("backends_config", "faster_whisper", "model_size"),
            label="モデル",
            field_type="dropdown",
            options_fn=opts,
        )
        # 呼び出し側が (controller, layer) を渡す規約
        result = f.options_fn("ctrl_stub", LayerKind.ASR)
        assert result == ["a", "b", "c"]
        assert captured == [("ctrl_stub", LayerKind.ASR)]

    def test_button_with_action_fn(self) -> None:
        triggered: list = []

        def action(ctrl, layer):
            triggered.append((ctrl, layer))

        f = SettingField(
            keys=(),
            label="Load Model",
            field_type="button",
            action_fn=action,
        )
        f.action_fn("ctrl_stub", LayerKind.ASR)
        assert triggered == [("ctrl_stub", LayerKind.ASR)]

    def test_label_readonly_with_reactive_to(self) -> None:
        f = SettingField(
            keys=("info", "asr_recent_ms"),
            label="直近処理時間",
            field_type="label_readonly",
            reactive_to=(LayerKind.ASR,),
        )
        assert f.reactive_to == (LayerKind.ASR,)
        assert f.field_type == "label_readonly"


class TestFormatModelOption:
    """`format_model_option` の表示整形(Phase C2)。"""

    _SMALL_HW = HwInfo(ram_gb=4.0, has_gpu=False, vram_gb=None)
    _BIG_HW = HwInfo(ram_gb=64.0, has_gpu=True, vram_gb=24.0)

    def test_includes_display_name(self) -> None:
        m = ModelInfo(name="small", display_name="Small (~460MB)")
        text = format_model_option(m, hw=self._BIG_HW)
        assert "Small (~460MB)" in text

    def test_includes_ram_when_known(self) -> None:
        m = ModelInfo(name="m", display_name="M", ram_gb=2.0)
        text = format_model_option(m, hw=self._BIG_HW)
        assert "RAM 2.0GB" in text

    def test_includes_vram_when_known(self) -> None:
        m = ModelInfo(name="m", display_name="M", vram_gb_if_gpu=4.0)
        text = format_model_option(m, hw=self._BIG_HW)
        assert "VRAM 4.0GB" in text

    def test_ok_icon_for_fitting_model(self) -> None:
        m = ModelInfo(name="m", display_name="M", ram_gb=1.0, vram_gb_if_gpu=1.0)
        text = format_model_option(m, hw=self._BIG_HW)
        assert "✓" in text

    def test_infeasible_icon_for_oversized(self) -> None:
        m = ModelInfo(name="huge", display_name="Huge", ram_gb=64.0)
        text = format_model_option(m, hw=self._SMALL_HW)
        assert "✗" in text

    def test_unknown_icon_when_no_info(self) -> None:
        m = ModelInfo(name="x", display_name="X")
        text = format_model_option(m, hw=self._BIG_HW)
        assert "?" in text


class TestRecentDurationsText:
    def test_empty_message_when_no_data(self) -> None:
        ctrl = MagicMock()
        ctrl.get_recent_durations = MagicMock(return_value=[])
        text = recent_durations_text(ctrl, LayerKind.ASR)
        assert "直近データなし" in text

    def test_average_in_text(self) -> None:
        ctrl = MagicMock()
        ctrl.get_recent_durations = MagicMock(return_value=[100.0, 200.0, 300.0])
        text = recent_durations_text(ctrl, LayerKind.ASR)
        assert "3 件" in text
        assert "200.0 ms" in text


class TestLoadModelAction:
    def test_spawns_thread_and_invokes_reload(self) -> None:
        """`load_model_action` はバックグラウンドで `reload_model_layer` を呼ぶ。

        (旧仕様の `_safe_load_layer` は既ロード時に no-op だったため、モデル選択を
        変えても反映されなかった。今は強制的に evict → 再ロードする `reload_model_layer`
        を呼ぶよう書き換えた)
        """
        import time
        called: list[LayerKind] = []
        ctrl = MagicMock()
        ctrl.reload_model_layer = MagicMock(side_effect=lambda l: called.append(l))

        load_model_action(ctrl, LayerKind.ASR)
        # スレッドの完了を少し待つ
        for _ in range(20):
            if called:
                break
            time.sleep(0.02)
        assert called == [LayerKind.ASR]


class TestVisibleFields:
    def test_unconditional_fields_always_visible(self) -> None:
        # Capture には条件なしのフィールドのみ
        fields = visible_fields(LayerKind.CAPTURE, current_backend="soundcard")
        assert len(fields) >= 1
        labels = [f.label for f in fields]
        assert any("入力バッファ" in l for l in labels)

    def test_sapi_rate_visible_only_with_sapi(self) -> None:
        """SAPI rate は backend=sapi のときだけ出る。"""
        with_sapi = visible_fields(LayerKind.TTS, current_backend="sapi")
        without_sapi = visible_fields(LayerKind.TTS, current_backend="other_tts")
        sapi_labels_with = [f.label for f in with_sapi]
        sapi_labels_without = [f.label for f in without_sapi]
        assert any("読み上げ速度" in l for l in sapi_labels_with)
        assert not any("読み上げ速度" in l for l in sapi_labels_without)

    def test_backend_filter_excludes_other_backends(self) -> None:
        """applies_when_backend で別 backend を渡すと該当エントリは除外される。

        旧テスト「VAD は空」は Phase C2 で auto_load トグル等が入って意味を失ったため、
        フィルタロジック自体を直接検証する形に書き換えた。
        """
        with_silero = visible_fields(LayerKind.VAD, current_backend="silero")
        with_other = visible_fields(LayerKind.VAD, current_backend="other_vad")
        # silero 時に出る auto_load トグルが、other_vad 時には消える
        assert any(f.field_type == "toggle" for f in with_silero)
        assert not any(f.field_type == "toggle" for f in with_other)


class TestFasterWhisperModelDropdown:
    """faster-whisper の model_size を選ぶ dropdown が ASR に出ること。"""

    def test_model_dropdown_visible_with_faster_whisper(self) -> None:
        fields = visible_fields(LayerKind.ASR, current_backend="faster_whisper")
        dropdowns = [f for f in fields if f.field_type == "dropdown"]
        assert len(dropdowns) == 1
        f = dropdowns[0]
        assert f.keys == ("backends_config", "faster_whisper", "model_size")
        assert f.options_fn is not None

    def test_model_dropdown_hidden_for_other_backend(self) -> None:
        fields = visible_fields(LayerKind.ASR, current_backend="some_other_asr")
        assert not any(f.field_type == "dropdown" for f in fields)

    def test_options_fn_returns_model_info_list(self) -> None:
        """options_fn は ModelInfo のリストを返し、small/medium/large 等を含む。"""
        fields = visible_fields(LayerKind.ASR, current_backend="faster_whisper")
        dropdown = next(f for f in fields if f.field_type == "dropdown")
        models = dropdown.options_fn(MagicMock(), LayerKind.ASR)
        assert len(models) >= 3
        names = [m.name for m in models]
        assert "small" in names
        assert "medium" in names


class TestExpectedFields:
    """主要なフィールドが存在することを確認(意図しない削除を防ぐ)。"""

    def test_capture_has_input_buffer(self) -> None:
        fields = visible_fields(LayerKind.CAPTURE, current_backend="soundcard")
        keys = [f.keys for f in fields]
        assert ("pipeline", "captured_queue_max_bytes") in keys

    def test_asr_has_recognized_queue_size(self) -> None:
        fields = visible_fields(LayerKind.ASR, current_backend="faster_whisper")
        keys = [f.keys for f in fields]
        assert ("pipeline", "recognized_queue_size") in keys

    def test_translator_has_translated_queue_size(self) -> None:
        fields = visible_fields(LayerKind.TRANSLATOR, current_backend="nllb200")
        keys = [f.keys for f in fields]
        assert ("pipeline", "translated_queue_size") in keys

    def test_output_has_synthesized_buffer(self) -> None:
        fields = visible_fields(LayerKind.OUTPUT, current_backend="soundcard")
        keys = [f.keys for f in fields]
        assert ("pipeline", "synthesized_queue_max_bytes") in keys

    def test_tts_sapi_has_rate(self) -> None:
        fields = visible_fields(LayerKind.TTS, current_backend="sapi")
        keys = [f.keys for f in fields]
        assert ("backends_config", "sapi", "rate") in keys
