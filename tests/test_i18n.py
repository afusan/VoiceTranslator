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
# CJK 残存検査の対象(キー化済みモジュール)。Phase 3 で widget を変換するたびに追加し、
# 完了時に gui/ 配下すべて(カタログ本体 i18n.py を除く)を覆う。
_GUI_DIR = _SRC_ROOT / "gui"
_KEYED_FILES: tuple[Path, ...] = tuple(_LOGIC_DIR.glob("*.py")) + (
    _GUI_DIR / "layer_settings_schema.py",
    _GUI_DIR / "language_select_dialog.py",
    _GUI_DIR / "process_select_dialog.py",
    _GUI_DIR / "consent_dialog.py",
    _GUI_DIR / "credential_dialog.py",
    _GUI_DIR / "layer_settings_dialog.py",
    _GUI_DIR / "control_panel.py",
)
# schema が SettingField に持たせる i18n キー登録源(`tr()` ではなくこの keyword で登録)。
_KEY_REGISTERING_KWARGS = {"label_key", "help_key"}

# CJK(ひらがな/カタカナ/漢字/半角カナ)を含むか判定。
_CJK_RE = re.compile(r"[぀-ヿ㐀-䶿一-鿿ｦ-ﾟ]")

# 検査対象モジュールで許容する「表示文言ではない CJK 文字列リテラル」。
# - "(未登録)": capture_internal_to_display が config 由来の未登録表現を判定する内部 sentinel。
# - "未対応の field_type: ": parse_field_value の programmer 向け例外メッセージ(UI 表示ではない)。
_CJK_ALLOWLIST = {"(未登録)", "未対応の field_type: "}


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
            first = node.args[0] if node.args else None
            if isinstance(first, ast.Constant) and isinstance(first.value, str):
                key = first.value
            # schema 解決呼び出し `tr(field.label_key)` / `tr(field.help_key)` は
            # キーが label_key= 側のリテラルで登録されるため、動的でも許可する。
            allowed_dynamic = (
                isinstance(first, ast.Attribute)
                and first.attr in _KEY_REGISTERING_KWARGS
            )
            kwargs = [kw.arg for kw in node.keywords if kw.arg is not None]
            self.calls.append(
                (key, kwargs, node.lineno, self.func_depth == 0, allowed_dynamic)
            )
        self.generic_visit(node)


@lru_cache(maxsize=1)
def _all_tr_calls() -> tuple[tuple[str, str | None, tuple[str, ...], int, bool, bool], ...]:
    """src 配下の全 tr() 呼び出し: (相対パス, key, kwargs, lineno, トップレベルか, 許可動的か)。"""
    out: list[tuple[str, str | None, tuple[str, ...], int, bool, bool]] = []
    for path in _SRC_ROOT.rglob("*.py"):
        visitor = _TrCallVisitor()
        visitor.visit(ast.parse(path.read_text(encoding="utf-8"), filename=str(path)))
        rel = str(path.relative_to(_SRC_ROOT))
        for key, kwargs, lineno, toplevel, allowed in visitor.calls:
            out.append((rel, key, tuple(kwargs), lineno, toplevel, allowed))
    return tuple(out)


@lru_cache(maxsize=1)
def _registered_field_keys() -> frozenset[str]:
    """schema が `label_key=` / `help_key=` のリテラルで登録する i18n キー(空文字は除外)。"""
    keys: set[str] = set()
    for path in _SRC_ROOT.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            for kw in node.keywords:
                if (
                    kw.arg in _KEY_REGISTERING_KWARGS
                    and isinstance(kw.value, ast.Constant)
                    and isinstance(kw.value.value, str)
                    and kw.value.value
                ):
                    keys.add(kw.value.value)
    return frozenset(keys)


def _used_keys() -> set[str]:
    """コードが使う全キー(リテラル tr() + schema の label_key/help_key 登録)。"""
    literal = {key for _, key, _, _, _, _ in _all_tr_calls() if key}
    return literal | set(_registered_field_keys())


def _placeholders(template: str) -> set[str]:
    return {f for _, f, _, _ in string.Formatter().parse(template) if f}


# ============================================================
# キー健全性検査
# ============================================================
def test_no_dynamic_keys() -> None:
    # リテラルでない第一引数は禁止。ただし schema 解決 `tr(field.label_key)` は許可。
    bad = [
        f"{p}:{ln}"
        for p, key, _, ln, _, allowed in _all_tr_calls()
        if key is None and not allowed
    ]
    assert not bad, f"動的 tr() キーは禁止(リテラルで渡す): {bad}"


def test_no_missing_keys() -> None:
    missing = _used_keys() - set(all_keys())
    assert not missing, f"辞書に無いキー: {sorted(missing)}"


def test_no_dead_keys() -> None:
    dead = set(all_keys()) - _used_keys()
    assert not dead, f"未使用の死にキー: {sorted(dead)}"


def test_no_toplevel_tr_calls() -> None:
    # 言語切替に追従させるため、tr() は表示する瞬間(関数内)で呼ぶ。モジュール
    # トップレベル(代入右辺・クラス body 含む)での評価は値を焼き付けるため禁止。
    bad = [f"{p}:{ln}" for p, _, _, ln, toplevel, _ in _all_tr_calls() if toplevel]
    assert not bad, f"トップレベルでの tr() 評価は禁止(関数内で呼ぶ): {bad}"


def test_tr_kwargs_cover_placeholders() -> None:
    # 各 tr("key", ...) の kwargs が、テンプレートが要求する placeholder を満たすこと
    # (引数不足を実行前に検出する)。動的解決(key=None)は対象外。
    catalog = _CATALOGS[_DEFAULT_LOCALE]
    bad: list[str] = []
    for path, key, kwargs, lineno, _, _ in _all_tr_calls():
        if not key or key not in catalog:
            continue
        missing = _placeholders(catalog[key]) - set(kwargs)
        if missing:
            bad.append(f"{path}:{lineno} key={key} 不足引数={sorted(missing)}")
    assert not bad, f"tr() 呼び出しのテンプレ引数不足: {bad}"


# ログ/例外メッセージは UI 表示ではなく dev 向け(日本語のままでよい)。CJK 検査から除外する。
_LOG_METHODS = {
    "debug", "info", "warning", "warn", "error", "exception", "critical", "log",
}


def _cjk_skip_ids(tree: ast.AST) -> set[int]:
    """CJK 検査でスキップすべき文字列 Constant の id 集合。

    除外対象: docstring / 式文の文字列 / logging 呼び出しの引数 / raise の引数。
    """
    skip: set[int] = set()

    def _mark_subtree(node: ast.AST) -> None:
        for sub in ast.walk(node):
            if isinstance(sub, ast.Constant) and isinstance(sub.value, str):
                skip.add(id(sub))

    for node in ast.walk(tree):
        if isinstance(
            node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
        ):
            body = getattr(node, "body", [])
            if body and isinstance(body[0], ast.Expr) and isinstance(
                body[0].value, ast.Constant
            ):
                skip.add(id(body[0].value))
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant):
            skip.add(id(node.value))
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in _LOG_METHODS
        ):
            _mark_subtree(node)
        if isinstance(node, ast.Raise):
            _mark_subtree(node)
    return skip


def test_no_cjk_literals_in_keyed_modules() -> None:
    # キー化済みであるべきモジュール(gui/ 配下、カタログ本体除く)に UI 表示文言の
    # 直書き(CJK 文字列リテラル)が残っていないこと。docstring / 式文 / ログ・例外
    # メッセージは除外。内部 sentinel 等は許可リストで除外。
    bad: list[str] = []
    for path in _KEYED_FILES:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        skip_ids = _cjk_skip_ids(tree)
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and id(node) not in skip_ids
                and node.value not in _CJK_ALLOWLIST
                and _CJK_RE.search(node.value)
            ):
                bad.append(f"{path.name}:{node.lineno} {node.value!r}")
    assert not bad, f"gui に未 tr() の CJK 表示文言が残存: {bad}"
