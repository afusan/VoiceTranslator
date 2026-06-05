"""SettingsPanel の 3 セクション分割の振る舞いテスト(P1)。

実 Tk を立てて SettingsPanel を構築し、以下を検証する:
- 3 つの CollapsibleSection(バックエンド / デバイス / 翻訳)が生成される
- 初期開閉状態が ConfigStore の `ui.collapsed.{backends,devices,languages}` から読まれる
- セクションを toggle すると ConfigStore に保存される(キー独立)
- 既存の `ui.collapsed.settings_panel` キーは新方式で参照されない

ヘッドレス環境(GUI 表示が立たない CI)では skip。
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest


def _make_root():
    """customtkinter のルートを立てる。ヘッドレス環境では skip。"""
    import customtkinter as ctk

    try:
        root = ctk.CTk()
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"GUI 表示環境が無いため skip: {e}")
    root.withdraw()
    return root


@pytest.fixture()
def root():
    r = _make_root()
    yield r
    try:
        r.destroy()
    except Exception:  # noqa: BLE001
        pass


def _make_controller(collapsed: dict[tuple[str, ...], bool] | None = None) -> MagicMock:
    """SettingsPanel に注入する AppController モックを作る。

    `collapsed` を渡すと、`get_setting("ui","collapsed",<name>, default=False)` で
    そのキーに対応する値を返す。それ以外の get_setting はデフォルト挙動。
    """
    from voice_translator.common.types import LayerKind, ModelStatus

    controller = MagicMock()
    collapsed = collapsed or {}

    def get_setting(*keys, default=None):
        if keys in collapsed:
            return collapsed[keys]
        # backends.* と languages.* は空文字 or default
        if keys[0] == "backends":
            return ""
        if keys[0] == "languages":
            return default if default is not None else "auto"
        if keys[0] == "log" and keys[1] == "directory":
            return "./logs"
        if keys[0] == "devices":
            return None
        return default

    controller.get_setting.side_effect = get_setting
    controller.list_backends.return_value = ["(未登録)"]
    controller.list_capture_sources.return_value = []
    controller.list_output_devices.return_value = []
    controller.get_all_model_statuses.return_value = {
        layer: ModelStatus.INIT for layer in LayerKind
    }
    controller.get_supported_input_languages.return_value = []
    controller.get_supported_target_languages.return_value = []
    controller.get_supported_output_languages.return_value = []
    controller.supports_auto_detect.return_value = False
    controller.get_layer_device.return_value = None
    controller.get_backend_capability_hint.return_value = None
    return controller


class TestSectionConstruction:
    """3 セクションが構築されることの確認。"""

    def test_three_sections_exist(self, root) -> None:
        from voice_translator.gui.settings_panel import SettingsPanel

        panel = SettingsPanel(root, _make_controller())
        assert panel._backends_section is not None  # noqa: SLF001
        assert panel._devices_section is not None  # noqa: SLF001
        assert panel._languages_section is not None  # noqa: SLF001

    def test_section_titles(self, root) -> None:
        """セクションヘッダのタイトルがそれぞれ「バックエンド」「デバイス」「翻訳」。"""
        from voice_translator.gui.settings_panel import SettingsPanel

        panel = SettingsPanel(root, _make_controller())
        # ヘッダボタンの text には ▼/▶ + " " + title が入る
        assert "バックエンド" in panel._backends_section._header_btn.cget("text")  # noqa: SLF001
        assert "デバイス" in panel._devices_section._header_btn.cget("text")  # noqa: SLF001
        assert "翻訳" in panel._languages_section._header_btn.cget("text")  # noqa: SLF001


class TestInitialOpenState:
    """初期状態が ConfigStore の値を反映していること。"""

    def test_default_all_open(self, root) -> None:
        """`ui.collapsed.*` が未設定なら 3 セクションとも初期は開。"""
        from voice_translator.gui.settings_panel import SettingsPanel

        panel = SettingsPanel(root, _make_controller())
        assert panel._backends_section.is_open is True  # noqa: SLF001
        assert panel._devices_section.is_open is True  # noqa: SLF001
        assert panel._languages_section.is_open is True  # noqa: SLF001

    def test_individual_collapsed_state(self, root) -> None:
        """各セクションは独立して閉じた状態で起動できる。"""
        from voice_translator.gui.settings_panel import SettingsPanel

        controller = _make_controller(collapsed={
            ("ui", "collapsed", "backends"): True,  # 閉じ
            ("ui", "collapsed", "devices"): False,  # 開
            ("ui", "collapsed", "languages"): True,  # 閉じ
        })
        panel = SettingsPanel(root, controller)
        assert panel._backends_section.is_open is False  # noqa: SLF001
        assert panel._devices_section.is_open is True  # noqa: SLF001
        assert panel._languages_section.is_open is False  # noqa: SLF001


class TestPersistOnToggle:
    """セクションを toggle すると ConfigStore に保存される。"""

    def test_close_backends_persists(self, root) -> None:
        from voice_translator.gui.settings_panel import SettingsPanel

        controller = _make_controller()
        panel = SettingsPanel(root, controller)
        # 開いている状態から閉じる
        panel._backends_section.close()  # noqa: SLF001
        controller.set_setting.assert_any_call("ui", "collapsed", "backends", True)

    def test_close_devices_persists(self, root) -> None:
        from voice_translator.gui.settings_panel import SettingsPanel

        controller = _make_controller()
        panel = SettingsPanel(root, controller)
        panel._devices_section.close()  # noqa: SLF001
        controller.set_setting.assert_any_call("ui", "collapsed", "devices", True)

    def test_close_languages_persists(self, root) -> None:
        from voice_translator.gui.settings_panel import SettingsPanel

        controller = _make_controller()
        panel = SettingsPanel(root, controller)
        panel._languages_section.close()  # noqa: SLF001
        controller.set_setting.assert_any_call("ui", "collapsed", "languages", True)

    def test_open_after_close_persists_false(self, root) -> None:
        """閉じた後に開くと False(=畳まれていない)で保存される。"""
        from voice_translator.gui.settings_panel import SettingsPanel

        controller = _make_controller(collapsed={
            ("ui", "collapsed", "backends"): True,
        })
        panel = SettingsPanel(root, controller)
        panel._backends_section.open()  # noqa: SLF001
        controller.set_setting.assert_any_call("ui", "collapsed", "backends", False)

    def test_persist_failure_is_swallowed(self, root) -> None:
        """ConfigStore への書き込み失敗が UI 操作を壊さない。"""
        from voice_translator.gui.settings_panel import SettingsPanel

        controller = _make_controller()
        panel = SettingsPanel(root, controller)
        controller.set_setting.side_effect = RuntimeError("broken store")
        # toggle 自体は例外を出さない
        panel._backends_section.toggle()  # noqa: SLF001
        assert panel._backends_section.is_open is False  # noqa: SLF001


class TestNoLegacyKey:
    """旧 `ui.collapsed.settings_panel` キーは新方式で参照されない(マイグレーション不要)。"""

    def test_legacy_key_ignored(self, root) -> None:
        from voice_translator.gui.settings_panel import SettingsPanel

        # 旧キーに True が入っていても、新キーで判定されるので初期は「開」
        controller = _make_controller(collapsed={
            ("ui", "collapsed", "settings_panel"): True,  # 旧キー
        })
        panel = SettingsPanel(root, controller)
        assert panel._backends_section.is_open is True  # noqa: SLF001
        assert panel._devices_section.is_open is True  # noqa: SLF001
        assert panel._languages_section.is_open is True  # noqa: SLF001
