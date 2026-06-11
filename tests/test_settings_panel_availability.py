"""SettingsPanel の「未導入 backend をプルダウンに列挙しない」配線テスト(shim 方式)。

固定する契約:
- 候補は `BackendCatalog.is_backend_available` で導入済みに絞る
  (「未導入のものを選んで Not Downloaded になる」混乱を候補の時点で防ぐ)
- 判定失敗・全滅時は無濾過に縮退(誤判定で隠すより、選んでロード失敗 +
  エラー案内に倒す方が安全)
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from voice_translator.common.types import LayerKind


def _bind(shim, *method_names: str):
    from voice_translator.gui.settings_panel import SettingsPanel

    for name in method_names:
        setattr(shim, name, getattr(SettingsPanel, name).__get__(shim))


@pytest.fixture()
def stub_panel():
    from voice_translator.gui.settings_panel import SettingsPanel

    shim = MagicMock(spec=SettingsPanel)
    _bind(shim, "_available_backend_names")
    shim._controller = MagicMock(name="controller")
    shim._controller.list_backends.return_value = ["local_a", "cloud_b", "local_c"]
    return shim


class TestAvailableBackendNames:
    def test_filters_unavailable_backends(self, stub_panel) -> None:
        stub_panel._controller.catalog.is_backend_available.side_effect = (
            lambda layer, name: name != "cloud_b"
        )

        names = stub_panel._available_backend_names(LayerKind.ASR)

        assert names == ["local_a", "local_c"]

    def test_all_available_keeps_order(self, stub_panel) -> None:
        stub_panel._controller.catalog.is_backend_available.return_value = True

        names = stub_panel._available_backend_names(LayerKind.ASR)

        assert names == ["local_a", "cloud_b", "local_c"]

    def test_catalog_failure_degrades_to_unfiltered(self, stub_panel) -> None:
        """判定不能時は無濾過(隠すより選んでロード失敗に倒す)。"""
        stub_panel._controller.catalog.is_backend_available.side_effect = (
            RuntimeError("boom")
        )

        names = stub_panel._available_backend_names(LayerKind.ASR)

        assert names == ["local_a", "cloud_b", "local_c"]

    def test_all_filtered_degrades_to_unfiltered(self, stub_panel) -> None:
        """全滅時も無濾過(空のプルダウンを出さない)。"""
        stub_panel._controller.catalog.is_backend_available.return_value = False

        names = stub_panel._available_backend_names(LayerKind.ASR)

        assert names == ["local_a", "cloud_b", "local_c"]

    def test_empty_registry_returns_empty(self, stub_panel) -> None:
        """未登録レイヤは空のまま(呼び出し側の「(未登録)」fallback に委譲)。"""
        stub_panel._controller.list_backends.return_value = []

        assert stub_panel._available_backend_names(LayerKind.ASR) == []
