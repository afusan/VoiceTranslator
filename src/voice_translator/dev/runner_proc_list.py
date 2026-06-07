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
  0 = Inactive  … セッションは存在するが、現在ストリームが running 中ではない
  1 = Active    … 現在ストリームが running 中
  2 = Expired   … セッション終了済み(本アプリは除外)

本アプリの enumerate_active_processes() は **Inactive + Active を採用**(Expired のみ
除外)。Sndvol(Windows の音量ミキサー)の表示集合と一致する。Win11 では無音 10 秒で
audio engine が sleep に入り、観測時点では多くが Inactive 状態のため。
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


# ============================================================
# 4) 全エンドポイント総当たり(IMMDeviceEnumerator)
# ============================================================
_DEVICE_STATE_ACTIVE = 0x00000001  # MMDeviceAPI: DEVICE_STATE_ACTIVE


def dump_all_endpoints_sessions() -> None:
    """全 Render エンドポイントを列挙し、各エンドポイントの session も列挙する。

    `AudioUtilities.GetAllSessions()` はデフォルトエンドポイントしか見ないため、
    Chrome/Firefox/Spotify 等が**別エンドポイント**に紐づいていると見えなくなる。
    本関数で「どのデバイスに何のセッションが居るか」を可視化する。
    """
    print("## 全 Render エンドポイント走査(IMMDeviceEnumerator 経由):")
    try:
        from comtypes import CLSCTX_INPROC_SERVER, GUID
        from pycaw.pycaw import (
            AudioUtilities,
            IAudioSessionManager2,
            IMMDeviceEnumerator,
        )
    except Exception as e:  # noqa: BLE001
        print(f"   [ERROR] 必要な型の import 失敗: {e}")
        return

    try:
        # IMMDeviceEnumerator のインスタンスを作る(pycaw が COM 経由で提供)
        device_enum = AudioUtilities.GetDeviceEnumerator()  # 内部で MMDeviceEnumerator
    except Exception as e:  # noqa: BLE001
        print(f"   [ERROR] GetDeviceEnumerator 失敗: {e}")
        return

    # EDataFlow.eRender = 0
    try:
        collection = device_enum.EnumAudioEndpoints(0, _DEVICE_STATE_ACTIVE)
        count = collection.GetCount()
    except Exception as e:  # noqa: BLE001
        print(f"   [ERROR] EnumAudioEndpoints 失敗: {e}")
        return

    print(f"   検出 Render デバイス数: {count}")
    for i in range(count):
        try:
            dev = collection.Item(i)
        except Exception as e:  # noqa: BLE001
            print(f"   [{i}] Item 失敗: {e}")
            continue

        # FriendlyName を取りに行く
        friendly = "(取得不可)"
        try:
            from pycaw.utils import AudioDeviceFriendlyName  # type: ignore[import-not-found]
            friendly = AudioDeviceFriendlyName(dev)
        except Exception:  # noqa: BLE001
            try:
                friendly = dev.GetId() or "(no id)"
            except Exception:  # noqa: BLE001
                pass

        print(f"\n   [Device {i}] {friendly}")
        # この device から IAudioSessionManager2 を Activate
        try:
            mgr = dev.Activate(
                IAudioSessionManager2._iid_, CLSCTX_INPROC_SERVER, None,
            )
            from comtypes import cast, POINTER
            mgr = cast(mgr, POINTER(IAudioSessionManager2))
        except Exception as e:  # noqa: BLE001
            print(f"      Activate 失敗: {e}")
            continue

        try:
            enumerator = mgr.GetSessionEnumerator()
            scount = enumerator.GetCount()
        except Exception as e:  # noqa: BLE001
            print(f"      GetSessionEnumerator 失敗: {e}")
            continue
        if scount == 0:
            print("      (セッションなし)")
            continue

        for j in range(scount):
            try:
                ctl = enumerator.GetSession(j)
                ctl2 = ctl.QueryInterface(
                    __import__("pycaw.pycaw", fromlist=["IAudioSessionControl2"]).IAudioSessionControl2
                )
                pid = ctl2.GetProcessId()
                state = ctl.GetState()
            except Exception as e:  # noqa: BLE001
                print(f"      [{j}] 読み取り失敗: {e}")
                continue
            # プロセス名を psutil で
            try:
                import psutil
                pname = psutil.Process(pid).name() if pid > 0 else "(system)"
            except Exception:  # noqa: BLE001
                pname = "(unknown)"
            state_name = _STATE_NAMES.get(state, f"?({state})")
            print(f"      [{j}] pid={pid:>6} state={state_name:<10} name={pname}")


def run() -> int:
    dump_default_endpoint()
    print()
    rc = dump_all_sessions()
    dump_enumerate_result()
    print()
    print("=" * 60)
    print("# 追加診断: 全エンドポイント走査")
    print("=" * 60)
    dump_all_endpoints_sessions()
    print()
    print("**メモ**: 本アプリは Active + Inactive のセッションを採用(Expired のみ除外)。")
    print("Sndvol(Win 音量ミキサー)に表示される集合と一致する。")
    print("`GetAllSessions()` がデフォルトエンドポイントしか見ないため、別デバイスに")
    print("紐づくアプリは「全エンドポイント走査」セクションでのみ見える。")
    if rc == 0:
        print("**観察結果**: Active セッションが 0 件ですが、Inactive があれば本体は拾います。")
        print("上の `enumerate_active_processes()` の件数も合わせて確認してください。")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(run())
