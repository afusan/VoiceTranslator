"""Windows WASAPI AudioSession を列挙するヘルパー(段階 3 + Peak Worker リファクタ)。

役割: ProcTap backend(per-process キャプチャ)が「現在音を出している可能性のある
プロセス」を提示するための列挙器と、プロセス選択ダイアログの試聴メータ用 peak 値の
継続供給。

## 設計上の判断
- フィルタは `AudioSessionState.Active` のみ(GetPeakValue ベースの「実音検知」は
  瞬間値で取りこぼすため使わない)。
- 同 PID に複数 AudioSession がある場合、ProcTap が PID 単位フックのため
  PID 単位で dedupe。最初に見つかった display_name を採用。
- プロセス名は psutil で取得し、欠落・権限不足時は "unknown" にフォールバック。
- pycaw / psutil の呼び出しは `_list_active_sessions()` / `_resolve_process_name()`
  に隔離し、テストでは monkeypatch で完全置換できる構造にする。

## COM スレッドモード対策(永続ワーカースレッド方式)
GUI スレッド(Tkinter / soundcard が STA で COM 初期化済み)から pycaw を直接呼ぶと
`comtypes` の `CoInitializeEx(MULTITHREADED)` が `RPC_E_CHANGED_MODE` を投げる。
これを避けるため、本モジュールは **`_PeakWorker` 永続スレッド** を 1 つ持ち、
全 COM 操作をその中で実行する:

- アプリ起動から最初の API 呼び出し時にワーカ起動 → `CoInitialize()` 1 回
- 列挙 (`enumerate_active_processes`) はコマンドキュー経由で同期実行
- 試聴は `start_audition(pid)` で対象を設定 → ワーカ内部で **5fps poll** ループが
  peak を取得して `_latest_peak: float` を atomic 更新
- GUI スレッドは `latest_peak()` を atomic 読みするだけ(スレッド境界をまたがない)
- `stop_audition()` で poll 停止 / `dispose()` でワーカ停止(プロセス終了時は daemon
  で自然消滅、明示 dispose は通常不要)

スレッド数は **1 個固定**(従来は peak 1 回ごとに新規スレッド = 1 分試聴で 1800 個)、
COM 初期化は **1 回だけ**。GUI スレッドと COM が完全分離。

## 公開 API
- `enumerate_active_processes() -> list[CaptureSource]`
- `start_audition(pid: int) -> bool`(メータが取れて poll 開始したら True)
- `stop_audition() -> None`
- `latest_peak() -> float`(GUI スレッドから atomic 読み)
- `dispose() -> None`(テストや明示停止用。通常不要)

未インストール環境(pycaw / psutil 無し)では呼び出し時点で ImportError が伝播する。
"""

from __future__ import annotations

import logging
import queue
import threading
from dataclasses import dataclass
from typing import Any, Callable, TypeVar

from voice_translator.common.types import CaptureKind, CaptureSource

logger = logging.getLogger(__name__)


# WASAPI AudioSessionState 定数(`pycaw.constants.AudioSessionState.*` と同じ値)。
# ここで再宣言しておくとテスト時に pycaw を完全モックしても比較が成立する。
#
# Microsoft 公式仕様(audiosessiontypes.h):
#   Inactive(0): ストリームはあるが running 中ではない(Stop 後 / 未 Start)
#   Active(1):   少なくとも 1 つのストリームが running 中(IAudioClient::Start 直後)
#   Expired(2):  ストリームが完全消失(セッション終了済み)
#
# 採用条件は **Active + Inactive**(Expired のみ除外)。これは Sndvol(Windows の
# 音量ミキサー)が表示するセッション集合と一致する。
# 「Active のみ」だと Win11 の audio engine sleep(10 秒で Inactive 化)や、
# 多くのアプリの「無音区間で Stop を呼ぶ」実装で観測時点にほぼ全部 Inactive に落ちて
# 列挙が空になる(2026-06-08 別環境で実観測)。proc-tap は PID 指定で動くので
# Inactive な PID でも、Start 押下時に音が鳴っていれば普通にキャプチャできる。
_AUDIO_SESSION_STATE_INACTIVE = 0
_AUDIO_SESSION_STATE_ACTIVE = 1
_AUDIO_SESSION_STATE_EXPIRED = 2
_CAPTURABLE_STATES = frozenset({_AUDIO_SESSION_STATE_INACTIVE, _AUDIO_SESSION_STATE_ACTIVE})

# Peak の内部 poll 間隔(秒)。5fps = 200ms。
# 視認性は十分(decay 効果で人間の目には連続的に見える)、COM 呼び出し負荷は
# 30fps の 1/6 で軽量。
_PEAK_POLL_INTERVAL_SEC = 0.2

# コマンドキューの待ちタイムアウト(秒)。これより小さい値で poll loop が回る。
_CMD_QUEUE_POLL_TIMEOUT_SEC = _PEAK_POLL_INTERVAL_SEC

# 列挙等のワンショット操作のクライアント側待ちタイムアウト(秒)。
_DEFAULT_SUBMIT_TIMEOUT_SEC = 5.0

T = TypeVar("T")


@dataclass(frozen=True)
class _SessionInfo:
    """`_list_active_sessions()` が返す中間表現(テスト容易性のため公開ロジックから分離)。"""

    pid: int
    process_name: str | None  # pycaw 経由で取れたプロセス名(取れなければ None)
    raw_session: Any           # pycaw の AudioSession インスタンス(試聴メータ取得に使う)


# ============================================================
# _PeakWorker: COM 操作専用の永続スレッド
# ============================================================
class _PeakWorker:
    """COM 操作を専用の永続スレッドで処理する。

    - **enumerate** 等のワンショット操作はコマンドキュー経由(同期)
    - **試聴 peak 取得** はワーカ内部の 5fps poll で行い、`_latest_peak` に atomic 保持
    - GUI スレッドは `latest_peak()` を atomic 読みするだけ(スレッド境界なし)

    スレッド寿命:
    - `__init__` で起動、`CoInitialize()` を 1 回
    - `dispose()` で明示停止可、通常は daemon=True なのでプロセス終了で自然消滅
    """

    def __init__(self) -> None:
        # コマンドキュー: (func, result_holder, error_holder, done_event)
        self._cmd_queue: queue.Queue = queue.Queue()
        # 試聴対象 PID(None = poll しない)。スレッド内のみで触る前提だが、
        # 読みは GUI スレッドからも参照するので atomic 想定で扱う(GIL で安全)。
        self._audition_pid: int | None = None
        # 現在のメータ(COM オブジェクト)。スレッド内のみで触る
        self._current_meter: Any = None
        # 最新 peak 値。GUI スレッドが atomic に読む(float 代入は GIL 下で atomic)
        self._latest_peak: float = 0.0
        # 停止フラグ
        self._stop_event = threading.Event()
        # スレッド起動
        self._thread = threading.Thread(
            target=self._run, name="vt_pycaw_com", daemon=True,
        )
        self._thread.start()

    # ---- 公開 API -------------------------------------------------------
    def submit(self, func: Callable[[], T], *, timeout: float = _DEFAULT_SUBMIT_TIMEOUT_SEC) -> T:
        """ワーカスレッド内で func を実行し、結果を同期で返す。

        Raises:
            TimeoutError: 制限時間内に終わらなかった
            Exception: func 内の例外をそのまま再 raise
        """
        done = threading.Event()
        result_holder: list[T] = []
        error_holder: list[BaseException] = []
        self._cmd_queue.put((func, result_holder, error_holder, done))
        if not done.wait(timeout):
            raise TimeoutError(f"COM 操作が {timeout}s 以内に完了しませんでした")
        if error_holder:
            raise error_holder[0]
        if not result_holder:
            # ありえないが防衛: func が完了したのに値を詰めなかった
            return None  # type: ignore[return-value]
        return result_holder[0]

    def start_audition(self, pid: int) -> bool:
        """試聴対象 PID を設定する。ワーカ内部で peak の 5fps poll が始まる。

        Returns:
            True: メータが取れて poll 開始 / False: メータ取得失敗(該当セッション無等)
        """
        return self.submit(lambda: self._do_start_audition(pid))

    def stop_audition(self) -> None:
        """試聴を停止する(poll 停止 + peak を 0 に)。"""
        self.submit(self._do_stop_audition)

    def latest_peak(self) -> float:
        """最新の peak 値を atomic に読む(GUI スレッドから安全)。"""
        return self._latest_peak

    def is_auditioning(self) -> bool:
        """現在試聴 poll が動いているか(GUI スレッドから atomic に読む)。"""
        return self._audition_pid is not None

    def dispose(self) -> None:
        """ワーカを明示停止する(プロセス終了時は daemon で自然消滅するので通常不要)。"""
        self._stop_event.set()
        # キューを起こす
        self._cmd_queue.put(None)
        self._thread.join(timeout=2.0)

    # ---- ワーカスレッド本体 ----------------------------------------------
    def _run(self) -> None:
        """ワーカスレッドのメインループ。COM 初期化 → コマンド消化 + peak poll。"""
        try:
            import comtypes
            try:
                comtypes.CoInitialize()
            except OSError:
                # 既に別モードで初期化済み等。通常は新規スレッドなのでここには来ない。
                pass
        except Exception:  # noqa: BLE001 - comtypes 未インストール等
            logger.exception("_PeakWorker: comtypes import に失敗")
            return

        try:
            while not self._stop_event.is_set():
                try:
                    item = self._cmd_queue.get(timeout=_CMD_QUEUE_POLL_TIMEOUT_SEC)
                except queue.Empty:
                    item = None
                if item is not None:
                    if isinstance(item, tuple):
                        func, result_holder, error_holder, done = item
                        try:
                            result_holder.append(func())
                        except BaseException as e:  # noqa: BLE001 - func 由来を伝播
                            error_holder.append(e)
                        finally:
                            done.set()
                    else:
                        # None = dispose 通知
                        break
                # 試聴中なら peak 取得(poll の周期は queue.get の timeout で律速)
                self._tick_peak()
        finally:
            try:
                import comtypes
                comtypes.CoUninitialize()
            except Exception:  # noqa: BLE001
                pass

    def _tick_peak(self) -> None:
        """試聴中であれば current_meter.GetPeakValue() を呼び _latest_peak を更新する。"""
        if self._audition_pid is None or self._current_meter is None:
            return
        try:
            self._latest_peak = float(self._current_meter.GetPeakValue())
        except Exception:  # noqa: BLE001 - メータ消失等
            self._latest_peak = 0.0

    def _do_start_audition(self, pid: int) -> bool:
        """ワーカスレッド内で実行: 指定 PID のメータを取得して poll 対象に設定。"""
        self._current_meter = None
        self._latest_peak = 0.0
        self._audition_pid = None
        sessions = _list_active_sessions()
        for info in sessions:
            if info.pid != pid:
                continue
            raw_meter = _query_meter(info.raw_session)
            if raw_meter is None:
                return False
            self._current_meter = raw_meter
            self._audition_pid = pid
            return True
        return False

    def _do_stop_audition(self) -> None:
        """ワーカスレッド内で実行: 試聴解除。"""
        self._current_meter = None
        self._latest_peak = 0.0
        self._audition_pid = None


# ============================================================
# モジュールシングルトン
# ============================================================
_worker: _PeakWorker | None = None
_worker_lock = threading.Lock()


def _get_worker() -> _PeakWorker:
    """グローバルな `_PeakWorker` を遅延初期化で 1 つだけ返す。"""
    global _worker
    if _worker is None:
        with _worker_lock:
            if _worker is None:
                _worker = _PeakWorker()
    return _worker


def dispose() -> None:
    """グローバルワーカを停止する(主にテスト後始末用)。

    プロセス終了時は daemon thread で自然消滅するので通常は呼ばなくてよい。
    呼んだ後の API 呼び出しは新しいワーカを起動する。
    """
    global _worker
    with _worker_lock:
        if _worker is not None:
            try:
                _worker.dispose()
            except Exception:  # noqa: BLE001
                pass
            _worker = None


# ============================================================
# 公開 API
# ============================================================
def enumerate_active_processes() -> list[CaptureSource]:
    """音声セッションを持つプロセスを列挙して `CaptureSource` のリストとして返す。

    挙動:
    - WASAPI AudioSession のうち state が **Active(1) または Inactive(0)** の
      ものを対象とする(Expired のみ除外)。Sndvol(音量ミキサー)と一致する集合。
    - 同 PID 内に複数 session があれば 1 件に dedupe(最初に見つかった名前を採用)。
    - プロセス名は psutil で補完。欠落・権限不足時は "unknown"。
    - 戻り値の各要素は `kind=CaptureKind.PROCESS` / `source_id=str(pid)` /
      `display_name=f"{name} ({pid})"`。
    - pycaw 呼び出しは永続ワーカスレッド経由(GUI スレッドの COM 状態と競合させない)。

    関数名は歴史的経緯で `_active_` を含むが、実際の採用範囲は Active + Inactive。
    Win11 の audio engine sleep(無音 10 秒で Inactive 化)や多くのアプリの実装
    (無音区間で `IAudioClient::Stop` を呼ぶ)で「再生中でも Inactive」が観測上
    支配的なため、Active のみフィルタでは列挙が空になりやすい。proc-tap は PID
    指定なので、Inactive な PID でも Start 時に音が鳴っていればキャプチャできる。

    Returns:
        list[CaptureSource]: 音声セッションを持つプロセス一覧。0 件もありうる。
    """
    return _get_worker().submit(_enumerate_in_com_thread)


def start_audition(pid: int) -> bool:
    """指定 PID の試聴を開始する。

    永続ワーカが該当セッションのメータを取得し、5fps で peak の poll を始める。
    GUI スレッドは `latest_peak()` を atomic 読みするだけで peak 値が取れる。

    Args:
        pid: 試聴対象プロセス ID。

    Returns:
        True: メータ取得 + poll 開始成功 / False: 該当 Active セッション無し等。
    """
    return _get_worker().start_audition(pid)


def stop_audition() -> None:
    """試聴を停止する(poll 停止 + peak を 0 に)。"""
    _get_worker().stop_audition()


def latest_peak() -> float:
    """ワーカが直近に取得した peak 値を atomic に読む(GUI スレッドから安全)。"""
    return _get_worker().latest_peak()


def is_auditioning() -> bool:
    """現在試聴 poll が動いているか。"""
    return _get_worker().is_auditioning()


# ============================================================
# COM ワーカースレッド内で実行される本体ロジック
# ============================================================
def _enumerate_in_com_thread() -> list[CaptureSource]:
    """COM ワーカースレッド内で実行する列挙本体。

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


# ============================================================
# pycaw / psutil 呼び出しの隔離(テスト時はここを monkeypatch する)
# ============================================================
def _list_active_sessions() -> list[_SessionInfo]:
    """pycaw で AudioSession を列挙し、キャプチャ対象のものだけを `_SessionInfo` で返す。

    採用対象: state が Active(1) または Inactive(0) のセッション(Expired のみ除外)。
    Sndvol(Windows の音量ミキサー)の表示集合と一致する。詳細は `_is_capturable` 参照。

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
        if not _is_capturable(s):
            continue
        name: str | None
        try:
            name = s.Process.name() if s.Process is not None else None
        except Exception:
            name = None
        result.append(_SessionInfo(pid=pid, process_name=name, raw_session=s))
    return result


def _is_capturable(session: Any) -> bool:
    """セッションが proc-tap でキャプチャ対象としてよいか判定する。

    採用条件は **Active(1) または Inactive(0)**。Expired(2) のみ除外。
    詳細は `_CAPTURABLE_STATES` 宣言のコメント参照。

    pycaw のセッションは内部の `_ctl.GetState()` でステートを返す。
    """
    try:
        state = int(session._ctl.GetState())
    except Exception:
        return False
    return state in _CAPTURABLE_STATES


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
