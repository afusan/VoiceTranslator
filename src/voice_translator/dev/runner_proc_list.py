"""WASAPI AudioSession 列挙の診断ランナー(切り分け用)。

役割: 別環境で「音は出ているのにプロセス選択ダイアログが空」のような症状が
出たとき、どこで弾かれているかを 1 コマンドで吐かせる調査ツール。
本体の `process_enumerator.enumerate_active_processes()` が見ているのと同じ
WASAPI セッション一覧を、フィルタ前 / 後の両方で表示する。

使い方(別環境で):
    py -m uv run --extra cpu --extra capture-proctap python -m voice_translator.dev.runner_proc_list
    py -m uv run --extra cuda --extra capture-proctap python -m voice_translator.dev.runner_proc_list

出力:
  - 全 session の生情報(ProcessId / state(値+名前) / Process.name() / DisplayName)
  - state 別の件数集計
  - `enumerate_active_processes()` の最終結果(本体 UI と同じビュー)

state の見方(`AudioSessionState`):
  0 = Inactive  … プロセスは音声セッションを持っているが現在再生していない
  1 = Active    … 現在再生中(本アプリはこれだけ拾う)
  2 = Expired   … セッション終了

「音が出ているのに Active=1 が出ない」なら、pycaw のバージョンで state 定義が
変わったか、デフォルトエンドポイントが想定と違う(別の出力デバイスを見ている)
可能性が高い。
"""

from __future__ import annotations

import sys
from collections import Counter

# ============================================================
# 1) 全 session の生情報を吐く(フィルタ前)
# ============================================================
_STATE_NAMES = {0: "Inactive", 1: "Active", 2: "Expired"}


def dump_all_sessions() -> int:
    """`AudioUtilities.GetAllSessions()` の全結果を表で出す。

    Returns: state ごとの件数集計を考慮した「Active 件数」(本体が拾うべき件数)。
    """
    try:
        from pycaw.pycaw import AudioUtilities
    except ImportError as e:
        print(f"[ERROR] pycaw が import できません: {e}", file=sys.stderr)
        print("       `uv sync --extra capture-proctap` を実行してください。", file=sys.stderr)
        return -1

    try:
        sessions = AudioUtilities.GetAllSessions()
    except Exception as e:  # noqa: BLE001
        print(f"[ERROR] AudioUtilities.GetAllSessions() 例外: {e}", file=sys.stderr)
        return -1

    print(f"## AudioUtilities.GetAllSessions() = {len(sessions)} 件")
    print(f"{'idx':>3} {'pid':>6} {'state':<12} {'name':<30} {'display':<30}")
    print("-" * 90)

    state_counter: Counter = Counter()
    active_count = 0
    for i, s in enumerate(sessions):
        try:
            pid = int(s.ProcessId or 0)
        except Exception:  # noqa: BLE001
            pid = -1
        try:
            state = int(s._ctl.GetState())  # noqa: SLF001
        except Exception:  # noqa: BLE001
            state = -1
        state_name = _STATE_NAMES.get(state, f"?({state})")
        state_counter[state_name] += 1
        if state == 1:
            active_count += 1
        try:
            name = s.Process.name() if s.Process is not None else "(no Process)"
        except Exception as e:  # noqa: BLE001
            name = f"(err: {e})"
        try:
            display = s.DisplayName or ""
        except Exception:  # noqa: BLE001
            display = "(err)"
        print(
            f"{i:>3} {pid:>6} {state_name:<12} {name[:28]:<30} {display[:28]:<30}"
        )

    print()
    print("## state 別集計:")
    for state_name, n in state_counter.most_common():
        print(f"   {state_name:<12} {n} 件")
    print(f"=> Active(1) 件数: {active_count}")
    return active_count


# ============================================================
# 2) 本体経路の最終結果(enumerate_active_processes)
# ============================================================
def dump_enumerate_result() -> None:
    """本体 UI が実際に受け取るリストを表示する。"""
    try:
        from voice_translator.capture import process_enumerator as pe
    except ImportError as e:
        print(f"[ERROR] process_enumerator が import できません: {e}", file=sys.stderr)
        return

    try:
        sources = pe.enumerate_active_processes()
    except Exception as e:  # noqa: BLE001
        print(f"[ERROR] enumerate_active_processes 例外: {e}", file=sys.stderr)
        return

    print()
    print(f"## enumerate_active_processes() = {len(sources)} 件(本体 UI と同じビュー)")
    if not sources:
        print("   (空)")
    for i, src in enumerate(sources):
        print(f"   {i:>3} source_id={src.source_id!r} display={src.display_name!r}")


# ============================================================
# 3) デフォルト出力エンドポイントの確認(参考情報)
# ============================================================
def dump_default_endpoint() -> None:
    """デフォルト出力デバイス名を表示する(セッションがこれに紐づく前提)。"""
    try:
        from pycaw.pycaw import AudioUtilities
        spk = AudioUtilities.GetSpeakers()
        # IMMDevice の FriendlyName を取れれば嬉しいがバージョンで違うので best-effort
        try:
            from pycaw.utils import AudioDeviceFriendlyName  # type: ignore[import-not-found]
            name = AudioDeviceFriendlyName(spk)
        except Exception:  # noqa: BLE001
            name = "(取得不可)"
        print(f"## デフォルト出力デバイス: {name}")
    except Exception as e:  # noqa: BLE001
        print(f"## デフォルト出力デバイス取得失敗: {e}")


def run() -> int:
    dump_default_endpoint()
    print()
    rc = dump_all_sessions()
    dump_enumerate_result()
    print()
    if rc == 0:
        print("**観察結果**: Active セッションが 0 件です。")
        print("- 対象プロセス(Discord, Chrome 等)で本当に音が再生中か確認")
        print("- Windows 設定 → サウンド → 音量ミキサー で同じ瞬間に表示されているか")
        print("- 出力先が「デフォルト出力」と一致しているか(別ヘッドセット等に出ていないか)")
    elif rc > 0:
        print(
            "**観察結果**: Active セッションは存在しますが、本体 UI には反映されません。"
        )
        print("- enumerate_active_processes() の結果と比べ、どこで欠けているかを確認")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(run())
