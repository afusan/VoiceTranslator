"""TranslatorBackend 抽象基底。

役割: src_text を tgt_lang のテキストに翻訳する I/F。
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from voice_translator.common.types import BackendCapabilities
from voice_translator.common.utterance import Utterance


class TranslatorBackend(ABC):
    """翻訳バックエンドの抽象基底。

    実装は NLLB-200 等(MVPは NLLB-200 distilled 600M)。
    """

    @abstractmethod
    def translate(self, utterance: Utterance, tgt_lang: str) -> Utterance:
        """utterance.src_text を tgt_lang に翻訳し tgt_text/tgt_lang を埋めて返す。

        - 同じ Utterance を mutate して返す。
        - src_text が空ならそのまま返す(翻訳しない)。
        - tgt_lang はバックエンドが対応している言語コード。
        """

    def capabilities(self) -> BackendCapabilities:
        """対応言語ペア等のメタ情報。"""
        return BackendCapabilities()
