"""status_summary: ステータス集約テキストの整形(純関数)。

役割: AppController.get_status_snapshot() のデータと GUI 操作イベント履歴から、
ステータステキストボックスに貼る 1 ブロックの文字列を組み立てる。

移行元(P1 / refactor-ui-3move): app_controller.py の `get_status_summary`(整形部)と
control_panel.py の `_refresh_status_text`(操作イベント合成部)。
出力文字列は移行元と byte 単位で同一に保つこと(golden テスト対象)。
"""

from __future__ import annotations

from typing import Sequence

from voice_translator.common.types import (
    AuthState,
    ErrorRecord,
    LayerKind,
    LayerStatusLine,
)

from .auth_display import AUTH_MISSING_TEXT, AUTH_UNVERIFIED_TEXT


def format_status_summary(
    lines: Sequence[LayerStatusLine],
    errors: Sequence[tuple[LayerKind, ErrorRecord]],
    gui_events: Sequence[str],
    *,
    max_errors: int = 5,
    max_events: int = 5,
) -> str:
    """レイヤ状態 + backend エラー + GUI 操作イベントを 1 ブロックに整形する。

    - lines: 表示順で渡す(呼び出し側が LayerKind 順に構築する)
    - errors: timestamp 降順(新しい順)でソート済みを渡す。max_errors 件で打ち切り
    - gui_events: 古い → 新しい順で渡す(deque のまま)。新しい順 max_events 件で表示
    """
    out = [_format_layer_line(line) for line in lines]
    if errors:
        out.append("")
        out.append("最近のエラー:")
        for layer, rec in list(errors)[:max_errors]:
            ctx = f" ({rec.context})" if rec.context else ""
            out.append(f"  [{layer.value}] {rec.exc_type}: {rec.message}{ctx}")
    return append_gui_events("\n".join(out), gui_events, max_events=max_events)


def _format_layer_line(line: LayerStatusLine) -> str:
    """レイヤ 1 行の表示。編成上の扱い(吸収 / 対象外)を実態どおりに出す。

    - 通常:   `[asr] faster_whisper: Loaded`
    - 吸収:   `[translator] (asr の faster_whisper_translate で実行)`
      — 設定されている backend 名や状態を出すと「それが動く」ように見えるため出さない
    - 対象外: `[tts] (なし)`
    - 認証未完了(static 判定)はインスタンス状態より優先して出す
      (設定パネルの行ステータス上書きと同じ文言: `Missing Credentials` / `Not Verified`)
    """
    if line.disposition == "absorbed":
        return (
            f"[{line.layer.value}] "
            f"({line.absorbed_into} の {line.absorbed_backend} で実行)"
        )
    if line.disposition == "skipped":
        return f"[{line.layer.value}] (なし)"
    status_text = f"{line.status.value}{line.dl_size_hint}"
    if line.auth == AuthState.MISSING:
        status_text = AUTH_MISSING_TEXT
    elif line.auth == AuthState.UNVERIFIED:
        status_text = AUTH_UNVERIFIED_TEXT
    return f"[{line.layer.value}] {line.backend_name}: {status_text}"


def append_gui_events(
    summary: str, gui_events: Sequence[str], *, max_events: int = 5,
) -> str:
    """summary 末尾に「操作イベント」セクションを付加する(イベントが無ければそのまま)。

    ステータス取得に失敗したときも操作イベントだけは表示する必要があるため、
    `format_status_summary` から分離して公開している(View の失敗分岐で使う)。
    """
    if not gui_events:
        return summary
    parts = [summary, "", "操作イベント:"]
    for ev in list(gui_events)[-max_events:][::-1]:
        parts.append(f"  {ev}")
    return "\n".join(parts)
