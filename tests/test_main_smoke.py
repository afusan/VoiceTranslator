"""__main__ のスモークテスト。GUI 起動はしないが、参照/構文エラーがないことを確認する。"""

from __future__ import annotations


class TestMainImportable:
    def test_module_imports(self) -> None:
        import voice_translator.__main__ as m

        assert hasattr(m, "main"), "main() がエクスポートされていない"
        assert callable(m.main)

    def test_default_config_path_is_yaml(self) -> None:
        from voice_translator.__main__ import _default_config_path

        p = _default_config_path()
        assert str(p).endswith(".yaml")
