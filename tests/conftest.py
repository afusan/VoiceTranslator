"""pytest 共通フィクスチャ。

役割: テスト全体で使う一時ディレクトリやテスト用ロガーなど共通の前提を提供。
"""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _reset_ui_locale():
    """各テスト後に UI ロケールを ja に戻す(set_locale のグローバル汚染でテスト間順序依存を防ぐ)。"""
    yield
    try:
        from voice_translator.gui import i18n

        i18n.set_locale("ja")
    except Exception:  # noqa: BLE001 - i18n 未 import 環境でも無害
        pass


@pytest.fixture()
def tmp_config_path(tmp_path: Path) -> Path:
    """設定YAMLの一時パス。"""
    return tmp_path / "config.yaml"


@pytest.fixture()
def tmp_jsonl_path(tmp_path: Path) -> Path:
    """翻訳jsonlの一時パス。"""
    return tmp_path / "history.jsonl"
