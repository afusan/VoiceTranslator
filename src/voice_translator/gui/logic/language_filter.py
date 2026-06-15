"""language_filter: 言語候補の絞り込み(検索)を行う純関数。

役割: 検索クエリ文字列と候補コード列から「クエリに一致する候補」を順序付きで返す。
出力言語が 100 超になり得るため(MMS-TTS ∩ NLLB)、`OptionMenu` の素のスクロールでは
選びにくい。検索付きダイアログ(`gui/language_select_dialog.py`)の判断部をここに集約し、
GUI 非依存に単体テストできるようにする(UI 規約: 判断は logic、widget は塗るだけ)。

一致判定はコード(`"swh"`)と英語表示名(`"Swahili"`)の双方を対象に、大文字小文字を
無視した部分一致で行う。並びは「コード前方一致 → 名前前方一致 → その他部分一致」の順に
寄せ、各群内は入力順を保つ(候補側で安定ソート済みである前提)。
"""

from __future__ import annotations

from typing import Sequence

from voice_translator.common.languages import language_name


def filter_languages(codes: Sequence[str], query: str) -> list[str]:
    """`query` に一致する言語コードを順序付きで返す。

    - query が空 / 空白のみ → 候補をそのまま返す(全件)。
    - コード or 英語名の部分一致(大文字小文字無視)。
    - 並び: コード前方一致 → 名前前方一致 → 残りの部分一致。群内は入力順維持。
    """
    q = (query or "").strip().lower()
    if not q:
        return list(codes)

    code_prefix: list[str] = []
    name_prefix: list[str] = []
    other: list[str] = []
    for c in codes:
        cl = c.lower()
        nl = language_name(c).lower()
        if cl.startswith(q):
            code_prefix.append(c)
        elif nl.startswith(q):
            name_prefix.append(c)
        elif q in cl or q in nl:
            other.append(c)
    return code_prefix + name_prefix + other
