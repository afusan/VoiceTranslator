"""1発話分のデータを保持するクラス群。

役割: 各パイプラインステージ(取得→VAD→ASR→翻訳→TTS→出力)が
追記していく**発話単位の中心データ**と、その時刻記録ユーティリティを提供する。
詳細は docs/design/Class.md を参照。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from time import monotonic
from typing import Any


class UtteranceTimeline:
    """Utterance に紐づくステージ別タイムスタンプ記録器。

    役割: `mark(stage)` 呼び出しで `monotonic()` を打ち、後段でレイテンシ算出に使う。
    """

    def __init__(self) -> None:
        self._times: dict[str, float] = {}

    def mark(self, stage: str) -> float:
        """ステージ名で時刻を記録し、その値を返す。同名は上書きする。"""
        now = monotonic()
        self._times[stage] = now
        return now

    def get(self, stage: str) -> float | None:
        """記録済み時刻を取得。未記録なら None。"""
        return self._times.get(stage)

    def elapsed(self, start_stage: str, end_stage: str) -> float | None:
        """2ステージ間の経過秒。どちらか未記録なら None。"""
        start = self._times.get(start_stage)
        end = self._times.get(end_stage)
        if start is None or end is None:
            return None
        return end - start

    def as_dict(self) -> dict[str, float]:
        """記録内容のコピーを返す(ロギング用)。"""
        return dict(self._times)


@dataclass
class Utterance:
    """1発話を表すパイプライン上のデータ。

    役割: 各ステージがフィールドを追記しながら下流に流す。
    全フィールドはステージ進行に伴い段階的に埋められる。
    """

    # VAD で確定
    pcm: Any = None  # np.ndarray[float32] を想定。numpy 依存を避けるため Any。
    src_lang: str = "auto"

    # ASR で追記
    src_text: str = ""

    # Translator で追記
    tgt_lang: str = ""
    tgt_text: str = ""

    # TTS で追記
    tts_pcm: Any = None

    # 全ステージで mark
    timeline: UtteranceTimeline = field(default_factory=UtteranceTimeline)
