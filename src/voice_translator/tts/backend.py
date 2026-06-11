"""TtsBackend 抽象基底。

役割: 翻訳済みテキストを音声(PCM)に合成する I/F。

R-2 でプリミティブ I/F に変更: Utterance 依存をやめ、(text, lang) を受けて
(pcm, samplerate) を返す。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from voice_translator.common.backend_base import BackendBase
from voice_translator.common.messages import PayloadKind
from voice_translator.common.types import BackendCapabilities, LayerKind


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

    @classmethod
    @abstractmethod
    def supported_output_languages(cls) -> list[str]:
        """対応する読み上げ言語(ISO 639-1)の名目リスト。

        - クラスメソッド: UI が backend 名から問い合わせる時点で backend を
          ロード済みとは限らない。設定ダイアログを開いただけで重い import を
          引きずらないため、未ロード状態でも答えられる必要がある
        - `"auto"` は含めない(読み上げ言語に「自動」は意味を持たない)
        - 空リストを返した場合 UI は「未知 = 警告しない」として扱う
          (OS 依存などで動的に変わるケースで「分からない」を表明する選択肢)
        """

    # ---- パイプライン編成への申告(複合 backend はオーバーライド) ----
    @classmethod
    def covers_roles(cls) -> tuple[LayerKind, ...]:
        """この backend が担うロール(パイプライン順で連続していること)。"""
        return (LayerKind.TTS,)

    @classmethod
    def consumes_payload(cls) -> PayloadKind:
        """入力の payload 形式。"""
        return PayloadKind.TRANSLATED

    @classmethod
    def produces_payload(cls) -> PayloadKind:
        """出力の payload 形式。"""
        return PayloadKind.SYNTHESIZED

    def capabilities(self) -> BackendCapabilities:
        """対応言語/声質等のメタ情報。"""
        return BackendCapabilities()
