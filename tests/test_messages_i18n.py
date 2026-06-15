"""messages(i18n 土台)の単体テストとキー健全性検査。

- tr() / current_locale() の挙動。
- AST 解析で src 全体の tr("...") リテラルキーを抽出し、ja 辞書と突合する:
  欠落キー(コードで使うが辞書に無い)/ 死にキー(辞書にあるが未使用)/
  動的キー(リテラルでない第一引数 = 規約違反)を検出する。
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

import voice_translator
from voice_translator.gui.logic import messages
from voice_translator.gui.logic.messages import all_keys, current_locale, tr

_SRC_ROOT = Path(voice_translator.__file__).resolve().parent


# ============================================================
# tr() / current_locale() の挙動
# ============================================================
def test_current_locale_is_ja() -> None:
    assert current_locale() == "ja"


def test_tr_returns_registered_message() -> None:
    assert tr("ready.toggle.start") == "▶ 開始"


def test_tr_formats_kwargs() -> None:
    msg = tr("accel.gpu", devices="cuda")
    assert msg == "演算: GPU (cuda)"


def test_tr_unknown_key_raises() -> None:
    # 黙って空文字を返さず、未登録キーは例外で気づける。
    with pytest.raises(KeyError):
        tr("no.such.key")


def test_tr_missing_kwarg_raises() -> None:
    # テンプレートが要求する引数を渡さないと例外。
    with pytest.raises(KeyError):
        tr("accel.gpu")  # {devices} 未指定


def test_no_duplicate_keys_in_catalog() -> None:
    # dict リテラルは重複キーを黙って後勝ちにするため、ソースを AST で読み直して検査する。
    src = (_SRC_ROOT / "gui" / "logic" / "messages.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    seen: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Dict):
            for k in node.keys:
                if isinstance(k, ast.Constant) and isinstance(k.value, str):
                    seen.append(k.value)
    assert len(seen) == len(set(seen)), "messages カタログにキー重複がある"


# ============================================================
# AST ベースのキー健全性検査
# ============================================================
def _iter_tr_calls() -> tuple[set[str], list[str]]:
    """src 配下の全 .py を走査し、(リテラルキー集合, 動的キー違反のリスト) を返す。"""
    used_keys: set[str] = set()
    dynamic_violations: list[str] = []
    for path in _SRC_ROOT.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = (
                func.id if isinstance(func, ast.Name)
                else func.attr if isinstance(func, ast.Attribute)
                else None
            )
            if name != "tr" or not node.args:
                continue
            first = node.args[0]
            if isinstance(first, ast.Constant) and isinstance(first.value, str):
                used_keys.add(first.value)
            else:
                rel = path.relative_to(_SRC_ROOT)
                dynamic_violations.append(f"{rel}:{node.lineno}")
    return used_keys, dynamic_violations


def test_no_dynamic_keys() -> None:
    # キーはリテラルで渡す規約。f-string や変数キーは静的検査を壊すので禁止。
    _, dynamic = _iter_tr_calls()
    assert not dynamic, f"動的 tr() キーは禁止: {dynamic}"


def test_no_missing_keys() -> None:
    # コードで使う全キーが辞書に存在する(ランタイム KeyError の予防)。
    used, _ = _iter_tr_calls()
    missing = used - set(all_keys())
    assert not missing, f"辞書に無いキー: {sorted(missing)}"


def test_no_dead_keys() -> None:
    # 辞書にあるが src のどこでも使われていないキー(死にキー)が無い。
    used, _ = _iter_tr_calls()
    dead = set(all_keys()) - used
    assert not dead, f"未使用の死にキー: {sorted(dead)}"
