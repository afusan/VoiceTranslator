"""BackendRegistry: レイヤ別バックエンドの登録・列挙・生成を司る。

役割: 各レイヤ(capture/vad/asr/translator/tts/output)に対して
複数バックエンド実装を「名前」で登録しておき、設定や GUI から
名前で取り出してインスタンス化する。Phase 2 以降の差し替え基盤になる。
"""

from __future__ import annotations

from typing import Any, Callable

from .types import LayerKind

# レイヤ + 名前 -> ファクトリ関数(引数なしでインスタンスを返す)
BackendFactory = Callable[[], Any]


class BackendRegistry:
    """レイヤ別にバックエンドのファクトリを保持するレジストリ。

    役割: アプリ起動時に各実装を登録 → 設定/GUI から名前で取り出す。
    インスタンス生成はファクトリ経由(都度新規 or シングルトンは実装側で選択)。
    """

    def __init__(self) -> None:
        self._factories: dict[LayerKind, dict[str, BackendFactory]] = {
            layer: {} for layer in LayerKind
        }

    def register(self, layer: LayerKind, name: str, factory: BackendFactory) -> None:
        """指定レイヤに名前付きでバックエンドのファクトリを登録する。

        同名の登録は上書き(プラグイン的に差し替え可能)。
        """
        self._factories[layer][name] = factory

    def is_registered(self, layer: LayerKind, name: str) -> bool:
        """指定レイヤ + 名前が登録済みか。"""
        return name in self._factories.get(layer, {})

    def list_names(self, layer: LayerKind) -> list[str]:
        """指定レイヤの登録済みバックエンド名(登録順)。"""
        return list(self._factories.get(layer, {}).keys())

    def create(self, layer: LayerKind, name: str) -> Any:
        """指定レイヤ + 名前のバックエンドインスタンスを生成する。

        未登録なら KeyError。
        """
        try:
            factory = self._factories[layer][name]
        except KeyError as e:
            raise KeyError(
                f"バックエンド未登録: layer={layer.value}, name={name}"
            ) from e
        return factory()
