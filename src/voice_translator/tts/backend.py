"""TtsBackend 抽象基底。

役割: 翻訳済みテキストを音声(PCM)に合成する I/F。
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from voice_translator.common.types import BackendCapabilities
from voice_translator.common.utterance import Utterance


class TtsBackend(ABC):
    """音声合成バックエンドの抽象基底。

    実装は Windows SAPI(pyttsx3) 等(MVPは SAPI)。
    """

    @abstractmethod
    def synthesize(self, utterance: Utterance) -> Utterance:
        """utterance.tgt_text を音声に合成し、tts_pcm を埋めて返す。

        - 同じ Utterance を mutate して返す。
        - tts_pcm のサンプリングレート/形式はエンジン依存。
          出力デバイス側でリサンプル/変換する想定。
        - tgt_text が空ならそのまま返す。
        """

    def capabilities(self) -> BackendCapabilities:
        """対応言語/声質等のメタ情報。"""
        return BackendCapabilities()
