"""AsrBackend 抽象基底。

役割: 発話単位の音声(PCM)を入力言語のテキストに書き起こす I/F。
出力言語の指定は行わない(=翻訳しない。それは Translator の責務)。

R-2 でプリミティブ I/F に変更: Utterance 依存をやめ、(pcm, hint) を受けて
(text, lang) を返す。横断メタ情報は UtteranceLedger 側で管理する。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from voice_translator.common.types import BackendCapabilities


class AsrBackend(ABC):
    """書き起こしバックエンドの抽象基底。

    実装は faster-whisper 等(MVPは faster-whisper)。
    """

    @abstractmethod
    def transcribe(self, pcm: Any, src_lang_hint: str = "auto") -> tuple[str, str]:
        """`pcm` を書き起こし、(text, lang) を返す。

        - pcm: 16kHz/mono/float32 の `np.ndarray[(n,)]` を想定。空入力は SkipError。
        - src_lang_hint: "auto"/""/None なら自動検出。それ以外は ISO 639-1。
        - 戻り値:
            - text: 認識テキスト(strip 済み)。空の場合は空文字。
            - lang: 検出/指定された言語(ISO 639-1)。
        """

    def capabilities(self) -> BackendCapabilities:
        """対応言語等のメタ情報。"""
        return BackendCapabilities()
