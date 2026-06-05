"""Windows WASAPI AudioSession を列挙するヘルパー(段階 3)。

役割: ProcTap backend(per-process キャプチャ)が「現在音を出している可能性のある
プロセス」を提示するための列挙器。WASAPI セッション → CaptureSource への
変換ロジックを 1 モジュールに閉じ込め、ProcTap backend や試聴ダイアログから
共有して使う。

設計上の判断(`docs/design/feature-proctap-process-list/Plan.md` 参照):
- フィルタは `AudioSessionState.Active` のみ。GetPeakValue ベースの「実音検知」は
  瞬間値で取りこぼすため使わない(実音検知は試聴 UI 側で peak メータとして表現)。
- 同 PID に複数 AudioSession がある場合、ProcTap が PID 単位フックのため
  PID 単位で dedupe。最初に見つかった display_name を採用。
- プロセス名は psutil で取得し、欠落・権限不足時は "unknown" にフォールバック。
- pycaw / psutil の呼び出しは `_list_active_sessions()` / `_resolve_process_name()`
  に隔離し、テストでは monkeypatch で完全置換できる構造にする。

公開 API:
- `enumerate_active_processes() -> list[CaptureSource]`
- `get_session_meter(pid: int)`: 試聴ダイアログ用に IAudioMeterInformation を返す
  (見つからなければ None)

未インストール環境(pycaw / psutil 無し)では呼び出し時点で ImportError が伝播する。
呼び出し側(ProcTapCaptureBackend 等)は extras 未導入をすでに FatalError として
扱っているため、ここでは特別なハンドリングはしない。

## COM スレッドモード対策(2026-06-05 修正)
GUI スレッド(Tkinter / soundcard が STA で COM 初期化済み)から pycaw を直接呼ぶと
`comtypes` の `CoInitializeEx(MULTITHREADED)` が `RPC_E_CHANGED_MODE` を投げる。
これを避けるため、pycaw を触る公開 API は **専用ワーカースレッドで実行** する
(`_run_in_com_thread`)。スレッド内で `CoInitialize()` を呼び、呼び出し元へ結果を
同期的に返す。peak 取得の poll(`tick()` のような高頻度呼び出し)も同じ仕組みを
通すので、tick 自体を別スレッドで動かさなくても COM 競合を起こさない。
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from typing import Any, Callable, TypeVar

from voice_translator.common.types import CaptureKind, CaptureSource

logger = logging.getLogger(__name__)


# WASAPI AudioSessionState の Active を表す定数。pycaw.constants.AudioSessionState.Active
# と同じ値だが、ここで再宣言しておくことでテスト時に pycaw を完全モックしても
# 比較ロジックを成立させやすくする。
_AUDIO_SESSION_STATE_ACTIVE = 1

# COM 操作ワーカースレッドのタイムアウト(秒)。pycaw の `GetAllSessions()` は通常
# 数十 ms。5 秒を超えるならシステムが詰まっているか、デッドロック相当。
_COM_THREAD_TIMEOUT_SEC = 5.0

T = TypeVar("T")


def _run_in_com_thread(func: Callable[[], T], *, timeout: float = _COM_THREAD_TIMEOUT_SEC) -> T:
    """COM 操作を専用スレッドで実行し、結果を同期的に返す。

    Tkinter / soundcard が GUI スレッドで STA を要求しているため、pycaw / comtypes が
    要求する MTA とモード競合する(`RPC_E_CHANGED_MODE` = WinError -2147417850)。
    本ヘルパーは新規スレッドを立て、その中で `CoInitialize()` を呼んでから func を
    実行することで、GUI スレッドの COM 状態を汚染せずに pycaw を扱える。

    呼び出し元は **同期** に結果を受け取る(thread.join() で待つ)。pycaw の通常呼び出し
    は数十 ms 程度のため UI ブロッキングは実用上気にならない範囲。

    Args:
        func: COM API を叩く関数(引数なし、任意の戻り値)
        timeout: ワーカ完走を待つ最大秒数。タイムアウト時は `TimeoutError`。

    Raises:
        TimeoutError: スレッドが timeout 秒以内に終わらなかった
        Exception: func 内で発生した例外をそのまま再 raise
    """
    result_holder: list[T] = []
    error_holder: list[BaseException] = []

    def _worker() -> None:
        try:
            import comtypes  # 遅延 import: テスト時にモック差替できるよう
            try:
                comtypes.CoInitialize()
            except OSError:
                # 同一プロセス内の別スレッドで既に別モード初期化済み等。
                # 本ワーカースレッドは新規なので普通は起きないが、念のため握りつぶす。
                pass
            try:
                result_holder.append(func())
            finally:
                try:
                    comtypes.CoUninitialize()
                except Exception:  # noqa: BLE001
                    pass
        except BaseException as e:  # noqa: BLE001 - func 由来の例外をそのまま伝播するため
            error_holder.append(e)

    t = threading.Thread(target=_worker, name="vt_pycaw_com", daemon=True)
    t.start()
    t.join(timeout=timeout)
    if t.is_alive():
        raise TimeoutError(f"COM 操作が {timeout}s 以内に完了しませんでした")
    if error_holder:
        raise error_holder[0]
    if not result_holder:
        # ありえないが防衛: スレッドが何も詰めずに終わった
        raise RuntimeError("COM ワーカースレッドが結果を返しませんでした")
    return result_holder[0]


@dataclass(frozen=True)
class _SessionInfo:
    """`_list_active_sessions()` が返す中間表現(テスト容易性のため公開ロジックから分離)。"""

    pid: int
    process_name: str | None  # pycaw 経由で取れたプロセス名(取れなければ None)
    raw_session: Any           # pycaw の AudioSession インスタンス(試聴メータ取得に使う)


def enumerate_active_processes() -> list[CaptureSource]:
    """音声出力中のプロセスを列挙して `CaptureSource` のリストとして返す。

    挙動:
    - WASAPI AudioSession のうち `AudioSessionState.Active` のものを対象とする。
    - 同 PID 内に複数 session があれば 1 件に dedupe(最初に見つかった名前を採用)。
    - プロセス名は psutil で補完。欠落・権限不足時は "unknown"。
    - 戻り値の各要素は `kind=CaptureKind.PROCESS` / `source_id=str(pid)` /
      `display_name=f"{name} ({pid})"`。
    - pycaw 呼び出しは COM ワーカースレッド経由(GUI スレッドの COM 状態と競合させない)。

    Returns:
        list[CaptureSource]: 音声出力中のプロセス一覧。0 件もありうる。
    """
    return _run_in_com_thread(_enumerate_in_com_thread)


def _enumerate_in_com_thread() -> list[CaptureSource]:
    """COM ワーカースレッド内で実行する本体ロジック。

    `_list_active_sessions` / `_resolve_process_name` を呼ぶ純ロジック。
    テストでは `_run_in_com_thread` を経由せず本関数を直接 monkeypatch 可。
    """
    sessions = _list_active_sessions()
    seen_pids: set[int] = set()
    sources: list[CaptureSource] = []
    for info in sessions:
        if info.pid in seen_pids:
            continue
        seen_pids.add(info.pid)
        name = _resolve_process_name(info.pid, hint=info.process_name)
        sources.append(
            CaptureSource(
                source_id=str(info.pid),
                display_name=f"{name} ({info.pid})",
                is_loopback=False,
                kind=CaptureKind.PROCESS,
            )
        )
    return sources


def get_session_meter(pid: int):
    """指定 PID の AudioSession から `IAudioMeterInformation` を取得する(試聴用)。

    用途: プロセス選択ダイアログでレベルメータを動かすため、ProcTap の本キャプチャを
    使わずに `GetPeakValue()` の polling だけで「鳴っているか」を可視化する。

    Args:
        pid: 対象プロセス ID。

    Returns:
        `GetPeakValue() -> float` を持つメータ proxy。当該 PID の Active セッションが
        見つからなければ None。

    メータの内部実装は **COM 操作も `_run_in_com_thread` 経由でラップ** したラッパ。
    GUI スレッドから `meter.GetPeakValue()` を呼んでも、内部でワーカースレッドに丸投げ
    するので COM スレッドモード競合を起こさない(`MeterProxy` 参照)。
    """
    return _run_in_com_thread(lambda: _get_meter_in_com_thread(pid))


def _get_meter_in_com_thread(pid: int):
    """COM ワーカースレッド内でセッションを引いてメータ raw を返す(`get_session_meter` 本体)。"""
    sessions = _list_active_sessions()
    for info in sessions:
        if info.pid != pid:
            continue
        raw_meter = _query_meter(info.raw_session)
        if raw_meter is not None:
            return _MeterProxy(raw_meter)
    return None


class _MeterProxy:
    """`IAudioMeterInformation` の薄いラッパ。`GetPeakValue()` 呼び出しを COM ワーカ経由にする。

    GUI スレッドから直接 raw メータの `GetPeakValue()` を呼ぶと、まだ初期化されていない
    スレッドや別 apartment のスレッドで COM 競合を起こす可能性がある。安全側として全
    peak 取得を COM ワーカスレッドに送る。peak 取得は数十 μs なので、30fps poll でも
    スレッド起動コストを含めて 1-2 ms 程度に収まる(実用上問題なし)。
    """

    def __init__(self, raw_meter) -> None:
        self._raw = raw_meter

    def GetPeakValue(self) -> float:  # noqa: N802 - WASAPI 命名
        return _run_in_com_thread(self._raw.GetPeakValue)


# ============================================================
# pycaw / psutil 呼び出しの隔離(テスト時はここを monkeypatch する)
# ============================================================
def _list_active_sessions() -> list[_SessionInfo]:
    """pycaw で AudioSession を列挙し、Active なものだけを `_SessionInfo` で返す。

    pycaw を呼ぶ唯一の入口。テストではこの関数を monkeypatch で差し替える。
    """
    from pycaw.pycaw import AudioUtilities  # 遅延 import

    try:
        sessions = AudioUtilities.GetAllSessions()
    except Exception:
        logger.exception("AudioUtilities.GetAllSessions() failed")
        return []

    result: list[_SessionInfo] = []
    for s in sessions:
        try:
            pid = int(s.ProcessId or 0)
        except Exception:
            continue
        if pid <= 0:
            # PID 0 はシステムセッション。ProcTap でフックできないので除外。
            continue
        if not _is_active(s):
            continue
        name: str | None
        try:
            name = s.Process.name() if s.Process is not None else None
        except Exception:
            name = None
        result.append(_SessionInfo(pid=pid, process_name=name, raw_session=s))
    return result


def _is_active(session: Any) -> bool:
    """セッションが `AudioSessionState.Active` か判定する。

    pycaw のセッションは内部の `_ctl.GetState()` でステートを返す。
    """
    try:
        state = session._ctl.GetState()
    except Exception:
        return False
    return int(state) == _AUDIO_SESSION_STATE_ACTIVE


def _query_meter(session: Any):
    """pycaw の AudioSession から `IAudioMeterInformation` を `QueryInterface` で取得。

    pycaw 標準ルートでは取得関数が公開されていないため、`session._ctl` に対して
    QueryInterface を投げる。プライベート属性依存だが pycaw 20240210 以降で安定して動く。
    """
    try:
        from pycaw.pycaw import IAudioMeterInformation  # 遅延 import
        return session._ctl.QueryInterface(IAudioMeterInformation)
    except Exception:
        logger.exception("QueryInterface(IAudioMeterInformation) failed for session")
        return None


def _resolve_process_name(pid: int, *, hint: str | None) -> str:
    """プロセス名を解決する。psutil 失敗時は hint → "unknown" の順にフォールバック。

    pycaw 経由で取れた名前(hint)があってもそのまま信頼せず、psutil で再取得を
    試みる(pycaw 側が欠落することがあるため)。両方失敗したら "unknown"。
    """
    try:
        import psutil  # 遅延 import
        return psutil.Process(pid).name()
    except Exception:
        if hint:
            return hint
        return "unknown"
