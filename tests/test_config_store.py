"""ConfigStore の単体テスト。"""

from __future__ import annotations

from pathlib import Path

import pytest

from voice_translator.common.config_store import DEFAULT_CONFIG, ConfigStore
from voice_translator.common.errors import FatalError


class TestConfigStoreDefaults:
    def test_default_values_loaded(self, tmp_config_path: Path) -> None:
        store = ConfigStore(tmp_config_path)
        assert store.get("languages", "src") == DEFAULT_CONFIG["languages"]["src"]
        assert store.get("backends", "asr") == "faster_whisper"

    def test_get_with_default(self, tmp_config_path: Path) -> None:
        store = ConfigStore(tmp_config_path)
        assert store.get("missing", "key", default="x") == "x"


class TestConfigStoreSetGet:
    def test_set_then_get(self, tmp_config_path: Path) -> None:
        store = ConfigStore(tmp_config_path)
        store.set("languages", "src", "en")
        assert store.get("languages", "src") == "en"

    def test_set_creates_nested_dict(self, tmp_config_path: Path) -> None:
        store = ConfigStore(tmp_config_path)
        store.set("new_section", "deep", "key", 42)
        assert store.get("new_section", "deep", "key") == 42

    def test_set_requires_key_and_value(self, tmp_config_path: Path) -> None:
        store = ConfigStore(tmp_config_path)
        with pytest.raises(ValueError):
            store.set("only_one_arg")


class TestSaveTransform:
    """save(transform=...): 書き出し直前のコピー変換(in-memory は不変)。"""

    def test_transform_applies_to_written_copy_only(
        self, tmp_config_path: Path
    ) -> None:
        import yaml

        store = ConfigStore(tmp_config_path)
        store.set("devices", "input", "pid-42")

        def strip(data):
            data["devices"]["input"] = ""
            return data

        store.save(transform=strip)

        # in-memory は変換の影響を受けない
        assert store.get("devices", "input") == "pid-42"
        # ファイル側には変換結果が書かれる
        written = yaml.safe_load(tmp_config_path.read_text(encoding="utf-8"))
        assert written["devices"]["input"] == ""

    def test_save_without_transform_writes_data_as_is(
        self, tmp_config_path: Path
    ) -> None:
        import yaml

        store = ConfigStore(tmp_config_path)
        store.set("devices", "input", "pid-42")
        store.save()
        written = yaml.safe_load(tmp_config_path.read_text(encoding="utf-8"))
        assert written["devices"]["input"] == "pid-42"


class TestConfigStorePersistence:
    def test_save_and_load_roundtrip(self, tmp_config_path: Path) -> None:
        # 内部標準は ISO 639-3。正準コードはそのまま往復する。
        store = ConfigStore(tmp_config_path)
        store.set("languages", "src", "eng")
        store.set("languages", "tgt", "jpn")
        store.save()

        store2 = ConfigStore(tmp_config_path)
        store2.load()
        assert store2.get("languages", "src") == "eng"
        assert store2.get("languages", "tgt") == "jpn"

    def test_load_normalizes_legacy_639_1(self, tmp_config_path: Path) -> None:
        """旧版が 639-1 で保存した config は load 時に正準(639-3)へ正規化される。"""
        tmp_config_path.write_text(
            "languages:\n  src: en\n  tgt: ja\n", encoding="utf-8"
        )
        store = ConfigStore(tmp_config_path)
        store.load()
        assert store.get("languages", "src") == "eng"
        assert store.get("languages", "tgt") == "jpn"

    def test_load_nonexistent_file_keeps_defaults(self, tmp_config_path: Path) -> None:
        # ファイル未作成
        store = ConfigStore(tmp_config_path)
        store.load()
        assert store.get("backends", "asr") == "faster_whisper"

    def test_load_merges_with_defaults(self, tmp_config_path: Path) -> None:
        """ファイル内のキーは優先、未指定キーは既定値で補完される。"""
        tmp_config_path.write_text("languages:\n  src: eng\n", encoding="utf-8")
        store = ConfigStore(tmp_config_path)
        store.load()
        assert store.get("languages", "src") == "eng"
        # 未指定キーは既定値が残る(既定 tgt は正準 639-3)
        assert store.get("languages", "tgt") == "jpn"
        assert store.get("backends", "asr") == "faster_whisper"

    def test_load_broken_yaml_raises_fatal(self, tmp_config_path: Path) -> None:
        tmp_config_path.write_text("this: is: : broken: :\n  -bad", encoding="utf-8")
        store = ConfigStore(tmp_config_path)
        with pytest.raises(FatalError):
            store.load()

    def test_load_non_dict_raises_fatal(self, tmp_config_path: Path) -> None:
        tmp_config_path.write_text("- a\n- b\n", encoding="utf-8")
        store = ConfigStore(tmp_config_path)
        with pytest.raises(FatalError):
            store.load()
