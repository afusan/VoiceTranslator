"""購読リーク防止の機械化検査(第2次レビュー 提案1 / 4a-1)。

`self._subscriptions = [...]` を持つ widget は、破棄時に解除しないと死んだ listener が
残留し emit のたびに破棄済み widget を叩く。言語切替で Panel が再構築される本ブランチでは
これがリーク + ログ汚染になる。よって「`_subscriptions` を代入するクラスは `destroy` を
override し、その中で `unsubscribe` を呼ぶ」を AST で機械的に要求する。
"""

from __future__ import annotations

import ast
from pathlib import Path

import voice_translator

_GUI_DIR = Path(voice_translator.__file__).resolve().parent / "gui"


def _assigns_self_subscriptions(class_node: ast.ClassDef) -> bool:
    for node in ast.walk(class_node):
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if (
                    isinstance(t, ast.Attribute)
                    and t.attr == "_subscriptions"
                    and isinstance(t.value, ast.Name)
                    and t.value.id == "self"
                ):
                    return True
    return False


def _destroy_calls_unsubscribe(class_node: ast.ClassDef) -> bool:
    for item in class_node.body:
        if isinstance(item, ast.FunctionDef) and item.name == "destroy":
            for node in ast.walk(item):
                if (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "unsubscribe"
                ):
                    return True
    return False


def test_subscription_widgets_unsubscribe_on_destroy() -> None:
    bad: list[str] = []
    for path in _GUI_DIR.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            if _assigns_self_subscriptions(node) and not _destroy_calls_unsubscribe(node):
                bad.append(f"{path.name}:{node.name}")
    assert not bad, (
        "_subscriptions を持つが destroy() で unsubscribe しないクラス"
        f"(破棄時に購読解除が必要): {bad}"
    )
