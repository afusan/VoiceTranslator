"""ステージ間で受け渡されるメッセージ型。

役割: 5スレッド版パイプラインで各ステージが**次段に必要なデータだけ**を渡すための
封筒(`PipelineMessage`)と、ステージごとの payload 型を定義する。
ステージ横断のメタ情報(timeline/言語履歴/テキスト履歴等)は payload には含めず、
`UtteranceLedger` 側で seq_id をキーに集約する。

詳細は docs/design/Class.md / Architecture.html を参照。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Union


@dataclass(frozen=True)
class RawPayload:
    """Input → ASR の受け渡しデータ。

    役割: VAD で確定した発話の生 PCM と、ASR への言語ヒントだけを運ぶ。
    """

    pcm: Any  # np.ndarray[float32, (n,)] を想定。numpy 依存を避け Any。
    src_lang_hint: str = "auto"


@dataclass(frozen=True)
class TranscribedPayload:
    """ASR → Translator の受け渡しデータ。

    役割: 認識結果テキストと検出言語。PCM は ASR 段で破棄する。
    """

    src_text: str
    src_lang: str


@dataclass(frozen=True)
class TranslatedPayload:
    """Translator → TTS の受け渡しデータ。

    役割: 翻訳結果テキストと翻訳先言語。
    """

    tgt_text: str
    tgt_lang: str


@dataclass(frozen=True)
class SynthesizedPayload:
    """TTS → Output の受け渡しデータ。

    役割: 合成済み PCM と再生サンプルレート。
    """

    tts_pcm: Any  # np.ndarray
    tts_samplerate: int


# 流通しうる payload の Union(型ヒント用)
Payload = Union[RawPayload, TranscribedPayload, TranslatedPayload, SynthesizedPayload]


@dataclass(frozen=True)
class PipelineMessage:
    """ステージ間キューを流れる封筒。

    役割: 1 発話に発行された seq_id と、その時点のステージ用 payload を一緒に運ぶ。
    seq_id で UtteranceLedger / 各種ログとの対応を取る。
    """

    seq_id: int
    payload: Payload
