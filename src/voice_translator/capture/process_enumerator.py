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
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from voice_translator.common.types import CaptureKind, CaptureSource

logger = logging.getLogger(__name__)


# WASAPI AudioSessionState の Active を表す定数。pycaw.constants.AudioSessionState.Active
# と同じ値だが、ここで再宣言しておくことでテスト時に pycaw を完全モックしても
# 比較ロジックを成立させやすくする。
_AUDIO_SESSION_STATE_ACTIVE = 1


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

    Returns:
        list[CaptureSource]: 音声出力中のプロセス一覧。0 件もありうる。
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
        IAudioMeterInformation 相当のオブジェクト(`GetPeakValue() -> float` を持つ)。
        当該 PID の Active セッションが見つからなければ None。
    """
    sessions = _list_active_sessions()
    for info in sessions:
        if info.pid != pid:
            continue
        meter = _query_meter(info.raw_session)
        if meter is not None:
            return meter
    return None


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
