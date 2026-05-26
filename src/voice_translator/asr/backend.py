"""AsrBackend 抽象基底。

役割: 発話単位の音声を入力言語のテキストに書き起こす I/F。
出力言語の指定は行わない(=翻訳しない。それは Translator の責務)。
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from voice_translator.common.types import BackendCapabilities
from voice_translator.common.utterance import Utterance


class AsrBackend(ABC):
    """書き起こしバックエンドの抽象基底。

    実装は faster-whisper 等(MVPは faster-whisper)。
    """

    @abstractmethod
    def transcribe(self, utterance: Utterance, src_lang: str = "auto") -> Utterance:
        """utterance.pcm を書き起こし、src_text を埋めて返す。

        - 同じ Utterance を mutate して返す(参照同一)。
        - src_lang は ISO 639-1(例: "en", "ja", "auto" は自動検出)。
        - 結果が空なら src_text="" のままで返す(SkipError は呼び出し側で判定)。
        """

    def capabilities(self) -> BackendCapabilities:
        """対応言語等のメタ情報。"""
        return BackendCapabilities()
