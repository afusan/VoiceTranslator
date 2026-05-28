"""layer_settings_schema の単体テスト。

GUI ダイアログ本体(customtkinter)はメインスレッド/Tk が必要なため、
ここではスキーマ層(値変換・条件付き表示)のみをテストする。
"""

from __future__ import annotations

import pytest

from voice_translator.common.types import LayerKind
from voice_translator.gui.layer_settings_schema import (
    LAYER_SETTINGS,
    SettingField,
    parse_field_value,
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
                assert len(f.keys) >= 2, f"{layer}: keys は最低2階層必要({f.keys})"
                assert f.label.strip(), f"{layer}: label が空"
                assert f.field_type in ("int", "float", "str", "bool")


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

    def test_empty_layer_returns_empty(self) -> None:
        # VAD はまだ編集可能項目を定義していない → 空リスト
        assert visible_fields(LayerKind.VAD, current_backend="silero") == []


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
