"""AsrBackend 抽象基底。

役割: 発話単位の音声(PCM)を入力言語のテキストに書き起こす I/F。
出力言語の指定は行わない(=翻訳しない。それは Translator の責務)。

R-2 でプリミティブ I/F に変更: Utterance 依存をやめ、(pcm, hint) を受けて
(text, lang) を返す。横断メタ情報は UtteranceLedger 側で管理する。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from voice_translator.common.backend_base import BackendBase
from voice_translator.common.types import BackendCapabilities


class AsrBackend(BackendBase, ABC):
    """書き起こしバックエンドの抽象基底。

    実装は faster-whisper 等(MVPは faster-whisper)。
    `BackendBase` から状態管理/購読/エラー履歴の機能を継承する。
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

    @classmethod
    @abstractmethod
    def supported_input_languages(cls) -> list[str]:
        """対応する入力言語(ISO 639-1)の名目リスト。

        - `"auto"` は含めない(自動検出可否は `supports_auto_detect` で別途宣言)
        - クラスメソッドにする理由: UI が backend 名から問い合わせる時点で
          backend をロード済みとは限らない。設定ダイアログを開いただけで
          load を走らせないために、未ロード状態でも答えられる必要がある
        - 「モデル DL 状況で対応言語が変わる」型の backend は本 I/F では表現しない
          (本アプリでは対象外)
        """

    @classmethod
    def supports_auto_detect(cls) -> bool:
        """言語自動検出に対応するか(= UI で `"auto"` を選ばせてよいか)。

        既定 False。自動検出を持つ backend(Whisper 系等)は True を返すこと。
        """
        return False

    def capabilities(self) -> BackendCapabilities:
        """対応言語等のメタ情報。"""
        return BackendCapabilities()
