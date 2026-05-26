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


class TestConfigStorePersistence:
    def test_save_and_load_roundtrip(self, tmp_config_path: Path) -> None:
        store = ConfigStore(tmp_config_path)
        store.set("languages", "src", "en")
        store.set("languages", "tgt", "ja")
        store.save()

        store2 = ConfigStore(tmp_config_path)
        store2.load()
        assert store2.get("languages", "src") == "en"
        assert store2.get("languages", "tgt") == "ja"

    def test_load_nonexistent_file_keeps_defaults(self, tmp_config_path: Path) -> None:
        # ファイル未作成
        store = ConfigStore(tmp_config_path)
        store.load()
        assert store.get("backends", "asr") == "faster_whisper"

    def test_load_merges_with_defaults(self, tmp_config_path: Path) -> None:
        """ファイル内のキーは優先、未指定キーは既定値で補完される。"""
        tmp_config_path.write_text("languages:\n  src: en\n", encoding="utf-8")
        store = ConfigStore(tmp_config_path)
        store.load()
        assert store.get("languages", "src") == "en"
        # 未指定キーは既定値が残る
        assert store.get("languages", "tgt") == "ja"
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
