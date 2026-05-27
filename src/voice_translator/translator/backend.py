"""TranslatorBackend 抽象基底。

役割: src_text を tgt_lang のテキストに翻訳する I/F。

R-2 でプリミティブ I/F に変更: Utterance 依存をやめ、(src_text, src_lang, tgt_lang)
を受けて翻訳テキスト str を返す。
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from voice_translator.common.types import BackendCapabilities


class TranslatorBackend(ABC):
    """翻訳バックエンドの抽象基底。

    実装は NLLB-200 等(MVPは NLLB-200 distilled 600M)。
    """

    @abstractmethod
    def translate(self, src_text: str, src_lang: str, tgt_lang: str) -> str:
        """`src_text` を `tgt_lang` に翻訳した文字列を返す。

        - src_text: 入力テキスト。空/空白のみなら空文字を返す(passthrough)。
        - src_lang: 入力言語(ISO 639-1)。auto/不明は実装側で英語等にフォールバック。
        - tgt_lang: 翻訳先言語(ISO 639-1)。
        - 戻り値: 翻訳テキスト(strip 済み)。
        """

    def capabilities(self) -> BackendCapabilities:
        """対応言語ペア等のメタ情報。"""
        return BackendCapabilities()
