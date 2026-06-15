"""i18n(文言カタログ + tr())の単体テストとキー健全性検査。

検査内容:
- tr() / current_locale() の挙動。
- AST 解析で src 全体の tr("...") を抽出し、ja 辞書と突合する:
  欠落キー / 死にキー / 動的キー(リテラルでない第一引数 = 規約違反)。
- **モジュールトップレベルでの tr() 呼び出し禁止**(言語切替に追従させるため定数に焼かない)。
- **gui/logic 配下に CJK 直書き文字列が残っていないこと**(置換漏れ検出。許可リストは内部 sentinel のみ)。
- 各 tr("key", ...) の kwargs が当該テンプレートの placeholder を満たすこと(引数不足の静的検出)。
"""

from __future__ import annotations

import ast
import re
import string
from functools import lru_cache
from pathlib import Path

import pytest

import voice_translator
from voice_translator.gui.i18n import _CATALOGS, _DEFAULT_LOCALE, all_keys, current_locale, tr

_SRC_ROOT = Path(voice_translator.__file__).resolve().parent
_I18N_FILE = _SRC_ROOT / "gui" / "i18n.py"
_LOGIC_DIR = _SRC_ROOT / "gui" / "logic"

# CJK(ひらがな/カタカナ/漢字/半角カナ)を含むか判定。
_CJK_RE = re.compile(r"[぀-ヿ㐀-䶿一-鿿ｦ-ﾟ]")

# gui/logic 配下で許容する「表示文言ではない CJK 文字列リテラル」(内部 sentinel)。
# capture_internal_to_display が config 由来の未登録表現を判定するための内部値。
_LOGIC_CJK_ALLOWLIST = {"(未登録)"}


# ============================================================
# tr() / current_locale() の挙動
# ============================================================
def test_current_locale_is_ja() -> None:
    assert current_locale() == "ja"


def test_tr_returns_registered_message() -> None:
    assert tr("ready.toggle.start") == "▶ 開始"


def test_tr_formats_kwargs() -> None:
    assert tr("accel.gpu", devices="cuda") == "演算: GPU (cuda)"


def test_tr_unknown_key_raises() -> None:
    with pytest.raises(KeyError):
        tr("no.such.key")


def test_tr_missing_kwarg_raises() -> None:
    with pytest.raises(KeyError):
        tr("accel.gpu")  # {devices} 未指定


def test_no_duplicate_keys_in_catalog() -> None:
    # dict リテラルは重複キーを黙って後勝ちにするため、ソースを AST で読み直して検査する。
    tree = ast.parse(_I18N_FILE.read_text(encoding="utf-8"))
    seen: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Dict):
            for k in node.keys:
                if isinstance(k, ast.Constant) and isinstance(k.value, str):
                    seen.append(k.value)
    assert len(seen) == len(set(seen)), "i18n カタログにキー重複がある"


# ============================================================
# AST 走査(tr 呼び出しの収集)
# ============================================================
class _TrCallVisitor(ast.NodeVisitor):
    """tr() 呼び出しを (key, kwarg 名, lineno, トップレベルか) で収集する。"""

    def __init__(self) -> None:
        self.func_depth = 0
        self.calls: list[tuple[str | None, list[str], int, bool]] = []

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self.func_depth += 1
        self.generic_visit(node)
        self.func_depth -= 1

    visit_AsyncFunctionDef = visit_FunctionDef  # type: ignore[assignment]

    def visit_Call(self, node: ast.Call) -> None:
        func = node.func
        name = (
            func.id if isinstance(func, ast.Name)
            else func.attr if isinstance(func, ast.Attribute)
            else None
        )
        if name == "tr":
            key = None
            if node.args and isinstance(node.args[0], ast.Constant) and isinstance(
                node.args[0].value, str
            ):
                key = node.args[0].value
            kwargs = [kw.arg for kw in node.keywords if kw.arg is not None]
            self.calls.append((key, kwargs, node.lineno, self.func_depth == 0))
        self.generic_visit(node)


@lru_cache(maxsize=1)
def _all_tr_calls() -> tuple[tuple[str, str | None, list[str], int, bool], ...]:
    """src 配下の全 tr() 呼び出し: (相対パス, key, kwargs, lineno, トップレベルか)。"""
    out: list[tuple[str, str | None, list[str], int, bool]] = []
    for path in _SRC_ROOT.rglob("*.py"):
        visitor = _TrCallVisitor()
        visitor.visit(ast.parse(path.read_text(encoding="utf-8"), filename=str(path)))
        rel = str(path.relative_to(_SRC_ROOT))
        for key, kwargs, lineno, toplevel in visitor.calls:
            out.append((rel, key, tuple(kwargs), lineno, toplevel))  # type: ignore[arg-type]
    return tuple(out)


def _placeholders(template: str) -> set[str]:
    return {f for _, f, _, _ in string.Formatter().parse(template) if f}


# ============================================================
# キー健全性検査
# ============================================================
def test_no_dynamic_keys() -> None:
    bad = [f"{p}:{ln}" for p, key, _, ln, _ in _all_tr_calls() if key is None]
    assert not bad, f"動的 tr() キーは禁止(リテラルで渡す): {bad}"


def test_no_missing_keys() -> None:
    used = {key for _, key, _, _, _ in _all_tr_calls() if key}
    missing = used - set(all_keys())
    assert not missing, f"辞書に無いキー: {sorted(missing)}"


def test_no_dead_keys() -> None:
    used = {key for _, key, _, _, _ in _all_tr_calls() if key}
    dead = set(all_keys()) - used
    assert not dead, f"未使用の死にキー: {sorted(dead)}"


def test_no_toplevel_tr_calls() -> None:
    # 言語切替に追従させるため、tr() は表示する瞬間(関数内)で呼ぶ。モジュール
    # トップレベル(代入右辺・クラス body 含む)での評価は値を焼き付けるため禁止。
    bad = [f"{p}:{ln}" for p, _, _, ln, toplevel in _all_tr_calls() if toplevel]
    assert not bad, f"トップレベルでの tr() 評価は禁止(関数内で呼ぶ): {bad}"


def test_tr_kwargs_cover_placeholders() -> None:
    # 各 tr("key", ...) の kwargs が、テンプレートが要求する placeholder を満たすこと
    # (引数不足を実行前に検出する)。
    catalog = _CATALOGS[_DEFAULT_LOCALE]
    bad: list[str] = []
    for path, key, kwargs, lineno, _ in _all_tr_calls():
        if not key or key not in catalog:
            continue
        missing = _placeholders(catalog[key]) - set(kwargs)
        if missing:
            bad.append(f"{path}:{lineno} key={key} 不足引数={sorted(missing)}")
    assert not bad, f"tr() 呼び出しのテンプレ引数不足: {bad}"


def test_no_cjk_literals_in_logic() -> None:
    # gui/logic 配下に表示文言の直書き(CJK 文字列リテラル)が残っていないこと。
    # docstring / 式文の文字列は除外。内部 sentinel は許可リストで除外。
    bad: list[str] = []
    for path in _LOGIC_DIR.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        # docstring / 式文としての文字列(コメント代わり)の id を集めて除外する。
        skip_ids: set[int] = set()
        for node in ast.walk(tree):
            if isinstance(
                node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
            ):
                body = getattr(node, "body", [])
                if body and isinstance(body[0], ast.Expr) and isinstance(
                    body[0].value, ast.Constant
                ):
                    skip_ids.add(id(body[0].value))
            if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant):
                skip_ids.add(id(node.value))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and id(node) not in skip_ids
                and node.value not in _LOGIC_CJK_ALLOWLIST
                and _CJK_RE.search(node.value)
            ):
                bad.append(f"{path.name}:{node.lineno} {node.value!r}")
    assert not bad, f"gui/logic に未 tr() の CJK 直書きが残存: {bad}"
