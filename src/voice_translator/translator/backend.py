"""TranslatorBackend 抽象基底。

役割: src_text を tgt_lang のテキストに翻訳する I/F。

R-2 でプリミティブ I/F に変更: Utterance 依存をやめ、(src_text, src_lang, tgt_lang)
を受けて翻訳テキスト str を返す。
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from voice_translator.common.backend_base import BackendBase
from voice_translator.common.messages import PayloadKind
from voice_translator.common.types import BackendCapabilities, LayerKind


class TranslatorBackend(BackendBase, ABC):
    """翻訳バックエンドの抽象基底。

    実装は NLLB-200 等(MVPは NLLB-200 distilled 600M)。
    `BackendBase` から状態管理/購読/エラー履歴の機能を継承する。
    """

    @abstractmethod
    def translate(self, src_text: str, src_lang: str, tgt_lang: str) -> str:
        """`src_text` を `tgt_lang` に翻訳した文字列を返す。

        - src_text: 入力テキスト。空/空白のみなら空文字を返す(passthrough)。
        - src_lang: 入力言語(ISO 639-1)。auto/不明は実装側で英語等にフォールバック。
        - tgt_lang: 翻訳先言語(ISO 639-1)。
        - 戻り値: 翻訳テキスト(strip 済み)。
        """

    @classmethod
    @abstractmethod
    def supported_target_languages(cls) -> list[str]:
        """対応する出力言語(ISO 639-1)の名目リスト。

        - クラスメソッド: UI が backend 名から問い合わせる時点で backend を
          ロード済みとは限らない。設定ダイアログを開いただけで重い import を
          引きずらないため、未ロード状態でも答えられる必要がある
        - `"auto"` は含めない(出力言語に「自動」は意味を持たない)
        """

    @classmethod
    def supported_source_languages(cls) -> list[str]:
        """対応する入力言語(ISO 639-1)の名目リスト。

        default 実装は出力言語と同じ(対称な backend が多い前提)。非対称な backend
        (例: 英→他言語のみの片方向モデル)はオーバーライドする。
        """
        return cls.supported_target_languages()

    # ---- パイプライン編成への申告(複合 backend はオーバーライド) ----
    @classmethod
    def covers_roles(cls) -> tuple[LayerKind, ...]:
        """この backend が担うロール(パイプライン順で連続していること)。"""
        return (LayerKind.TRANSLATOR,)

    @classmethod
    def consumes_payload(cls) -> PayloadKind:
        """入力の payload 形式。"""
        return PayloadKind.TRANSCRIBED

    @classmethod
    def produces_payload(cls) -> PayloadKind:
        """出力の payload 形式。"""
        return PayloadKind.TRANSLATED

    def capabilities(self) -> BackendCapabilities:
        """対応言語ペア等のメタ情報。"""
        return BackendCapabilities()
