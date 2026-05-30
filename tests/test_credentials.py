"""credentials の単体テスト。

R-5 の 3 層運用に対応:
- keyring 経路: `InMemoryKeyring` を `keyring.set_keyring` 経由で注入して検証
- 平文ファイル経路: `use_local_file=True` 強制 or keyring 失敗時の fallback を検証
- 失敗注入: `FailKeyring` で set/get/delete が黙って fallback することを検証
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests._fixtures import FailKeyring, InMemoryKeyring
from voice_translator.common.credentials import CredentialsStore


@pytest.fixture()
def fake_keyring(monkeypatch):
    """`InMemoryKeyring` を `keyring.set_keyring` で注入する。"""
    import keyring

    fake = InMemoryKeyring()
    keyring.set_keyring(fake)
    yield fake
    # 後始末: グローバル keyring を実装デフォルトに戻す試行(失敗してもテストは無関係)
    try:
        keyring.core.init_backend()
    except Exception:  # noqa: BLE001
        pass


@pytest.fixture()
def failing_keyring(monkeypatch):
    """全操作で例外を投げる keyring を注入する。"""
    import keyring

    fake = FailKeyring()
    keyring.set_keyring(fake)
    yield fake
    try:
        keyring.core.init_backend()
    except Exception:  # noqa: BLE001
        pass


class TestModeDetection:
    def test_use_local_file_forces_file_mode(self, fake_keyring, tmp_path: Path) -> None:
        """フラグが立っていれば keyring が正常でも file モードを選ぶ。"""
        store = CredentialsStore(
            use_local_file=True, file_path=tmp_path / "secrets"
        )
        assert store.mode == "file"

    def test_keyring_mode_when_available(self, fake_keyring, tmp_path: Path) -> None:
        store = CredentialsStore(
            use_local_file=False, file_path=tmp_path / "secrets"
        )
        assert store.mode == "keyring"

    def test_file_mode_when_keyring_probe_fails(
        self, failing_keyring, tmp_path: Path
    ) -> None:
        """probe が失敗したら file モードに落ちる。"""
        store = CredentialsStore(
            use_local_file=False, file_path=tmp_path / "secrets"
        )
        assert store.mode == "file"


class TestKeyringMode:
    def test_set_then_get_roundtrip(self, fake_keyring, tmp_path: Path) -> None:
        store = CredentialsStore(
            use_local_file=False, file_path=tmp_path / "secrets"
        )
        store.set("openai", "api_key", "sk-abc")
        assert store.get("openai", "api_key") == "sk-abc"

    def test_get_returns_none_when_unset(self, fake_keyring, tmp_path: Path) -> None:
        store = CredentialsStore(
            use_local_file=False, file_path=tmp_path / "secrets"
        )
        assert store.get("openai", "api_key") is None

    def test_delete_removes_value(self, fake_keyring, tmp_path: Path) -> None:
        store = CredentialsStore(
            use_local_file=False, file_path=tmp_path / "secrets"
        )
        store.set("deepl", "api_key", "v")
        store.delete("deepl", "api_key")
        assert store.get("deepl", "api_key") is None

    def test_delete_unset_is_safe(self, fake_keyring, tmp_path: Path) -> None:
        store = CredentialsStore(
            use_local_file=False, file_path=tmp_path / "secrets"
        )
        # 存在しない key の delete は例外を出さない
        store.delete("nope", "nope")

    def test_set_empty_value_deletes(self, fake_keyring, tmp_path: Path) -> None:
        store = CredentialsStore(
            use_local_file=False, file_path=tmp_path / "secrets"
        )
        store.set("openai", "api_key", "v")
        store.set("openai", "api_key", "")
        assert store.get("openai", "api_key") is None


class TestFileMode:
    def test_set_creates_file_with_json(self, tmp_path: Path) -> None:
        path = tmp_path / "secrets"
        store = CredentialsStore(use_local_file=True, file_path=path)
        store.set("deepl", "api_key", "abc")
        assert path.exists()
        # ファイルに値が入っていること(キー名はそのまま記録される)
        text = path.read_text(encoding="utf-8")
        assert "deepl" in text
        assert "abc" in text

    def test_get_after_set(self, tmp_path: Path) -> None:
        store = CredentialsStore(
            use_local_file=True, file_path=tmp_path / "secrets"
        )
        store.set("deepl", "api_key", "abc")
        assert store.get("deepl", "api_key") == "abc"

    def test_persists_across_instances(self, tmp_path: Path) -> None:
        """別インスタンスから同じファイルを読めば値が見える(永続化確認)。"""
        path = tmp_path / "secrets"
        a = CredentialsStore(use_local_file=True, file_path=path)
        a.set("deepl", "api_key", "xyz")
        b = CredentialsStore(use_local_file=True, file_path=path)
        assert b.get("deepl", "api_key") == "xyz"

    def test_delete_removes_entry(self, tmp_path: Path) -> None:
        store = CredentialsStore(
            use_local_file=True, file_path=tmp_path / "secrets"
        )
        store.set("a", "k", "v")
        store.delete("a", "k")
        assert store.get("a", "k") is None

    def test_multiple_backends_isolated(self, tmp_path: Path) -> None:
        store = CredentialsStore(
            use_local_file=True, file_path=tmp_path / "secrets"
        )
        store.set("openai", "api_key", "x")
        store.set("deepl", "api_key", "y")
        assert store.get("openai", "api_key") == "x"
        assert store.get("deepl", "api_key") == "y"

    def test_missing_file_get_returns_none(self, tmp_path: Path) -> None:
        store = CredentialsStore(
            use_local_file=True, file_path=tmp_path / "secrets"
        )
        assert store.get("nope", "key") is None

    def test_corrupt_json_treated_as_empty(self, tmp_path: Path) -> None:
        path = tmp_path / "secrets"
        path.write_text("not valid json", encoding="utf-8")
        store = CredentialsStore(use_local_file=True, file_path=path)
        # 例外なく None
        assert store.get("a", "k") is None
        # 書き込みは新規 JSON で上書き
        store.set("a", "k", "v")
        assert store.get("a", "k") == "v"


class TestFailKeyringFallback:
    """`FailKeyring` で keyring 操作が失敗したとき、平文ファイルに退避すること。"""

    def test_mode_detects_as_file_when_keyring_fails(
        self, failing_keyring, tmp_path: Path
    ) -> None:
        store = CredentialsStore(
            use_local_file=False, file_path=tmp_path / "secrets"
        )
        assert store.mode == "file"
        # 通常の set/get/delete が動く
        store.set("a", "k", "v")
        assert store.get("a", "k") == "v"
