"""SettingsPanel の入力言語連動ロジックの単体テスト。

UI 全体ではなく、`_refresh_input_language_choices` と `_notify_lang_fallback` の
振る舞いに絞って検証する(customtkinter widget は MagicMock で代替)。
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest


@pytest.fixture()
def stub_panel(monkeypatch):
    """SettingsPanel の `__init__` を経由せず、必要な属性だけ持つ shim を作る。

    `_refresh_input_language_choices` / `_notify_lang_fallback` は self に対する
    属性アクセスだけで動くので、MagicMock + 必要な実メソッドの bound で十分。
    """
    from voice_translator.gui.settings_panel import SettingsPanel

    shim = MagicMock(spec=SettingsPanel)
    # 実メソッドを使う(self 経由で呼ばれる)
    shim._refresh_input_language_choices = SettingsPanel._refresh_input_language_choices.__get__(shim)
    shim._notify_lang_fallback = SettingsPanel._notify_lang_fallback.__get__(shim)
    shim._on_src_lang_changed = SettingsPanel._on_src_lang_changed.__get__(shim)
    # widget 群はモック
    shim._src_dropdown = MagicMock(name="src_dropdown")
    shim._src_var = MagicMock(name="src_var")
    shim._controller = MagicMock(name="controller")
    shim._banner = MagicMock(name="banner")
    return shim


class TestRefreshInputLanguageChoices:
    def test_uses_backend_supported_languages_when_available(self, stub_panel) -> None:
        stub_panel._controller.get_supported_input_languages.return_value = ["en", "ja", "fr"]
        stub_panel._controller.supports_auto_detect.return_value = True
        stub_panel._controller.get_setting.return_value = "en"

        stub_panel._refresh_input_language_choices("fake_backend", notify_fallback=True)

        # configure に渡された values を取り出す
        configure_kwargs = stub_panel._src_dropdown.configure.call_args.kwargs
        labels = configure_kwargs["values"]
        # auto が先頭に入り、残りはソート順
        assert labels[0] == "auto (Auto-detect)"
        assert "en (English)" in labels
        assert "ja (Japanese)" in labels
        assert "fr (French)" in labels

    def test_no_auto_when_backend_not_supports_auto(self, stub_panel) -> None:
        stub_panel._controller.get_supported_input_languages.return_value = ["en"]
        stub_panel._controller.supports_auto_detect.return_value = False
        stub_panel._controller.get_setting.return_value = "en"

        stub_panel._refresh_input_language_choices("fake_backend", notify_fallback=True)

        labels = stub_panel._src_dropdown.configure.call_args.kwargs["values"]
        assert "auto (Auto-detect)" not in labels

    def test_fallback_when_backend_returns_empty(self, stub_panel) -> None:
        stub_panel._controller.get_supported_input_languages.return_value = []
        stub_panel._controller.supports_auto_detect.return_value = False
        stub_panel._controller.get_setting.return_value = "en"

        stub_panel._refresh_input_language_choices("unknown_backend", notify_fallback=False)

        # fallback 候補が使われる(MVP セット相当)
        labels = stub_panel._src_dropdown.configure.call_args.kwargs["values"]
        assert "en (English)" in labels

    def test_keeps_current_setting_if_supported(self, stub_panel) -> None:
        stub_panel._controller.get_supported_input_languages.return_value = ["en", "ja"]
        stub_panel._controller.supports_auto_detect.return_value = True
        stub_panel._controller.get_setting.return_value = "ja"

        stub_panel._refresh_input_language_choices("fake_backend", notify_fallback=True)

        # 現在値が新リストに含まれるので保持(set_setting は呼ばれない)
        stub_panel._controller.set_setting.assert_not_called()
        # 表示形式に更新される
        stub_panel._src_var.set.assert_called_with("ja (Japanese)")

    def test_falls_back_to_auto_when_current_unsupported_and_auto_available(
        self, stub_panel
    ) -> None:
        stub_panel._controller.get_supported_input_languages.return_value = ["en"]
        stub_panel._controller.supports_auto_detect.return_value = True
        stub_panel._controller.get_setting.return_value = "fr"  # 非対応

        stub_panel._refresh_input_language_choices("fake_backend", notify_fallback=True)

        # auto に fallback
        stub_panel._controller.set_setting.assert_called_with("languages", "src", "auto")
        stub_panel._src_var.set.assert_called_with("auto (Auto-detect)")
        # 通知バナーが呼ばれる
        stub_panel._banner.show_warning.assert_called_once()

    def test_falls_back_to_first_lang_when_no_auto(self, stub_panel) -> None:
        stub_panel._controller.get_supported_input_languages.return_value = ["en", "ja"]
        stub_panel._controller.supports_auto_detect.return_value = False
        stub_panel._controller.get_setting.return_value = "fr"  # 非対応

        stub_panel._refresh_input_language_choices("fake_backend", notify_fallback=True)

        # ソート済み先頭(= "en")に fallback
        stub_panel._controller.set_setting.assert_called_with("languages", "src", "en")
        stub_panel._banner.show_warning.assert_called_once()

    def test_no_notification_when_notify_fallback_false(self, stub_panel) -> None:
        """起動時の初回構築では通知を出さない(設定 OK のときも fallback のときも)。"""
        stub_panel._controller.get_supported_input_languages.return_value = ["en"]
        stub_panel._controller.supports_auto_detect.return_value = False
        stub_panel._controller.get_setting.return_value = "fr"

        stub_panel._refresh_input_language_choices("fake_backend", notify_fallback=False)

        # fallback はするが通知はしない
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
