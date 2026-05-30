"""BackendRegistry: レイヤ別バックエンドの登録・列挙・生成を司る。

役割: 各レイヤ(capture/vad/asr/translator/tts/output)に対して
複数バックエンド実装を「名前」で登録しておき、設定や GUI から
名前で取り出してインスタンス化する。Phase 2 以降の差し替え基盤になる。

Phase D で `capabilities` ヒントを optional 登録できるよう拡張。GUI が「未ロードでも
☁ クラウドか / 認証情報が要るか」を判定するために使う(R-3 / R2-1 の状態管理とは別軸)。
"""

from __future__ import annotations

from typing import Any, Callable

from .types import BackendCapabilities, LayerKind

# レイヤ + 名前 -> ファクトリ関数(引数なしでインスタンスを返す)
BackendFactory = Callable[[], Any]


class BackendRegistry:
    """レイヤ別にバックエンドのファクトリを保持するレジストリ。

    役割: アプリ起動時に各実装を登録 → 設定/GUI から名前で取り出す。
    インスタンス生成はファクトリ経由(都度新規 or シングルトンは実装側で選択)。

    Phase D 追加: `capabilities` ヒントを登録時に渡せるようにした。
    指定されていれば backend を生成せずに `get_capability_hint(layer, name)` で参照できる。
    """

    def __init__(self) -> None:
        self._factories: dict[LayerKind, dict[str, BackendFactory]] = {
            layer: {} for layer in LayerKind
        }
        self._capabilities: dict[LayerKind, dict[str, BackendCapabilities]] = {
            layer: {} for layer in LayerKind
        }
        # Phase E-2: 認証フローで backend クラス自体の classmethod を呼ぶために
        # クラス参照を持つ。factory はインスタンス生成、backend_cls は credential_spec
        # / verify_credentials の呼び出しに使う。
        self._classes: dict[LayerKind, dict[str, type]] = {
            layer: {} for layer in LayerKind
        }

    def register(
        self,
        layer: LayerKind,
        name: str,
        factory: BackendFactory,
        *,
        backend_cls: type | None = None,
        capabilities: BackendCapabilities | None = None,
    ) -> None:
        """指定レイヤに名前付きでバックエンドのファクトリを登録する。

        同名の登録は上書き(プラグイン的に差し替え可能)。
        `capabilities` が指定されていれば `get_capability_hint` で参照できる。
        `backend_cls` が指定されていれば `get_backend_class` で参照でき、
        `credential_spec` / `verify_credentials` 等の classmethod を呼べる(Phase E-2)。
        """
        self._factories[layer][name] = factory
        if capabilities is not None:
            self._capabilities[layer][name] = capabilities
        if backend_cls is not None:
            self._classes[layer][name] = backend_cls

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

    def get_capability_hint(
        self, layer: LayerKind, name: str
    ) -> BackendCapabilities | None:
        """登録時に渡された capability ヒントを返す(Phase D)。

        backend を生成せずに「クラウドか / 認証情報が要るか」を判定する用。
        ヒント未登録なら None(=情報不明)。
        """
        return self._capabilities.get(layer, {}).get(name)

    def get_backend_class(self, layer: LayerKind, name: str) -> type | None:
        """登録時に渡された backend クラス参照を返す(Phase E-2)。

        `credential_spec()` / `verify_credentials()` などの classmethod を直接呼ぶ用。
        未登録なら None。
        """
        return self._classes.get(layer, {}).get(name)
