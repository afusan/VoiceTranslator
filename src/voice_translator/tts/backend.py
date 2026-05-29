"""TtsBackend 抽象基底。

役割: 翻訳済みテキストを音声(PCM)に合成する I/F。

R-2 でプリミティブ I/F に変更: Utterance 依存をやめ、(text, lang) を受けて
(pcm, samplerate) を返す。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from voice_translator.common.backend_base import BackendBase
from voice_translator.common.types import BackendCapabilities


class TtsBackend(BackendBase, ABC):
    """音声合成バックエンドの抽象基底。

    実装は Windows SAPI(pyttsx3) 等(MVPは SAPI)。
    `BackendBase` から状態管理/購読/エラー履歴の機能を継承する。
    """

    @abstractmethod
    def synthesize(self, text: str, tgt_lang: str) -> tuple[Any, int]:
        """`text` を音声に合成し、(pcm, samplerate) を返す。

        - text: 翻訳済みテキスト。空なら SkipError。
        - tgt_lang: 声選択のヒント(ISO 639-1)。
        - 戻り値:
            - pcm: np.ndarray(1次元 mono もしくは (N, ch))、dtype=float32 推奨。
            - samplerate: PCM のサンプルレート(Hz)。Output 側で参照する。
        """

    def capabilities(self) -> BackendCapabilities:
        """対応言語/声質等のメタ情報。"""
        return BackendCapabilities()
