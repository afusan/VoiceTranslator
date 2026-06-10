"""SettingsPanel の言語連動の「配線」テスト(wiring smoke)。

P1(refactor-ui-3move)で言語候補・fallback の判断ロジックは
`gui/logic/language_choices.py` に移動した。判断の全分岐は
tests/test_logic_language_choices.py(純 small)で検証する。
本ファイルは「View が logic の計算結果を widget / controller / banner に
正しく配線しているか」の代表ケースだけを残す。

shim 方式: SettingsPanel の `__init__`(GUI 構築)を経由せず、検証対象メソッドを
MagicMock に bind して呼ぶ。配線先(dropdown / var / controller / banner)の
呼び出しを観察する。
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest


def _bind(shim, *method_names: str):
    """SettingsPanel の実メソッドを shim に bind する。"""
    from voice_translator.gui.settings_panel import SettingsPanel

    for name in method_names:
        setattr(shim, name, getattr(SettingsPanel, name).__get__(shim))


@pytest.fixture()
def stub_panel():
    """入力言語(src)連動の配線検証用 shim。"""
    from voice_translator.gui.settings_panel import SettingsPanel

    shim = MagicMock(spec=SettingsPanel)
    _bind(
        shim,
        "_refresh_input_language_choices",
        "_notify_lang_fallback",
        "_notify_warning",
        "_on_src_lang_changed",
    )
    shim._src_dropdown = MagicMock(name="src_dropdown")
    shim._src_var = MagicMock(name="src_var")
    shim._controller = MagicMock(name="controller")
    shim._banner = MagicMock(name="banner")
    return shim


class TestSrcLanguageWiring:
    def test_rebuild_applies_choices_and_selection_to_widgets(self, stub_panel) -> None:
        """logic の計算結果(候補 + 選択値)が dropdown / StringVar に反映される。"""
        stub_panel._controller.get_supported_input_languages.return_value = ["en", "ja"]
        stub_panel._controller.supports_auto_detect.return_value = True
        stub_panel._controller.get_setting.return_value = "ja"

        stub_panel._refresh_input_language_choices("fake_backend", notify_fallback=True)

        labels = stub_panel._src_dropdown.configure.call_args.kwargs["values"]
        assert labels[0] == "auto (Auto-detect)"
        assert "ja (Japanese)" in labels
        stub_panel._src_var.set.assert_called_with("ja (Japanese)")
        # 現在値が対応言語なので設定の書き戻しは起きない
        stub_panel._controller.set_setting.assert_not_called()

    def test_fallback_writes_setting_and_shows_banner(self, stub_panel) -> None:
        """fallback 発生時は set_setting + 警告バナーまで配線される。"""
        stub_panel._controller.get_supported_input_languages.return_value = ["en"]
        stub_panel._controller.supports_auto_detect.return_value = True
        stub_panel._controller.get_setting.return_value = "fr"  # 非対応

        stub_panel._refresh_input_language_choices("fake_backend", notify_fallback=True)

        stub_panel._controller.set_setting.assert_called_with("languages", "src", "auto")
        stub_panel._src_var.set.assert_called_with("auto (Auto-detect)")
        stub_panel._banner.show_warning.assert_called_once()

    def test_no_notification_when_notify_fallback_false(self, stub_panel) -> None:
        """起動時の初回構築では fallback しても通知を出さない。"""
        stub_panel._controller.get_supported_input_languages.return_value = ["en"]
        stub_panel._controller.supports_auto_detect.return_value = False
        stub_panel._controller.get_setting.return_value = "fr"

        stub_panel._refresh_input_language_choices("fake_backend", notify_fallback=False)

        stub_panel._controller.set_setting.assert_called_with("languages", "src", "en")
        stub_panel._banner.show_warning.assert_not_called()

    def test_dropdown_missing_is_noop(self, stub_panel) -> None:
        """初期化未完了時(dropdown が None)は何もしない。"""
        stub_panel._src_dropdown = None
        # 例外を出さないこと
        stub_panel._refresh_input_language_choices("any", notify_fallback=True)


class TestOnSrcLangChanged:
    def test_parses_label_to_code_and_saves(self, stub_panel) -> None:
        stub_panel._on_src_lang_changed("en (English)")
        stub_panel._controller.set_setting.assert_called_with("languages", "src", "en")

    def test_handles_bare_code(self, stub_panel) -> None:
        stub_panel._on_src_lang_changed("auto")
        stub_panel._controller.set_setting.assert_called_with("languages", "src", "auto")


@pytest.fixture()
def stub_tgt_panel():
    """出力言語(tgt)連動の配線検証用 shim。"""
    from voice_translator.gui.settings_panel import SettingsPanel

    shim = MagicMock(spec=SettingsPanel)
    _bind(
        shim,
        "_refresh_target_language_choices",
        "_notify_tgt_lang_fallback",
        "_notify_warning",
    )
    shim._tgt_dropdown = MagicMock(name="tgt_dropdown")
    shim._tgt_var = MagicMock(name="tgt_var")
    shim._controller = MagicMock(name="controller")
    shim._banner = MagicMock(name="banner")
    return shim


class TestTgtLanguageWiring:
    def test_fallback_writes_setting_banner_and_chains_tts_check(
        self, stub_tgt_panel,
    ) -> None:
        """fallback 時: set_setting + バナー + TTS 互換チェック連鎖まで配線される。"""
        stub_tgt_panel._controller.get_supported_target_languages.return_value = [
            "en", "ja", "fr",
        ]
        stub_tgt_panel._controller.get_setting.return_value = "xx"  # 非対応

        stub_tgt_panel._refresh_target_language_choices(
            "fake_backend", notify_fallback=True
        )

        stub_tgt_panel._controller.set_setting.assert_called_with(
            "languages", "tgt", "ja"
        )
        stub_tgt_panel._banner.show_warning.assert_called_once()
        # tgt が変わったので TTS 互換チェックに連鎖する(shim 上は auto-mock)
        stub_tgt_panel._check_tts_output_lang_compatibility.assert_called_once_with(
            notify_fallback=True
        )

    def test_keeps_current_if_supported(self, stub_tgt_panel) -> None:
        stub_tgt_panel._controller.get_supported_target_languages.return_value = [
            "en", "ja",
        ]
        stub_tgt_panel._controller.get_setting.return_value = "ja"

        stub_tgt_panel._refresh_target_language_choices(
            "fake_backend", notify_fallback=True
        )
        stub_tgt_panel._controller.set_setting.assert_not_called()
        stub_tgt_panel._tgt_var.set.assert_called_with("ja (Japanese)")

    def test_dropdown_missing_is_noop(self, stub_tgt_panel) -> None:
        stub_tgt_panel._tgt_dropdown = None
        stub_tgt_panel._refresh_target_language_choices("any", notify_fallback=True)


@pytest.fixture()
def stub_tts_panel():
    """TTS 互換チェックの配線検証用 shim。"""
    from voice_translator.gui.settings_panel import SettingsPanel

    shim = MagicMock(spec=SettingsPanel)
    _bind(
        shim,
        "_check_tts_output_lang_compatibility",
        "_notify_tts_unsupported_lang",
        "_notify_warning",
    )
    shim._show_message = MagicMock()
    shim._controller = MagicMock(name="controller")
    shim._banner = MagicMock(name="banner")
    return shim


def _make_setting_fn(table: dict):
    """get_setting(*keys, default=None) のモック実装を返す。"""
    def fn(*keys, default=None):
        return table.get(keys, default)
    return fn


class TestTtsCompatibilityWiring:
    def test_warns_when_tts_does_not_support_current_tgt(self, stub_tts_panel) -> None:
        stub_tts_panel._controller.get_setting.side_effect = _make_setting_fn({
            ("backends", "tts"): "fake_tts",
            ("languages", "tgt"): "fr",
        })
        stub_tts_panel._controller.get_supported_output_languages.return_value = [
            "en", "ja",
        ]

        stub_tts_panel._check_tts_output_lang_compatibility(notify_fallback=True)

        stub_tts_panel._banner.show_warning.assert_called_once()

    def test_no_warn_when_notify_fallback_false(self, stub_tts_panel) -> None:
        """起動時の初期化(notify_fallback=False)では対応外でも警告しない。"""
        stub_tts_panel._controller.get_setting.side_effect = _make_setting_fn({
            ("backends", "tts"): "fake_tts",
            ("languages", "tgt"): "fr",
        })
        stub_tts_panel._controller.get_supported_output_languages.return_value = [
            "en", "ja",
        ]

        stub_tts_panel._check_tts_output_lang_compatibility(notify_fallback=False)

        stub_tts_panel._banner.show_warning.assert_not_called()

    def test_no_query_when_tts_none(self, stub_tts_panel) -> None:
        """TTS=(なし) のときは supported の問い合わせ自体を行わない。"""
        stub_tts_panel._controller.get_setting.side_effect = _make_setting_fn({
            ("backends", "tts"): "none",
            ("languages", "tgt"): "fr",
        })

        stub_tts_panel._check_tts_output_lang_compatibility(notify_fallback=True)

        stub_tts_panel._controller.get_supported_output_languages.assert_not_called()
        stub_tts_panel._banner.show_warning.assert_not_called()

    def test_falls_back_to_show_message_when_banner_missing(
        self, stub_tts_panel,
    ) -> None:
        """banner=None でも _show_message に落ちて例外にならない。"""
        stub_tts_panel._banner = None
        stub_tts_panel._controller.get_setting.side_effect = _make_setting_fn({
            ("backends", "tts"): "fake_tts",
            ("languages", "tgt"): "fr",
        })
        stub_tts_panel._controller.get_supported_output_languages.return_value = [
            "en", "ja",
        ]

        stub_tts_panel._check_tts_output_lang_compatibility(notify_fallback=True)

        stub_tts_panel._show_message.assert_called_once()
