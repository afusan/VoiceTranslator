"""Utterance / UtteranceTimeline の単体テスト。"""

from __future__ import annotations

import time

from voice_translator.common.utterance import Utterance, UtteranceTimeline


class TestUtteranceTimeline:
    def test_mark_records_time(self) -> None:
        tl = UtteranceTimeline()
        t = tl.mark("t_capture")
        assert tl.get("t_capture") == t

    def test_mark_overwrites_same_key(self) -> None:
        tl = UtteranceTimeline()
        first = tl.mark("t_asr")
        time.sleep(0.001)
        second = tl.mark("t_asr")
        assert second >= first
        assert tl.get("t_asr") == second

    def test_get_unknown_returns_none(self) -> None:
        tl = UtteranceTimeline()
        assert tl.get("nonexistent") is None

    def test_elapsed_returns_difference(self) -> None:
        tl = UtteranceTimeline()
        tl.mark("a")
        time.sleep(0.05)  # Windowsの time.sleep 最小解像度(~15.6ms)を上回る値
        tl.mark("b")
        elapsed = tl.elapsed("a", "b")
        assert elapsed is not None
        assert elapsed > 0  # 前後関係を確認(具体的なしきい値はOS依存なので緩く)

    def test_elapsed_with_missing_returns_none(self) -> None:
        tl = UtteranceTimeline()
        tl.mark("a")
        assert tl.elapsed("a", "b") is None
        assert tl.elapsed("x", "a") is None

    def test_as_dict_is_copy(self) -> None:
        tl = UtteranceTimeline()
        tl.mark("a")
        snapshot = tl.as_dict()
        snapshot["x"] = 99.9
        assert tl.get("x") is None  # 内部は影響を受けない


class TestUtterance:
    def test_defaults(self) -> None:
        u = Utterance()
        assert u.src_lang == "auto"
        assert u.src_text == ""
        assert u.tgt_text == ""
        assert isinstance(u.timeline, UtteranceTimeline)

    def test_field_progression(self) -> None:
        """各ステージでフィールドを段階的に埋められること。"""
        u = Utterance(pcm=b"raw", src_lang="en")
        u.timeline.mark("t_capture")

        u.src_text = "hello"
        u.timeline.mark("t_asr")

        u.tgt_lang = "ja"
        u.tgt_text = "こんにちは"
        u.timeline.mark("t_translate")

        u.tts_pcm = b"synth"
        u.timeline.mark("t_tts")

        u.timeline.mark("t_playback")

        assert u.src_text == "hello"
        assert u.tgt_text == "こんにちは"
        assert u.timeline.elapsed("t_capture", "t_playback") is not None
