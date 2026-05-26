"""pytest 共通フィクスチャ。

役割: テスト全体で使う一時ディレクトリやテスト用ロガーなど共通の前提を提供。
"""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture()
def tmp_config_path(tmp_path: Path) -> Path:
    """設定YAMLの一時パス。"""
    return tmp_path / "config.yaml"


@pytest.fixture()
def tmp_jsonl_path(tmp_path: Path) -> Path:
    """翻訳jsonlの一時パス。"""
    return tmp_path / "history.jsonl"
