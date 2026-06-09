"""gui/logic/status_summary.py の単体テスト(純関数)。

golden テスト(TestGoldenFormat)は、移行元(app_controller.get_status_summary +
control_panel._refresh_status_text)の出力を byte 単位で固定したもの。
**表示形式を変える意図が無い限り、この期待文字列を書き換えてはならない。**
"""

from __future__ import annotations

from voice_translator.common.types import (
    ErrorRecord,
    LayerKind,
    LayerStatusLine,
    ModelStatus,
)
from voice_translator.gui.logic.status_summary import (
    append_gui_events,
    format_status_summary,
)


def _line(layer, name, status, hint="") -> LayerStatusLine:
    return LayerStatusLine(
        layer=layer, backend_name=name, status=status, dl_size_hint=hint,
    )


_FULL_LINES = [
    _line(LayerKind.CAPTURE, "soundcard", ModelStatus.LOADED),
    _line(LayerKind.VAD, "silero", ModelStatus.LOADED),
    _line(LayerKind.ASR, "faster_whisper", ModelStatus.DOWNLOADING, " (~0.5GB)"),
    _line(LayerKind.TRANSLATOR, "nllb200", ModelStatus.INIT),
    _line(LayerKind.TTS, "sapi", ModelStatus.NOT_DOWNLOADED),
    _line(LayerKind.OUTPUT, "soundcard", ModelStatus.LOADING),
]


class TestGoldenFormat:
    def test_full_composition_matches_legacy_output(self) -> None:
        """レイヤ 6 行 + エラー 2 件 + 操作イベント 3 件のフル合成(golden)。"""
        errors = [
            (
                LayerKind.ASR,
                ErrorRecord(
                    timestamp=2000.0, message="conn reset",
                    exc_type="ConnectionError", context="transcribe",
                ),
            ),
            (
                LayerKind.TTS,
                ErrorRecord(
                    timestamp=1000.0, message="voice missing",
                    exc_type="RuntimeError", context=None,
                ),
            ),
        ]
        events = [
            "[10:00:00] [起動失敗] x",
            "[10:00:01] [出力テスト] 再生完了: 'テスト音声'",
            "[10:00:02] [致命的エラー] y",
        ]
        expected = (
            "[capture] soundcard: Loaded\n"
            "[vad] silero: Loaded\n"
            "[asr] faster_whisper: Downloading... (~0.5GB)\n"
            "[translator] nllb200: Init\n"
            "[tts] sapi: Not Downloaded\n"
            "[output] soundcard: Loading...\n"
            "\n"
            "最近のエラー:\n"
            "  [asr] ConnectionError: conn reset (transcribe)\n"
            "  [tts] RuntimeError: voice missing\n"
            "\n"
            "操作イベント:\n"
            "  [10:00:02] [致命的エラー] y\n"
            "  [10:00:01] [出力テスト] 再生完了: 'テスト音声'\n"
            "  [10:00:00] [起動失敗] x"
        )
        assert format_status_summary(_FULL_LINES, errors, events) == expected

    def test_layers_only(self) -> None:
        """エラー・イベント無し → レイヤ行のみ(末尾改行なし)。"""
        lines = _FULL_LINES[:2]
        expected = "[capture] soundcard: Loaded\n[vad] silero: Loaded"
        assert format_status_summary(lines, [], []) == expected


class TestErrorSection:
    def test_no_error_section_when_empty(self) -> None:
        out = format_status_summary(_FULL_LINES, [], [])
        assert "最近のエラー:" not in out

    def test_errors_truncated_to_max(self) -> None:
        """6 件渡しても新しい順(渡された順)5 件で打ち切り。"""
        errors = [
            (
                LayerKind.ASR,
                ErrorRecord(
                    timestamp=float(1000 - i), message=f"e{i}",
                    exc_type="RuntimeError", context=None,
                ),
            )
            for i in range(6)
        ]
        out = format_status_summary(_FULL_LINES, errors, [])
        assert "e0" in out and "e4" in out
        assert "e5" not in out

    def test_context_suffix_present_only_when_set(self) -> None:
        errors = [
            (
                LayerKind.ASR,
                ErrorRecord(
                    timestamp=1.0, message="m1", exc_type="E1", context="load",
                ),
            ),
            (
                LayerKind.VAD,
                ErrorRecord(timestamp=0.5, message="m2", exc_type="E2", context=None),
            ),
        ]
        out = format_status_summary(_FULL_LINES, errors, [])
        assert "  [asr] E1: m1 (load)" in out
        assert "  [vad] E2: m2" in out
        assert "m2 (" not in out


class TestGuiEventsSection:
    def test_no_event_section_when_empty(self) -> None:
        out = format_status_summary(_FULL_LINES, [], [])
        assert "操作イベント:" not in out

    def test_events_newest_first_truncated_to_max(self) -> None:
        """7 件(古い→新しい順)→ 新しい順 5 件表示。"""
        events = [f"[t{i}] ev{i}" for i in range(7)]
        out = format_status_summary(_FULL_LINES, [], events)
        section = out.split("操作イベント:\n", 1)[1]
        shown = section.splitlines()
        assert shown == [f"  [t{i}] ev{i}" for i in (6, 5, 4, 3, 2)]

    def test_append_gui_events_on_failure_text(self) -> None:
        """ステータス取得失敗時も操作イベントは付加される(View の失敗分岐用)。"""
        out = append_gui_events("(ステータス取得に失敗: boom)", ["[t] ev"])
        assert out == "(ステータス取得に失敗: boom)\n\n操作イベント:\n  [t] ev"

    def test_append_gui_events_without_events_returns_input(self) -> None:
        assert append_gui_events("base", []) == "base"
