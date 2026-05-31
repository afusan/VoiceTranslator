"""CredentialField の `field_type="file"` 拡張に関する単体テスト。

CredentialDialog の widget レンダリングは customtkinter 依存で重いので、
ここでは CredentialField のデータ構造 + `_pick_file` のロジックに絞って検証する。
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from voice_translator.common.types import CredentialField


class TestCredentialFieldDataclass:
    def test_default_field_type_is_text(self) -> None:
        f = CredentialField(key_name="x", label="X")
        assert f.field_type == "text"
        assert f.file_extensions == ()

    def test_file_type_with_extensions(self) -> None:
        f = CredentialField(
            key_name="json_path",
            label="サービスアカウント JSON",
            secret=False,
            field_type="file",
            file_extensions=(("JSON", "*.json"), ("All", "*.*")),
        )
        assert f.field_type == "file"
        assert f.file_extensions == (("JSON", "*.json"), ("All", "*.*"))

    def test_immutable(self) -> None:
        """frozen dataclass: 後付けで属性変更できない。"""
        f = CredentialField(key_name="x", label="X")
        with pytest.raises((AttributeError, Exception)):
            f.field_type = "file"  # type: ignore[misc]


class TestPickFileLogic:
    """CredentialDialog._pick_file の挙動(filedialog はモック)。"""

    @pytest.fixture()
    def shim(self):
        """`CredentialDialog._pick_file` を裸の self に bind する shim。"""
        from voice_translator.gui.credential_dialog import CredentialDialog

        shim = MagicMock(spec=CredentialDialog)
        shim._pick_file = CredentialDialog._pick_file.__get__(shim)
        return shim

    def test_selected_path_is_set_to_var(self, shim, monkeypatch) -> None:
        monkeypatch.setattr(
            "tkinter.filedialog.askopenfilename",
            lambda **kw: "C:/path/to/key.json",
        )

        var = MagicMock()
        field = CredentialField(
            key_name="json_path", label="JSON",
            field_type="file",
            file_extensions=(("JSON", "*.json"),),
        )
        shim._pick_file(var, field)
        var.set.assert_called_once_with("C:/path/to/key.json")

    def test_cancel_does_not_change_var(self, shim, monkeypatch) -> None:
        """ダイアログでキャンセル(空文字)→ var.set は呼ばれない。"""
        monkeypatch.setattr(
            "tkinter.filedialog.askopenfilename",
            lambda **kw: "",
        )

        var = MagicMock()
        field = CredentialField(
            key_name="json_path", label="JSON", field_type="file",
        )
        shim._pick_file(var, field)
        var.set.assert_not_called()

    def test_default_filetypes_when_unspecified(self, shim, monkeypatch) -> None:
        captured: dict = {}

        def _capture(**kwargs):
            captured.update(kwargs)
            return ""  # キャンセル扱い

        monkeypatch.setattr("tkinter.filedialog.askopenfilename", _capture)

        var = MagicMock()
        field = CredentialField(key_name="x", label="X", field_type="file")
        shim._pick_file(var, field)
        # file_extensions 未指定 → All files デフォルト
        assert captured["filetypes"] == [("All files", "*.*")]
