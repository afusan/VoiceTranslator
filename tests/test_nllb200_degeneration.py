"""NLLB-200 翻訳の degenerate output (退化出力) 再現/防止テスト。

translations.jsonl L184 で観測された「同じ語句を延々と繰り返す」現象を
**実モデル**(NLLB-200 distilled 600M)で再現し、修正後にゼロになることを確認する。

実モデルを使うため遅い(モデル初期化 ~30s + 翻訳 ~2-3s × 10回)。`slow` マーカー付き。
通常は `pytest -m "not slow"` で除外し、明示的に `pytest -m slow tests/test_nllb200_degeneration.py`
で起動する。
"""

from __future__ import annotations

import pytest


# ============================================================
# L184 で観測された "崩壊する" 入力テキスト(translations.jsonl から抜粋)。
# - 920文字程度の英文
# - "internet"/"online"/"restrictions"/"businesses" が密集して繰り返される
# - greedy decoding が局所最適に落ちる典型例
# ============================================================
L184_SRC_TEXT = (
    "Well, a number of people have contacted BBC Persian to say their home internet is "
    "connected, but their mobile internet is still not working. People in Tehran welcome "
    "the lifting of the restrictions, which lasted for almost three months. We caught up "
    "with computer science students, Rustin and Panter to see what they thought. This is "
    "100 percent a positive thing. The online market is thirsty to go back to its previous "
    "state, but a social prosecution that keeps happening significantly harms the online "
    "businesses. The businesses highly depend on the internet and every time these "
    "restrictions make life more difficult for them. I'm very happy that the internet is "
    "going to be restored because businesses can get back to normal. I had an online shop "
    "for a while and sold products. Definitely it will benefit us. But the only problem "
    "is the censorship. If they come up with a good solution and correct solution to "
    "this, many problems would be solved."
)


# ============================================================
# 退化判定ロジック
# ============================================================
def max_ngram_count(text: str, n: int = 8) -> tuple[str, int]:
    """text 中で最も多く出現する n-gram と、その出現回数を返す。

    degenerate output 検出のヒューリスティック。
    正常な翻訳では同じ 8-gram が複数回繰り返されることはほぼ無い(1〜2回)。
    退化していると同じ 8-gram が 10 回以上繰り返される。
    """
    if len(text) < n:
        return "", 0
    counts: dict[str, int] = {}
    for i in range(len(text) - n + 1):
        ng = text[i : i + n]
        counts[ng] = counts.get(ng, 0) + 1
    if not counts:
        return "", 0
    return max(counts.items(), key=lambda kv: kv[1])


def is_degenerate(text: str, *, n: int = 8, threshold: int = 5) -> bool:
    """同じ n-gram が threshold 回以上現れたら退化とみなす(既定: 8-gram が 5 回以上)。"""
    _, count = max_ngram_count(text, n=n)
    return count >= threshold


# ============================================================
class TestNgramHelpers:
    """退化判定ヘルパの単体テスト(モデル不要)。"""

    def test_no_repetition_returns_one(self) -> None:
        _, count = max_ngram_count("これは普通の文章である。何度も読みたい。", n=8)
        assert count == 1

    def test_repeated_ngram_counted(self) -> None:
        # "妨げている" が 5 回繰り返される文字列を作る
        text = "妨げている。" * 5
        ng, count = max_ngram_count(text, n=4)
        assert count >= 5
        assert "妨げて" in ng

    def test_is_degenerate_on_repeat(self) -> None:
        text = "ウェブのインターネットは,インターネットの普及を 妨げている. " * 20
        assert is_degenerate(text) is True

    def test_is_not_degenerate_on_normal_text(self) -> None:
        text = (
            "インターネットが部分的に回復した後 再びオンラインに復帰したことで "
            "イラン人が表した喜びに"
        )
        assert is_degenerate(text) is False


# ============================================================
@pytest.mark.slow
class TestL184DegenerationRepro:
    """実モデルを使って L184 入力の退化を観測する。

    修正前は 10/10 (greedy decoding は決定論的なので毎回同じ崩壊) を想定。
    修正後 (num_beams=4 + no_repeat_ngram_size=3) では 0/10 を期待。
    """

    def test_l184_input_does_not_degenerate(self) -> None:
        """同じ入力を 10 回翻訳し、すべて退化しないことを assert する。

        テスト失敗時は退化件数とサンプル出力をログに残す。
        """
        from voice_translator.translator.nllb200_backend import (
            Nllb200TranslatorBackend,
        )

        backend = Nllb200TranslatorBackend()

        results: list[dict] = []
        for i in range(10):
            tgt = backend.translate(L184_SRC_TEXT, "en", "ja")
            ng, count = max_ngram_count(tgt, n=8)
            results.append(
                {
                    "i": i,
                    "len": len(tgt),
                    "max_8gram_count": count,
                    "max_8gram": ng,
                    "degenerate": count >= 5,
                    "head": tgt[:80],
                    "tail": tgt[-80:],
                }
            )

        bad = sum(1 for r in results if r["degenerate"])

        # 詳細をログに残す(成功時も失敗時もレポート用に出力)
        print(f"\n=== L184 degeneration report: {bad}/10 退化 ===")
        for r in results:
            mark = "DEGEN" if r["degenerate"] else "ok   "
            print(
                f"  attempt {r['i']}: {mark} len={r['len']:4d}"
                f" max_8gram_count={r['max_8gram_count']:2d}"
                f" ng={r['max_8gram']!r}"
            )
            if r["degenerate"]:
                print(f"    head: {r['head']!r}")
                print(f"    tail: {r['tail']!r}")

        assert bad == 0, (
            f"{bad}/10 件で退化(同じ 8-gram が 5 回以上)が再現した。"
            f" 詳細は上記レポート参照。"
        )
