"""AppController: GUI と各レイヤ/Coordinator を仲介する制御層。

役割: GUI のイベント(開始/停止/設定変更)を受け、BackendRegistry から
バックエンドを取り出し、DeviceValidator でチェック、PipelineCoordinator を
組み立てて起動・停止する。Loader スレッドでモデル初期化を非同期化し、
UI をブロックしないようにする。モデルの状態は各 backend が保有し、
AppController は購読 → UI に re-broadcast する(R2-1)。

ロード/開始-停止 分離:
- バックエンド実体は `self._backends` にキャッシュし、毎回の Start で作り直さない。
- `load_models[_async]()` でまとめてロード、`load_model_layer(layer)` で個別ロード(冪等)。
- `start_pipeline[_async]()` はロード済みのバックエンドで Coordinator を組み立てるだけ。
- `stop_pipeline()` は Coordinator を止めるだけ(バックエンドは在駐継続)。
- `set_setting("backends", layer, name)` は該当レイヤのキャッシュを破棄し、新名で再ロードを発火する。

状態管理(Phase A2):
- `_model_status` dict は廃止。状態の真実は backend 側にある(`backend.get_status()`)。
- backend をロードしたら `backend.subscribe(...)` でその後の変化を購読し、UI に re-broadcast する。

UI への通知(refactor-ui-3move P2 で Subscription 1 本に統一):
- すべて `add_<event>_listener(callback) -> Subscription` で購読する
  (status / text_ready / utterance_done / fatal / warn / settings / restart)。
- 旧 `set_callbacks` の single callback 経路は撤去済み。
- listener は emit 元スレッドで呼ばれる。UI 側は `widget.after(0, ...)` で marshalling する。
- 動作中に `set_setting("devices", ...)` が書かれたら自動 restart し、ライフサイクルを
  restart イベントで通知する(backends / languages と同じ「実行条件が変わったら反応する」規則)。

処理時間バッファ(Phase A2):
- レイヤ別に直近 5 件の処理時間(ms)をリングバッファで保持。
- `_handle_utterance_done(record)` から timeline を読んで push。
- `get_recent_durations(layer)` で UI が参照(Phase C の詳細ダイアログ等)。
"""

from __future__ import annotations

import logging
import threading
from collections import deque
from pathlib import Path
from typing import Any, Callable

from voice_translator.capture.backend import AudioCaptureBackend
from voice_translator.output.backend import AudioOutputBackend

from .backend_base import Subscription
from .backend_catalog import BackendCatalog
from .backend_registry import BackendRegistry
from .config_store import ConfigStore
from .credentials_service import CredentialsService
from .device_validator import DeviceValidator
from .error_handler import ErrorHandler
from .ledger import UtteranceLedger
from .logger import TextLogger, TranslationLogger
from .notification_throttle import NotificationThrottle
from .process_time_logger import ProcessTimeLogger
from .pipeline import PipelineCoordinator
from .pipeline_plan import (
    DEFAULT_DECLARATIONS,
    PipelinePlan,
    RoleDeclaration,
    build_pipeline_plan,
    declaration_of,
)
from .sequence import SequenceGenerator
from .stage_dump import NullStageDumpWriter, StageDumpWriter
from .types import (
    AuthState,
    CaptureSource,
    CredentialField,
    ErrorRecord,
    LayerKind,
    LayerStatusLine,
    ModelStatus,
    OutputDevice,
    PipelineRestartEvent,
    VerifyResult,
)

# 直近処理時間リングバッファのサイズ(レイヤごと)。
_RECENT_DURATIONS_MAXLEN = 5

# 状態変化リスナの型(UI 側用、layer + status を受ける)。
UiStatusListener = Callable[[LayerKind, ModelStatus], None]


class AppController:
    """GUI と内部モジュールを橋渡しする制御クラス(B+案: 非同期Loader対応版)。"""

    def __init__(
        self,
        *,
        registry: BackendRegistry,
        config: ConfigStore,
        app_logger: logging.Logger | None = None,
    ) -> None:
        self._registry = registry
        self._config = config
        self._logger = app_logger or logging.getLogger("voice_translator")

        self._coord: PipelineCoordinator | None = None
        self._translation_logger: TranslationLogger | None = None
        self._text_logger: TextLogger | None = None
        self._process_time_logger: ProcessTimeLogger | None = None
        self._ledger: UtteranceLedger | None = None
        self._sequence: SequenceGenerator | None = None
        self._loader_thread: threading.Thread | None = None
        # ステージ間データダンプ(検証用)。pipeline.dump.enabled=true のとき有効。
        # ライフサイクル: start_pipeline で生成・start_run、stop_pipeline で stop_run。
        self._stage_dump: StageDumpWriter | NullStageDumpWriter | None = None

        # バックエンド実体のキャッシュ(レイヤごとに1つ)。
        # load_models() で埋め、backends 設定の変更時は該当レイヤを破棄する(再ロードは
        # 開始 / ↻ ロード / auto_load のタイミング)。
        self._backends: dict[LayerKind, Any] = {}
        # backend ごとの状態変化購読(load 時に subscribe、eviction で unsubscribe)。
        self._backend_subscriptions: dict[LayerKind, Subscription] = {}
        # _backends / 構築管理 / self._coord の短い読み書きを排他する。
        # 原則: このロックを保持したまま重い処理(モデル構築)をしない。UI スレッドも
        # evict のために取るので、長時間保持すると UI が固まる(過去にロード中の
        # バックエンド変更でフリーズした実害あり)。
        self._load_lock = threading.Lock()
        # 同一レイヤの二重構築防止。構築中レイヤは _inflight に入り、完了を
        # _load_cond(_load_lock を共有)で待ち合わせる。待つのは loader スレッド
        # 同士だけ(UI スレッドはロード API を呼ばない)。
        self._load_cond = threading.Condition(self._load_lock)
        self._inflight: set[LayerKind] = set()
        # レイヤ別世代カウンタ。evict のたびに進める。構築完了時に世代が進んでいたら
        # その結果は破棄して最新の設定でロードし直す(last-write-wins)。
        self._layer_generations: dict[LayerKind, int] = {
            layer: 0 for layer in LayerKind
        }

        # UI 側からの multi-listener(R2-6 / P2 で全イベント種に汎用化)。
        # token → (event 名, callback)。解除は Subscription.unsubscribe()。
        self._ui_listeners: dict[int, tuple[str, Callable[..., None]]] = {}
        self._next_listener_token: int = 0
        self._listeners_lock = threading.Lock()

        # レイヤ別 直近処理時間(ms)のリングバッファ。Phase C の詳細ダイアログで使う。
        self._recent_durations: dict[LayerKind, deque[float]] = {
            layer: deque(maxlen=_RECENT_DURATIONS_MAXLEN) for layer in LayerKind
        }

        # P3(refactor-ui-3move): 無状態の 2 切片。
        # メタ問合せは catalog、認証は credentials に実体がある。本クラスの同名
        # メソッドは互換窓(1 行委譲)で、新規コードは直接こちらを使うこと。
        self._catalog = BackendCatalog(registry, self._logger)
        self._credentials_service = CredentialsService(
            registry=registry, config=config, logger=self._logger,
        )

    @property
    def catalog(self) -> BackendCatalog:
        """backend クラスのメタ情報問合せ口(状態なし)。"""
        return self._catalog

    @property
    def credentials(self) -> CredentialsService:
        """認証情報の保管・疎通確認・verified 管理。"""
        return self._credentials_service

    # ============================================================
    # UI イベント購読(R2-6 の Subscription 機構を全イベント種に適用、P2)
    # ============================================================
    # イベント種と callback シグネチャ:
    # - "status":         (layer: LayerKind, status: ModelStatus)
    # - "text_ready":     (record: dict)  … TTS 完了時の前倒し通知(text_only では最終通知)
    # - "utterance_done": (record: dict)  … Output 完了時
    # - "fatal" / "warn": (message, *, exc, stage, seq_id, suppressed)
    # - "settings":       (keys: tuple[str, ...]) … set_setting のキー(値は含めない)
    # - "restart":        (event: PipelineRestartEvent)
    # - "running":        (running: bool) … パイプライン起動完了 / 停止
    # listener は emit 元スレッド(Loader / Coordinator / vt_restart 等)で呼ばれる。
    # UI 側は `widget.after(0, ...)` でメインスレッドへ marshalling すること。
    def add_status_listener(self, callback: UiStatusListener) -> Subscription:
        """UI 側から状態変化を購読する(複数同時 OK)。解除は `Subscription.unsubscribe()`。"""
        return self._add_listener("status", callback)

    def add_text_ready_listener(self, callback: Callable[[dict], None]) -> Subscription:
        """翻訳テキスト確定(TTS 完了 / text_only では最終)の前倒し通知を購読する。"""
        return self._add_listener("text_ready", callback)

    def add_utterance_done_listener(self, callback: Callable[[dict], None]) -> Subscription:
        """発話完了(Output 完了)通知を購読する。record は ledger.pop() の dict。"""
        return self._add_listener("utterance_done", callback)

    def add_fatal_listener(self, callback: Callable[..., None]) -> Subscription:
        """致命的エラー通知を購読する(ErrorHandler 由来。context は kwargs で届く)。"""
        return self._add_listener("fatal", callback)

    def add_warn_listener(self, callback: Callable[..., None]) -> Subscription:
        """警告通知を購読する(ErrorHandler 由来)。"""
        return self._add_listener("warn", callback)

    def add_settings_listener(
        self, callback: Callable[[tuple[str, ...]], None],
    ) -> Subscription:
        """設定変更通知を購読する。`set_setting` のキー tuple が届く(値は含めない)。"""
        return self._add_listener("settings", callback)

    def add_restart_listener(self, callback: Callable[..., None]) -> Subscription:
        """動作中デバイス変更に伴う自動 restart のライフサイクル通知を購読する。"""
        return self._add_listener("restart", callback)

    def add_running_listener(self, callback: Callable[[bool], None]) -> Subscription:
        """パイプラインの起動完了 / 停止を購読する(running: bool が届く)。

        用途: SettingsPanel が動作中にバックエンド行をロックする等、
        「動作中かどうか」で UI を切り替える Panel 間同期(直接参照の代わり)。
        """
        return self._add_listener("running", callback)

    def _add_listener(self, event: str, callback: Callable[..., None]) -> Subscription:
        with self._listeners_lock:
            token = self._next_listener_token
            self._next_listener_token += 1
            self._ui_listeners[token] = (event, callback)
        return Subscription(self, token)

    def _remove_listener(self, token: int) -> None:
        """`Subscription` の解除フック。AppController 直結 listener 用。"""
        with self._listeners_lock:
            self._ui_listeners.pop(token, None)

    def _emit(self, event: str, *args, **kwargs) -> None:
        """指定イベントの listener へ通知する。listener の例外は他を止めない。"""
        with self._listeners_lock:
            callbacks = [
                cb for (ev, cb) in self._ui_listeners.values() if ev == event
            ]
        for cb in callbacks:
            try:
                cb(*args, **kwargs)
            except Exception:  # noqa: BLE001
                self._logger.exception("UI %s listener で例外", event)

    # ErrorHandler への注入口(emit への薄い橋。シグネチャは FatalNotifier 準拠)
    def _emit_fatal(self, message: str, **kwargs) -> None:
        self._emit("fatal", message, **kwargs)

    def _emit_warn(self, message: str, **kwargs) -> None:
        self._emit("warn", message, **kwargs)

    # ============================================================
    # モデルステータス
    # ============================================================
    def get_model_status(self, layer: LayerKind) -> ModelStatus:
        """指定レイヤの現状態を返す。未ロードなら INIT(backend の真実は backend 側)。"""
        backend = self._backends.get(layer)
        if backend is None:
            return ModelStatus.INIT
        try:
            return backend.get_status()
        except Exception:  # noqa: BLE001 - mock や仕様逸脱 backend に対する保険
            return ModelStatus.INIT

    def get_all_model_statuses(self) -> dict[LayerKind, ModelStatus]:
        return {layer: self.get_model_status(layer) for layer in LayerKind}

    def get_recent_durations(self, layer: LayerKind) -> list[float]:
        """指定レイヤの直近処理時間(ms、古い→新しい順、最大 5 件)。"""
        return list(self._recent_durations[layer])

    def get_status_snapshot(
        self,
    ) -> tuple[list[LayerStatusLine], list[tuple[LayerKind, ErrorRecord]]]:
        """全レイヤの状態 + 直近エラーを整形前のデータで返す(Phase C3 改 / P1)。

        文字列への整形は UI 側(`gui/logic/status_summary.py`)の役割。ここは
        - 各レイヤの「backend 名 + 現状態 + (DOWNLOADING 時は DL サイズ目安)」
        - 全 backend の直近エラー(timestamp 新しい順)
        をデータとして集めるだけ。backend が `BackendBase` 由来でない場合(モック等)は
        ベストエフォートで縮退する。
        """
        # 現在の編成での各レイヤの扱い(吸収 / 対象外)を行に載せる。
        # 編成が組めない設定でもステータス表示自体は出せるよう、全 active に縮退。
        try:
            plan = self._current_plan()
            absorbed = plan.absorbed_map
            leads = set(plan.lead_layers)
        except Exception:  # noqa: BLE001
            absorbed, leads = {}, set(LayerKind)

        lines: list[LayerStatusLine] = []
        for layer in LayerKind:
            backend_name = self._config.get("backends", layer.value, default="-")
            status = self.get_model_status(layer)
            tail = self._dl_size_hint(layer) if status == ModelStatus.DOWNLOADING else ""
            if layer in absorbed:
                lead = absorbed[layer]
                disposition = "absorbed"
                absorbed_into = lead.value
                absorbed_backend = str(
                    self._config.get("backends", lead.value, default="") or ""
                )
            elif layer not in leads:
                disposition, absorbed_into, absorbed_backend = "skipped", "", ""
            else:
                disposition, absorbed_into, absorbed_backend = "active", "", ""
            try:
                auth = self.get_auth_state(layer)
            except Exception:  # noqa: BLE001 - 表示用スナップショットは止めない
                auth = AuthState.NOT_REQUIRED
            lines.append(
                LayerStatusLine(
                    layer=layer,
                    backend_name=str(backend_name),
                    status=status,
                    dl_size_hint=tail,
                    disposition=disposition,
                    absorbed_into=absorbed_into,
                    absorbed_backend=absorbed_backend,
                    auth=auth,
                )
            )
        return lines, self._collect_recent_errors()

    def _dl_size_hint(self, layer: LayerKind) -> str:
        """DOWNLOADING 時に表示する「~XGB」ヒント。取得失敗時は空文字。"""
        backend = self._backends.get(layer)
        if backend is None:
            return ""
        backend_name = self._config.get("backends", layer.value, default=None)
        # 選択中モデル名を推定: ASR は model_size、Translator は model_name 等の慣習
        # 暫定: list_recommended_models の先頭を「目安サイズ」として表示
        try:
            models = backend.list_recommended_models()
        except Exception:  # noqa: BLE001
            return ""
        if not models:
            return ""
        # 先頭(=デフォルト想定)モデルの download_size_gb を表示
        size = models[0].download_size_gb
        if size is None:
            return ""
        return f" (~{size:.1f}GB)"

    # ============================================================
    # 互換窓: メタ問合せ(実体は BackendCatalog)/ 認証(実体は CredentialsService)
    # ============================================================
    # P3(refactor-ui-3move): 実装は catalog / credentials に移管済み。以下は既存
    # 呼び出し元(GUI / テスト)互換の 1 行委譲。新規コードは
    # `controller.catalog.…` / `controller.credentials.…` を直接使うこと。
    # 互換窓の削除(参照の全付け替え)は P4(任意)で判断する。
    def get_credential(self, backend: str, key: str) -> str | None:
        """互換窓 → `CredentialsService.get`。"""
        return self._credentials_service.get(backend, key)

    def set_credential(self, backend: str, key: str, value: str) -> None:
        """互換窓 → `CredentialsService.set`(verified を False に戻す)。"""
        self._credentials_service.set(backend, key, value)
        self._emit_credentials_changed(backend)

    def delete_credential(self, backend: str, key: str) -> None:
        """互換窓 → `CredentialsService.delete`。"""
        self._credentials_service.delete(backend, key)
        self._emit_credentials_changed(backend)

    def has_credential(self, backend: str, key: str) -> bool:
        """互換窓 → `CredentialsService.has`。"""
        return self._credentials_service.has(backend, key)

    def is_backend_verified(self, backend_name: str) -> bool:
        """互換窓 → `CredentialsService.is_backend_verified`。"""
        return self._credentials_service.is_backend_verified(backend_name)

    def invalidate_verification(self, backend_name: str) -> None:
        """互換窓 → `CredentialsService.invalidate_verification`。"""
        self._credentials_service.invalidate_verification(backend_name)
        self._emit_credentials_changed(backend_name)

    def _emit_credentials_changed(self, backend_name: str) -> None:
        """認証情報の変化を settings イベントとして UI へ通知する。

        認証状態(AuthState)は ConfigStore / CredentialsStore 由来で status イベントが
        発火しないため、専用キー `("credentials", <backend>)` で再計算を促す
        (SettingsPanel の行ステータス上書き / ControlPanel の ready 再計算)。
        """
        self._emit("settings", ("credentials", backend_name))

    def get_auth_state(self, layer: LayerKind) -> AuthState:
        """選択中 backend の認証準備状態(静的判定)を返す。

        backend 未選択のレイヤは NOT_REQUIRED。実体は
        `CredentialsService.get_auth_state`(インスタンス不要の判定)。
        """
        name = self._config.get("backends", layer.value, default=None)
        if not name:
            return AuthState.NOT_REQUIRED
        return self._credentials_service.get_auth_state(layer, str(name))

    def get_all_auth_states(self) -> dict[LayerKind, AuthState]:
        """全レイヤ分の認証準備状態(ready_state / ステータス表示の入力)。"""
        return {layer: self.get_auth_state(layer) for layer in LayerKind}

    def get_backend_capability_hint(self, layer: LayerKind, name: str):
        """互換窓 → `BackendCatalog.get_capability_hint`。"""
        return self._catalog.get_capability_hint(layer, name)

    def get_capture_kind(self, backend_name: str):
        """互換窓 → `BackendCatalog.get_capture_kind`。"""
        return self._catalog.get_capture_kind(backend_name)

    def get_supported_input_languages(self, backend_name: str) -> list[str]:
        """互換窓 → `BackendCatalog.get_supported_input_languages`。"""
        return self._catalog.get_supported_input_languages(backend_name)

    def supports_auto_detect(self, backend_name: str) -> bool:
        """互換窓 → `BackendCatalog.supports_auto_detect`。"""
        return self._catalog.supports_auto_detect(backend_name)

    def get_supported_target_languages(self, backend_name: str) -> list[str]:
        """互換窓 → `BackendCatalog.get_supported_target_languages`。"""
        return self._catalog.get_supported_target_languages(backend_name)

    def get_supported_output_languages(self, backend_name: str) -> list[str]:
        """互換窓 → `BackendCatalog.get_supported_output_languages`。"""
        return self._catalog.get_supported_output_languages(backend_name)

    # ---- 複合 backend(ロール吸収)関連 ----
    def get_absorbed_roles(self) -> dict[LayerKind, LayerKind]:
        """複合 backend に吸収されているロール → 吸収先レイヤ(現在の設定から)。

        例: ASR に ASR+翻訳の複合が選ばれていれば `{TRANSLATOR: ASR}`。
        吸収されたロールはロード・認証 gate・編成の対象外で、設定された backend は
        Start 時に無視される(UI は「吸収済み」表示を出す)。
        """
        return self._current_plan().absorbed_map

    def get_target_language_provider(self) -> tuple[LayerKind, str]:
        """翻訳先言語の候補を決める backend の (登録レイヤ, backend 名) を返す。

        通常は Translator レイヤの選択 backend。翻訳ロールが複合に吸収されている
        場合は吸収先(ASR 等)の backend(英語固定の複合なら候補も英語のみになる)。
        """
        lead = self.get_absorbed_roles().get(
            LayerKind.TRANSLATOR, LayerKind.TRANSLATOR
        )
        name = str(self._config.get("backends", lead.value, default="") or "")
        return lead, name

    def get_effective_target_languages(self) -> list[str]:
        """翻訳先言語の候補(翻訳ロールを実際に担う backend に問い合わせる)。"""
        layer, name = self.get_target_language_provider()
        return self._catalog.get_supported_target_languages(name, layer=layer)

    def get_credential_spec(self, layer: LayerKind, name: str) -> list[CredentialField]:
        """互換窓 → `BackendCatalog.get_credential_spec`。"""
        return self._catalog.get_credential_spec(layer, name)

    def verify_and_save_credentials(
        self,
        layer: LayerKind,
        backend_name: str,
        values: dict[str, str],
    ) -> VerifyResult:
        """認証の疎通確認 + 保存(実体は `CredentialsService.verify_and_save`)に加え、
        ランタイム側の後処理を行う互換窓。

        認証成功時、該当レイヤで本 backend が選択中かつロード済みなら evict して
        INIT に戻す(古い認証情報で作られたインスタンスを使い続けない)。即時の
        作り直しはしない — 実ロードは Start / ↻ ロード / auto_load に寄せる方針
        (バックエンド変更時の evict-only と同じ規則)。backend キャッシュに触るため、
        この後処理だけはランタイム(本クラス)の責務。
        """
        result = self._credentials_service.verify_and_save(layer, backend_name, values)
        if not result.ok:
            return result

        current_choice = self._config.get("backends", layer.value, default=None)
        if current_choice == backend_name and layer in self._backends:
            self.evict_model_layer(layer)
        self._emit_credentials_changed(backend_name)
        return result

    def _collect_recent_errors(self):
        """全 backend の直近エラーを timestamp 新しい順に並べて返す。

        戻り値: `list[tuple[LayerKind, ErrorRecord]]`。
        """
        items = []
        for layer, backend in self._backends.items():
            try:
                records = backend.get_recent_errors()
            except Exception:  # noqa: BLE001
                continue
            for rec in records:
                items.append((layer, rec))
        # 新しいもの順
        items.sort(key=lambda lr: getattr(lr[1], "timestamp", 0.0), reverse=True)
        return items

    def get_layer_device(self, layer: LayerKind) -> str | None:
        """指定レイヤのバックエンドが報告するデバイス名を返す。

        ASR / Translator のように GPU 対応バックエンドは `device` プロパティを持ち、
        "cpu" / "cuda" / "mps" のいずれかを返す。device 概念を持たないバックエンド
        (Capture / VAD / TTS / Output) や未ロードのレイヤは None。
        """
        backend = self._backends.get(layer)
        if backend is None:
            return None
        device = getattr(backend, "device", None)
        if device is None:
            return None
        try:
            value = str(device).strip()
        except Exception:  # noqa: BLE001
            return None
        return value or None

    def _emit_status(self, layer: LayerKind, status: ModelStatus) -> None:
        """状態変化を UI 側 listener へ伝搬する(P2: multi-listener 一本)。"""
        self._emit("status", layer, status)

    def _on_backend_status_changed(self, layer: LayerKind, status: ModelStatus) -> None:
        """backend.subscribe 由来の通知ハンドラ。UI に re-broadcast する。"""
        self._emit_status(layer, status)

    # ============================================================
    # 列挙
    # ============================================================
    def list_capture_sources(self) -> list[CaptureSource]:
        backend: AudioCaptureBackend = self._create(LayerKind.CAPTURE)
        return backend.list_sources()

    def list_output_devices(self) -> list[OutputDevice]:
        backend: AudioOutputBackend = self._create(LayerKind.OUTPUT)
        return backend.list_devices()

    def list_backends(self, layer: LayerKind) -> list[str]:
        return self._registry.list_names(layer)

    # ============================================================
    # 設定アクセス
    # ============================================================
    def get_setting(self, *keys: str, default: Any = None) -> Any:
        return self._config.get(*keys, default=default)

    def set_setting(self, *keys_and_value: Any) -> None:
        self._config.set(*keys_and_value)
        # バックエンド名が変わったら、該当レイヤのキャッシュを破棄して INIT に戻すだけ。
        # 実ロードは「開始ボタン押下 / ↻ ロード / 起動時 auto_load」の 3 経路に寄せる。
        # 変更即ロードは廃止: 押し間違いでも数 GB のロードが走る・ロード中の再変更で
        # UI スレッドがロック待ちで固まる、の 2 点が実害で、即ロードを要した前提
        # (全レイヤ LOADED でないと開始不可)は「押下時にロード」方式で消滅している。
        # 詳細ダイアログ保存(evict のみ)とも同じ規則になる。
        if len(keys_and_value) == 3 and keys_and_value[0] == "backends":
            try:
                layer_changed = LayerKind(keys_and_value[1])
            except ValueError:
                layer_changed = None
            if layer_changed is not None:
                with self._load_lock:
                    self._evict_backend_locked(layer_changed)
                # 該当レイヤは INIT に戻す(まだロード起動前の状態)
                self._emit_status(layer_changed, ModelStatus.INIT)

        # 言語設定が変わったら、動作中の Coordinator にも反映する(P2)。
        # `is_running` でないときは Coordinator が無いか停止中なので、次回 Start 時に
        # ConfigStore から読まれる(従来通り)。
        if (
            len(keys_and_value) == 3
            and keys_and_value[0] == "languages"
            and self._coord is not None
            and self._coord.is_running
        ):
            key = keys_and_value[1]
            value = str(keys_and_value[2])
            if key == "src":
                self._coord.set_languages(src=value)
            elif key == "tgt":
                self._coord.set_languages(tgt=value)

        # デバイス設定が動作中に変わったら自動 restart(refactor-ui-3move P2)。
        # 「実行条件が変わったら反応する」規則を backends / languages と揃えた。
        # ユーザのプルダウン操作だけでなく、再列挙 fallback による書き込みでも発火する
        # (実デバイスが変わったのに旧デバイスで動き続ける方が事故。契約 §3.11 / §13.6)。
        if (
            len(keys_and_value) == 3
            and keys_and_value[0] == "devices"
            and keys_and_value[1] in ("input", "output")
            and self.is_running
        ):
            self._restart_for_device_change(str(keys_and_value[1]))

        # 設定変更イベント(キーのみ、値は含めない)を UI へ通知する(P2)。
        # ControlPanel が devices.* を購読して ready 状態を再計算する。
        self._emit("settings", tuple(str(k) for k in keys_and_value[:-1]))

    def _restart_for_device_change(self, device_key: str) -> None:
        """動作中デバイス変更の自動 restart を起動し、ライフサイクルを emit する。

        多重 restart は `restart_pipeline_async` 側の防御により failed("既に再開中です")
        として届く。
        """
        self._emit(
            "restart",
            PipelineRestartEvent(phase="started", device_key=device_key),
        )
        self.restart_pipeline_async(
            on_restarted=lambda: self._emit(
                "restart",
                PipelineRestartEvent(phase="completed", device_key=device_key),
            ),
            on_failed=lambda m: self._emit(
                "restart",
                PipelineRestartEvent(
                    phase="failed", device_key=device_key, message=m
                ),
            ),
        )

    def save_settings(self) -> None:
        # 段階 3 / A-7 確定方針: PROCESS kind の capture backend が選ばれているときは
        # `devices.input` を永続化しない。PID はアプリ再起動で別プロセスに振られるので、
        # 残しても次回起動で意味を持たない(誤って別アプリの音を取り込む事故も防ぐ)。
        # 除外は**書き出し用コピー**に対して行い、in-memory の選択は維持する
        # (実メモリを空にしてから保存すると、保存のたびにセッション中のプロセス選択が
        #  消えてしまう)。
        self._config.save(transform=self._strip_volatile_inputs_for_save)

    def load_settings(self) -> None:
        """設定ファイルを読み直し、**実効内容が変わったレイヤだけ** キャッシュを破棄する。

        比較対象はレイヤごとに「選択 backend 名」+「その backend の backends_config」。
        どちらも同じレイヤはロード済みインスタンスと状態表示を維持する(全破棄だと
        再読込のたびに全モデルの再ロードが必要になり、重いローカルモデルで待ち時間が
        無駄になるため)。破棄されたレイヤは INIT に戻り、次の 開始 / ↻ ロードで
        新しい設定のインスタンスが入る。
        """
        before = self._layer_effective_settings()
        self._config.load()
        # A-7 確定方針: 古い config に PROCESS kind の `devices.input` が残っていても、
        # 起動時には空扱いに正規化する(save 側でも除外しているが、手動編集や旧版から
        # 引き継いだ config を想定したセーフティ)。
        self._normalize_volatile_inputs_after_load()
        after = self._layer_effective_settings()
        for layer in LayerKind:
            if before[layer] == after[layer]:
                continue
            with self._load_lock:
                self._evict_backend_locked(layer)
            self._emit_status(layer, ModelStatus.INIT)

    def _layer_effective_settings(self) -> dict[LayerKind, tuple[Any, Any]]:
        """レイヤごとの「ロード結果に効く設定」のスナップショット(差分判定用)。

        (選択 backend 名, その backend の backends_config サブツリー) のペア。
        `_config.load()` は内部 dict を丸ごと置き換えるため、参照のままでも
        load 前後のスナップショットが混ざることはない。
        """
        snapshot: dict[LayerKind, tuple[Any, Any]] = {}
        for layer in LayerKind:
            name = self._config.get("backends", layer.value)
            cfg = (
                self._config.get("backends_config", str(name)) if name else None
            )
            snapshot[layer] = (name, cfg)
        return snapshot

    def _strip_volatile_inputs_for_save(
        self, data: dict[str, Any]
    ) -> dict[str, Any]:
        """save 直前の書き出しコピー変換: PROCESS kind なら `devices.input` を空にする。

        in-memory の設定には触らない(実メモリを空にしてから保存すると、保存のたびに
        セッション中のプロセス選択が消えてしまうため、除外はコピー側だけに行う)。
        """
        if not self._is_process_capture_selected():
            return data
        devices = data.get("devices")
        if isinstance(devices, dict):
            devices["input"] = ""
        return data

    def _normalize_volatile_inputs_after_load(self) -> None:
        """load 直後のフック: PROCESS kind なら `devices.input` を空にする(セーフティ)。

        再起動 / 再読込後の PID は別プロセスを指し得るため、in-memory 側も空に揃える
        (手動編集や旧版から引き継いだ config を想定。読み込み直後なので消える
        セッション状態は無い)。
        """
        if not self._is_process_capture_selected():
            return
        try:
            self._config.set("devices", "input", "")
        except Exception:  # noqa: BLE001 - 失敗しても load を止めない
            pass

    def _is_process_capture_selected(self) -> bool:
        """現在の capture backend が PROCESS kind か(判定不能は False)。"""
        backend_name = str(
            self._config.get("backends", LayerKind.CAPTURE.value, default="")
        )
        if not backend_name:
            return False
        try:
            kind = self.get_capture_kind(backend_name)
        except Exception:  # noqa: BLE001
            return False
        from .types import CaptureKind as _CK
        return kind == _CK.PROCESS

    # ============================================================
    # ロード / 起動 / 停止
    # ============================================================
    @property
    def is_running(self) -> bool:
        return self._coord is not None and self._coord.is_running

    @property
    def is_loading(self) -> bool:
        return self._loader_thread is not None and self._loader_thread.is_alive()

    @property
    def is_loaded(self) -> bool:
        """全レイヤのバックエンドがメモリ常駐済みかを返す。"""
        return all(layer in self._backends for layer in LayerKind)

    # ---- ロード ----
    # TTS backend に「(なし)」が選ばれていることを表す内部値。
    # UI では `(なし)` / 内部では `"none"` を使う(BackendRegistry に同名 backend は
    # 登録されない前提)。
    TTS_NONE = "none"

    @property
    def output_mode(self) -> str:
        """現在の出力モード("audio" / "text_only")。

        判定: `backends.tts` の値が `TTS_NONE`(= "none") / 空文字 / None なら
        `text_only`、それ以外は `audio`。独立した `pipeline.output_mode` キーは持たない。

        text_only モードでは TTS / Output レイヤが「対象外」扱いになる:
        - `load_models` / `load_auto_load_layers_async` の対象から外れる
        - `_check_missing_credentials_gate` のチェック対象から外れる
        - Coordinator は TTS / Output スレッドを起動しない
        """
        tts_choice = self._config.get("backends", "tts", default=None)
        if tts_choice in (None, "", self.TTS_NONE):
            return "text_only"
        return "audio"

    def _current_plan(self) -> PipelinePlan:
        """現在の設定(backends.*)から編成表を組む(backend 未ロードでも可)。

        申告は registry に登録された backend クラスの classmethod から取る。
        `backend_cls` 未登録 / 申告 I/F を持たないクラスはレイヤ既定(単体ロール)
        とみなす。組めない設定は PlanError(start 時の起動拒否と同じ例外)。
        """
        text_only = self.output_mode == "text_only"
        decls: dict[LayerKind, RoleDeclaration] = {}
        for layer in LayerKind:
            if text_only and layer in (LayerKind.TTS, LayerKind.OUTPUT):
                continue
            name = self._config.get("backends", layer.value, default=None)
            cls = (
                self._registry.get_backend_class(layer, str(name)) if name else None
            )
            if cls is not None and callable(getattr(cls, "covers_roles", None)):
                decls[layer] = declaration_of(cls)
            else:
                decls[layer] = DEFAULT_DECLARATIONS[layer]
        return build_pipeline_plan(decls, text_only=text_only)

    def _active_layers(self) -> list[LayerKind]:
        """ロード/起動/認証 gate の対象レイヤ(= 編成表の lead)を返す。

        text_only モードの TTS / Output、複合 backend に吸収されたロールは含まれない
        (どちらも「編成表に載らない」の一例)。
        """
        return list(self._current_plan().lead_layers)

    def load_models(self) -> None:
        """全レイヤのバックエンドを生成しキャッシュする(冪等)。

        既にロード済みのレイヤは触らない。各レイヤごとに `_emit_status` で LOADING →
        (backend 由来の最終状態) を通知するので、GUI 側で進捗を観測できる。
        text_only モードでは TTS / Output レイヤを skip する。
        """
        for layer in self._active_layers():
            self._load_layer(layer)

    def load_model_layer(self, layer: LayerKind) -> None:
        """単一レイヤだけをロードする(冪等)。Phase B 以降の手動ロードボタンの入口。

        既にロード済みなら何もしない。失敗時は例外を伝播し、状態は NOT_DOWNLOADED に戻す。
        """
        self._load_layer(layer)

    def reload_model_layer(self, layer: LayerKind) -> None:
        """単一レイヤを強制的に作り直す(既ロードでも evict してから load)。

        backends_config の値(faster-whisper model_size 等)を変更したあとに、設定を
        反映させるために使う。パイプライン動作中はキャッシュ参照されている backend を
        差し替えないので、停止 → 再開で新インスタンスが入る。
        """
        with self._load_lock:
            self._evict_backend_locked(layer)
        self._emit_status(layer, ModelStatus.INIT)
        self._load_layer(layer)

    def evict_model_layer(self, layer: LayerKind) -> None:
        """単一レイヤの backend を破棄するが、再 load はしない(2026-05-30)。

        用途: LayerSettingsDialog の保存時に backends_config / 認証情報が変わったときに
        呼ぶ。再 load はユーザが ControlPanel の中央「↻ ロード」を押したタイミングで
        `load_models_async` 経由で行われる(冪等 load なので未ロードのレイヤだけ作る)。
        """
        with self._load_lock:
            self._evict_backend_locked(layer)
        self._emit_status(layer, ModelStatus.INIT)

    def _load_layer(self, layer: LayerKind) -> None:
        """単一レイヤのロード実体(ロック保持なしで呼ぶ)。

        原則: **モデル構築(`_create`)はロック外**で行い、ロックは
        `_backends` / `_inflight` / 世代カウンタの短い読み書きに限る。
        ロックを保持したまま構築すると、evict のためにロックを取る UI スレッドが
        構築完了までブロックされ UI が固まる(過去の実害)。

        - 既ロードならスキップ(冪等)。
        - 同一レイヤを別スレッドが構築中なら完了を待ってから再判定(二重構築防止)。
        - 構築中に evict(バックエンド変更 / 設定保存 / 認証更新)で世代が進んだら、
          完成品は捨てて最新の設定でロードし直す(last-write-wins。
          モデル構築は中断できないため完走させてから破棄する)。
        - 失敗時は NOT_DOWNLOADED を emit して例外を伝播。

        進捗ログ: どのレイヤが何秒かかったかが分かるように info ログを出す。
        ロード中に「何も起きていない」ように見えても、実は重いモデルの DL/ロードが
        進んでいるケースを切り分けるため。
        """
        from time import monotonic

        while True:
            with self._load_cond:
                while layer in self._inflight:
                    self._load_cond.wait()
                if layer in self._backends:
                    return
                generation = self._layer_generations[layer]
                self._inflight.add(layer)

            self._emit_status(layer, ModelStatus.LOADING)
            self._logger.info("レイヤ %s のロード開始", layer.value)
            t0 = monotonic()
            try:
                inst = self._create(layer)
            except Exception:
                self._logger.error(
                    "レイヤ %s のロード失敗 (%.1fs 経過)",
                    layer.value, monotonic() - t0,
                )
                with self._load_cond:
                    self._inflight.discard(layer)
                    self._load_cond.notify_all()
                self._emit_status(layer, ModelStatus.NOT_DOWNLOADED)
                raise

            stale = False
            with self._load_cond:
                self._inflight.discard(layer)
                self._load_cond.notify_all()
                if self._layer_generations[layer] != generation:
                    stale = True
                else:
                    self._backends[layer] = inst
                    # backend のその後の状態変化を購読
                    # (DOWNLOADING on reload / MISSING_CREDENTIALS 等)
                    self._subscribe_backend(layer, inst)
            if stale:
                self._logger.info(
                    "レイヤ %s のロード結果を破棄(構築中に設定が変わった)。"
                    "最新の設定でロードし直す", layer.value,
                )
                del inst
                continue
            self._logger.info(
                "レイヤ %s のロード完了 (%.1fs)", layer.value, monotonic() - t0
            )
            # 生成時点での backend 状態(通常 LOADED)を最終通知
            self._emit_status(layer, self._safe_backend_status(inst))
            return

    def _subscribe_backend(self, layer: LayerKind, backend: Any) -> None:
        """backend の状態変化購読を登録する(失敗しても本体は止めない)。"""
        try:
            sub = backend.subscribe(
                lambda s, _layer=layer: self._on_backend_status_changed(_layer, s)
            )
        except Exception:  # noqa: BLE001 - subscribe を持たない仕様逸脱 backend に対する保険
            self._logger.exception("backend.subscribe に失敗 layer=%s", layer.value)
            return
        # Subscription でない値が返るケース(古い backend、テストモック)も握る
        self._backend_subscriptions[layer] = sub

    @staticmethod
    def _safe_backend_status(backend: Any) -> ModelStatus:
        """`get_status()` を呼び、ModelStatus でない値や例外時は LOADED 扱いにする。

        Phase A1 で導入した `BackendBase` を継承していれば必ず `ModelStatus` が返るが、
        既存のテストモックやプロトコル逸脱 backend に対する保険として LOADED で握る
        (load 完了時点では LOADED とみなして UI を進める方が運用上素直)。
        """
        try:
            status = backend.get_status()
        except Exception:  # noqa: BLE001
            return ModelStatus.LOADED
        if isinstance(status, ModelStatus):
            return status
        return ModelStatus.LOADED

    def _evict_backend_locked(self, layer: LayerKind) -> None:
        """`load_lock` 保持中の caller から呼ぶ。subscribe 解除 + キャッシュ削除。

        世代カウンタも進める: このレイヤを構築中のスレッドがいたら、その完成品は
        旧設定産なので破棄される(`_load_layer` の stale 判定)。
        """
        self._layer_generations[layer] += 1
        sub = self._backend_subscriptions.pop(layer, None)
        if sub is not None:
            try:
                sub.unsubscribe()
            except Exception:  # noqa: BLE001
                self._logger.exception("subscription.unsubscribe に失敗 layer=%s", layer.value)
        self._backends.pop(layer, None)

    def load_models_async(
        self,
        *,
        on_done: Callable[[], None] | None = None,
        on_failed: Callable[[str], None] | None = None,
    ) -> None:
        """`load_models()` をバックグラウンドスレッドで呼び出す。

        既にロード/起動中(Loader スレッド稼働中)なら何もしない。
        """
        if self.is_loading:
            return
        on_done = on_done or (lambda: None)
        on_failed = on_failed or (lambda _msg: None)

        def _target() -> None:
            try:
                self.load_models()
            except Exception as exc:  # noqa: BLE001
                self._logger.exception("モデルロードに失敗")
                on_failed(str(exc))
                return
            on_done()

        self._loader_thread = threading.Thread(
            target=_target, name="vt_loader", daemon=True
        )
        self._loader_thread.start()

    # ---- 起動 ----
    def get_auto_load_layers(self) -> list[LayerKind]:
        """選択中 backend に `auto_load=True` が指定されているレイヤを返す(Phase B)。

        ユーザは詳細ダイアログから per-backend で auto_load を ON にする。
        該当する backend が選ばれているレイヤだけが起動時に自動ロードされる。
        text_only モードでは TTS / Output レイヤは候補から除外する(出力モードに
        対象外のレイヤを起動時に読み込まないため)。
        """
        active = set(self._active_layers())
        layers: list[LayerKind] = []
        for layer in LayerKind:
            if layer not in active:
                continue
            backend_name = self._config.get("backends", layer.value)
            if not backend_name:
                continue
            if self._config.get(
                "backends_config", backend_name, "auto_load", default=False
            ):
                layers.append(layer)
        return layers

    def load_auto_load_layers_async(
        self,
        *,
        on_done: Callable[[], None] | None = None,
        on_failed: Callable[[str], None] | None = None,
    ) -> None:
        """`auto_load=True` のレイヤだけを Loader スレッドで順次ロードする(Phase B 起動シーケンス)。

        対象レイヤなしなら即時 on_done。既ロード中なら何もしない。
        """
        if self.is_loading:
            return
        layers = self.get_auto_load_layers()
        on_done = on_done or (lambda: None)
        on_failed = on_failed or (lambda _msg: None)
        if not layers:
            on_done()
            return

        def _target() -> None:
            try:
                for layer in layers:
                    self.load_model_layer(layer)
            except Exception as exc:  # noqa: BLE001
                self._logger.exception("auto-load レイヤのロード失敗")
                on_failed(str(exc))
                return
            on_done()

        self._loader_thread = threading.Thread(
            target=_target, name="vt_auto_loader", daemon=True
        )
        self._loader_thread.start()

    def _check_missing_credentials_gate(self) -> None:
        """認証が必要なレイヤで未完了の項目があれば FatalError で start をブロック。

        Phase B では空骨子だったが、Phase E-2 で `requires_credentials=True` の backend に
        対する以下のチェックを足した:
        1. `ModelStatus.MISSING_CREDENTIALS` を backend 側が立てている → blocked
        2. backend が要求する `credential_spec` のキーが `CredentialsStore` に保存されていない
           → blocked
        3. 保存されているが `is_backend_verified=False`(認証未完了 or 失効) → blocked

        ユーザは詳細ダイアログから「認証」ボタン → CredentialDialog で疎通確認 → 保存、で
        gate を通過できる。
        """
        problems: list[str] = []
        # text_only モードでは TTS / Output レイヤは起動対象外なので、認証 gate でも
        # 評価しない(クラウド TTS の認証が無くてもテキスト出力モードでは Start できる)。
        active = set(self._active_layers())
        for layer in LayerKind:
            if layer not in active:
                continue
            # 1) backend 側が明示的に MISSING_CREDENTIALS を立てている場合
            status = self.get_model_status(layer)
            if status == ModelStatus.MISSING_CREDENTIALS:
                self._logger.info(
                    "gate: %s = MISSING_CREDENTIALS でブロック", layer.value
                )
                problems.append(f"{layer.value} (認証情報未設定)")
                continue

            backend_name = self._config.get("backends", layer.value, default=None)
            if not backend_name:
                continue
            hint = self.get_backend_capability_hint(layer, backend_name)
            if hint is None or not hint.requires_credentials:
                continue

            # 2) スペックのキーが揃っているか
            spec = self.get_credential_spec(layer, backend_name)
            missing_keys = [
                f.label for f in spec if not self.has_credential(backend_name, f.key_name)
            ]
            if missing_keys:
                self._logger.info(
                    "gate: %s (%s) 認証情報未入力でブロック: %s",
                    layer.value, backend_name, missing_keys,
                )
                problems.append(
                    f"{layer.value} ({backend_name}: 認証情報未入力 — {', '.join(missing_keys)})"
                )
                continue

            # 3) 検証(verified)を通過しているか
            if not self.is_backend_verified(backend_name):
                self._logger.info(
                    "gate: %s (%s) 未検証でブロック", layer.value, backend_name
                )
                problems.append(f"{layer.value} ({backend_name}: 未検証)")

        if not problems:
            self._logger.info("gate: 全レイヤ PASS")
            return
        from .errors import FatalError
        self._logger.warning("gate: ブロック problems=%s", problems)
        raise FatalError(
            "認証が完了していないレイヤがあります: "
            + " / ".join(problems)
            + "。詳細ダイアログから「認証」ボタンを開いて、API キーを入力 → 「テスト」を通してください。"
        )

    def start_pipeline_async(
        self,
        *,
        on_started: Callable[[], None] | None = None,
        on_failed: Callable[[str], None] | None = None,
    ) -> None:
        """Loader スレッドでロード(必要なら)+ パイプラインを起動する(非同期)。

        Phase B: 開始ボタンは常時押下可。押された時点で未ロードの backend があれば
        Loader スレッドでロード → Coordinator 起動。
        - 既に動作中 / ロード中なら何もしない。
        - DeviceValidator は呼び出し元スレッドで先にチェックする(即時に失敗を返したい)。
        - MISSING_CREDENTIALS のレイヤがあれば即時失敗(ロードしても意味がない)。
        """
        if self.is_running or self.is_loading:
            self._logger.info(
                "start_pipeline_async: 既に起動中(is_running=%s, is_loading=%s)のため何もしない",
                self.is_running, self.is_loading,
            )
            return

        # 同期で先に検証(呼び出し側で即時例外を受け取れる)
        self._logger.info("start_pipeline_async: 同期検証(デバイス + 認証 gate)")
        input_id = self._config.get("devices", "input")
        output_id = self._config.get("devices", "output")
        try:
            DeviceValidator.validate(input_id, output_id)
        except Exception:
            self._logger.exception(
                "start_pipeline_async: DeviceValidator が失敗 input=%s output=%s",
                input_id, output_id,
            )
            raise
        try:
            self._check_missing_credentials_gate()
        except Exception:
            self._logger.exception("start_pipeline_async: 認証 gate が失敗")
            raise

        on_started = on_started or (lambda: None)
        on_failed = on_failed or (lambda _msg: None)

        def _loader_target() -> None:
            self._logger.info("start_pipeline_async: ロード/起動シーケンス開始")
            try:
                self.load_models()  # Phase B: 実質ロードが走る局面が増える
                self._logger.info("start_pipeline_async: 全レイヤロード完了、coordinator 起動")
                self._start_coord(input_id, output_id)
            except Exception as exc:  # noqa: BLE001
                self._logger.exception("Loader 失敗")
                on_failed(str(exc))
                return
            self._logger.info("start_pipeline_async: coordinator 起動完了、動作中")
            on_started()

        self._loader_thread = threading.Thread(
            target=_loader_target, name="vt_loader", daemon=True
        )
        self._loader_thread.start()

    def start_pipeline(self) -> None:
        """同期版: テスト・スクリプト用。GUI からは start_pipeline_async を使う。"""
        if self.is_running:
            return
        input_id = self._config.get("devices", "input")
        output_id = self._config.get("devices", "output")
        DeviceValidator.validate(input_id, output_id)
        self._check_missing_credentials_gate()
        self.load_models()
        self._start_coord(input_id, output_id)

    def _start_coord(self, input_id: str, output_id: str) -> None:
        """ロード済みバックエンドから Coordinator を組み立てて開始する。

        ロガー / ErrorHandler / Ledger / Sequence は毎回(設定が反映される)生成する。
        """
        with self._load_lock:
            # 翻訳jsonl + 個別テキストログ(設定変更を反映させるため毎回再生成)
            log_dir = Path(self._config.get("log", "directory", default="./logs"))
            jsonl_enabled = bool(self._config.get("log", "jsonl_enabled", default=True))
            src_text_enabled = bool(
                self._config.get("log", "src_text_enabled", default=False)
            )
            tgt_text_enabled = bool(
                self._config.get("log", "tgt_text_enabled", default=False)
            )
            self._translation_logger = TranslationLogger(
                log_dir / "translations.jsonl", enabled=jsonl_enabled
            )
            self._text_logger = TextLogger(
                src_path=log_dir / "soundsrc.txt",
                tgt_path=log_dir / "translated.txt",
                src_enabled=src_text_enabled,
                tgt_enabled=tgt_text_enabled,
            )
            process_time_enabled = bool(
                self._config.get("log", "process_time_enabled", default=False)
            )
            self._process_time_logger = ProcessTimeLogger(
                log_dir / "processtime.csv", enabled=process_time_enabled
            )

            throttle_sec = float(
                self._config.get("notifications", "throttle_sec", default=5.0)
            )
            throttle = NotificationThrottle(window_sec=throttle_sec)
            error_handler = ErrorHandler(
                logger=self._logger,
                on_fatal=self._emit_fatal,
                on_warn=self._emit_warn,
                throttle=throttle,
            )

            src_lang = self._config.get("languages", "src", default="auto")
            tgt_lang = self._config.get("languages", "tgt", default="ja")

            self._ledger = UtteranceLedger()
            self._sequence = SequenceGenerator()

            # パイプラインのバッファ容量(config.yaml の pipeline セクションで上書き可)。
            captured_max_bytes = int(
                self._config.get(
                    "pipeline", "captured_queue_max_bytes", default=10_000_000
                )
            )
            synthesized_max_bytes = int(
                self._config.get(
                    "pipeline", "synthesized_queue_max_bytes", default=5_000_000
                )
            )
            recognized_size = int(
                self._config.get("pipeline", "recognized_queue_size", default=10)
            )
            translated_size = int(
                self._config.get("pipeline", "translated_queue_size", default=10)
            )
            max_retries = int(
                self._config.get("pipeline", "max_retries", default=3)
            )
            retry_base_sec = float(
                self._config.get("pipeline", "retry_base_sec", default=0.5)
            )
            retry_max_sec = float(
                self._config.get("pipeline", "retry_max_sec", default=8.0)
            )

            # ステージ間ダンプ(検証用)。enabled=false なら NullStageDumpWriter を注入。
            self._stage_dump = self._build_stage_dump()
            self._stage_dump.start_run(self._build_dump_meta(input_id, output_id))

            # text_only モードでは TTS / Output は使わない(未ロードでも OK)。
            mode = self.output_mode
            tts_backend = (
                self._backends[LayerKind.TTS] if mode == "audio" else None
            )
            output_backend = (
                self._backends[LayerKind.OUTPUT] if mode == "audio" else None
            )

            self._coord = PipelineCoordinator(
                capture=self._backends[LayerKind.CAPTURE],
                vad=self._backends[LayerKind.VAD],
                asr=self._backends[LayerKind.ASR],
                # 複合 backend に吸収されたレイヤはロードされない(.get → None)。
                # 編成の整合は Coordinator 構築時の plan 検証が保証する。
                translator=self._backends.get(LayerKind.TRANSLATOR),
                tts=tts_backend,
                output=output_backend,
                error_handler=error_handler,
                ledger=self._ledger,
                sequence=self._sequence,
                text_logger=self._text_logger,
                src_lang=src_lang,
                tgt_lang=tgt_lang,
                on_utterance_done=self._handle_utterance_done,
                on_text_ready=self._handle_text_ready,
                on_dropped=self._handle_dropped,
                captured_queue_max_bytes=captured_max_bytes,
                synthesized_queue_max_bytes=synthesized_max_bytes,
                recognized_queue_size=recognized_size,
                translated_queue_size=translated_size,
                max_retries=max_retries,
                retry_base_sec=retry_base_sec,
                retry_max_sec=retry_max_sec,
                dump=self._stage_dump,
                output_mode=mode,
            )
        self._coord.start(
            capture_source_id=input_id, output_device_id=output_id
        )
        self._emit("running", True)

    def stop_pipeline(self) -> None:
        """Coordinator を停止する。バックエンド実体は常駐させたまま残す。"""
        was_running = self._coord is not None
        if self._coord is not None:
            self._coord.stop()
            self._coord = None
        if self._stage_dump is not None:
            try:
                self._stage_dump.stop_run()
            except Exception:  # noqa: BLE001 - ダンプ停止失敗で本体は止めない
                self._logger.exception("StageDumpWriter.stop_run に失敗")
            self._stage_dump = None
        if was_running:
            self._emit("running", False)

    def test_output_playback(self, text: str = "テスト音声") -> None:
        """選択中の TTS / Output backend / 出力デバイスで `text` を 1 回だけ再生する。

        ControlPanel の「🔊 出力テスト」ボタンから呼ばれる。「翻訳まで出ているのに
        音が鳴らない」の切り分けを GUI 上で完結させるのが目的:
        - 鳴れば: TTS → Output → スピーカ の経路は健全(本体側の hand-off 等を疑う)
        - 鳴らなければ: 出力デバイス選択 / Output backend / TTS いずれかが原因

        制約:
        - パイプライン動作中は呼べない(本体が Output を掴んでいて競合する)
        - text_only モード(TTS=「(なし)」)では呼べない(合成する手段がない)
        - `devices.output` が空でも呼べない(再生先不明)

        副作用: TTS / Output backend が未ロードなら、その場でロードしてキャッシュに乗せる
        (本体の Start 時に再ロードしなくて済む。テスト再生のためだけにロード状態を変えない
        方が望ましいが、未ロードでテストできない方が UX 上の損が大きい)。
        """
        if self.is_running:
            raise RuntimeError(
                "パイプライン動作中はテスト再生できません(本体を停止してから実行してください)"
            )
        if self.output_mode == "text_only":
            raise RuntimeError(
                "TTS=「(なし)」のためテスト再生できません(TTS を選択してください)"
            )

        output_id = str(self._config.get("devices", "output", default="") or "")
        if not output_id.strip():
            raise RuntimeError("出力デバイスが未選択です(設定パネルから選択してください)")

        # TTS / Output backend を必要に応じてロード(冪等)
        self.load_model_layer(LayerKind.TTS)
        self.load_model_layer(LayerKind.OUTPUT)

        tts = self._backends[LayerKind.TTS]
        output = self._backends[LayerKind.OUTPUT]

        tgt_lang = str(self._config.get("languages", "tgt", default="ja") or "ja")
        # 1) 合成
        pcm, samplerate = tts.synthesize(text, tgt_lang)
        if pcm is None or getattr(pcm, "size", 0) == 0:
            raise RuntimeError(
                f"TTS が空の PCM を返しました(text={text!r}, lang={tgt_lang})"
            )
        # 2) 再生(start → play → stop)。stop はエラー時も呼ぶ。
        output.start(output_id)
        try:
            output.play(pcm, samplerate)
        finally:
            try:
                output.stop()
            except Exception:  # noqa: BLE001
                self._logger.exception("test_output_playback: output.stop で例外(無視)")

    def restart_pipeline_async(
        self,
        *,
        on_restarted: Callable[[], None] | None = None,
        on_failed: Callable[[str], None] | None = None,
    ) -> None:
        """動作中の Coordinator を停止 → 同じ ConfigStore 値で再開する(P4: デバイス変更用)。

        - 動作中でない(`is_running == False`)場合は no-op で `on_restarted` を即時呼ぶ。
          ConfigStore には既に新値が書き戻されている前提で、次回手動 Start で反映される。
        - 動作中の場合は `vt_restart` スレッドを立て、その中で `stop_pipeline()` →
          `start_pipeline()`(同期版)を直列に実行する。Loader スレッドのネストを避けるため、
          `start_pipeline_async` ではなく同期版を使う。
        - 多重起動防御: 既に restart が走っている場合は `on_failed("既に再開中です")` を呼ぶ。
        - callback は **呼び出しスレッド(=`vt_restart`)上で呼ばれる**。tkinter 等の UI に
          反映する場合は呼び出し側で `widget.after(0, ...)` でメインスレッドへ marshalling すること。
        """
        on_restarted = on_restarted or (lambda: None)
        on_failed = on_failed or (lambda _msg: None)

        if not self.is_running:
            on_restarted()
            return

        # 多重起動防御: 同名スレッドが alive ならスキップ
        existing = getattr(self, "_restart_thread", None)
        if existing is not None and existing.is_alive():
            on_failed("既に再開中です")
            return

        def _target() -> None:
            try:
                self.stop_pipeline()
            except Exception as e:  # noqa: BLE001
                self._logger.exception("restart_pipeline_async: 停止で失敗")
                on_failed(f"停止に失敗: {e}")
                return
            try:
                # 同期版 start を呼ぶ(Loader スレッドのネストを避ける)
                self.start_pipeline()
            except Exception as e:  # noqa: BLE001
                self._logger.exception("restart_pipeline_async: 再開で失敗")
                on_failed(f"再開に失敗: {e}")
                return
            on_restarted()

        self._restart_thread = threading.Thread(
            target=_target, name="vt_restart", daemon=True,
        )
        self._restart_thread.start()

    def _build_stage_dump(self) -> StageDumpWriter | NullStageDumpWriter:
        """ConfigStore の `pipeline.dump.*` に基づき writer を生成する。

        enabled=false / 不正な設定の場合は NullStageDumpWriter を返す。
        """
        enabled = bool(self._config.get("pipeline", "dump", "enabled", default=False))
        if not enabled:
            return NullStageDumpWriter()
        directory = self._config.get("pipeline", "dump", "directory", default="./logs/dumps")
        stages = self._config.get(
            "pipeline", "dump", "stages", default=["vad", "asr", "translate", "tts"]
        )
        max_runs = int(self._config.get("pipeline", "dump", "max_runs", default=20))
        return StageDumpWriter(
            dump_dir=Path(directory),
            stages=stages if isinstance(stages, (list, tuple, set)) else (),
            max_runs=max_runs,
            logger=self._logger,
        )

    def _build_dump_meta(self, input_id: str, output_id: str) -> dict[str, Any]:
        """run.json に乗せるメタ情報。後から「どの設定で取れたダンプか」を特定する用。"""
        backends_snapshot = self._config.get("backends", default={}) or {}
        backends_config = self._config.get("backends_config", default={}) or {}
        languages = self._config.get("languages", default={}) or {}
        return {
            "backends": dict(backends_snapshot),
            "backends_config": dict(backends_config),
            "languages": dict(languages),
            "devices": {"input": input_id, "output": output_id},
        }

    # ---- 内部 ----
    def _create(self, layer: LayerKind):
        name = self._config.get("backends", layer.value)
        if not name:
            raise KeyError(f"backends.{layer.value} が設定されていません")
        return self._registry.create(layer, name)

    def _handle_text_ready(self, record: dict) -> None:
        """TTS 完了時に Coordinator から呼ばれる(UI 履歴の前倒し通知)。

        audio モード: ledger スナップショットを UI に流すだけ。レイテンシ計算や CSV
        ロガーへの記録は `_handle_utterance_done`(Output 完了時)で行う。

        text_only モード: ここが最終通知(Output が無いため `on_utterance_done` は
        呼ばれない)。jsonl / processtime / レイヤ別 処理時間バッファへの記録もこの
        メソッドで兼ねる。record は ledger.pop() の戻り値そのままで、timeline には
        t_translate までが含まれる(t_tts / t_playback は無い)。
        """
        if self.output_mode == "text_only":
            # 最終扱い: 通常の done パスと同じ後処理を回す(UI 通知は最後)
            self._push_recent_durations(record)
            try:
                if self._translation_logger is not None:
                    self._translation_logger.write_record(record)
            except Exception:  # noqa: BLE001
                self._logger.exception("翻訳ログ(jsonl)書き出しに失敗(text_only)")
            try:
                if self._process_time_logger is not None:
                    self._process_time_logger.write_record(record)
            except Exception:  # noqa: BLE001
                self._logger.exception("処理時間ログ(csv)書き出しに失敗(text_only)")
        self._emit("text_ready", record)

    def _handle_utterance_done(self, record: dict) -> None:
        """Output 完了時に Coordinator から呼ばれる。

        record は UtteranceLedger.pop() の戻り値 dict。
        seq_id / timeline / src_text / src_lang / tgt_text / tgt_lang などを含む。
        """
        # レイヤ別 処理時間リングバッファに push(GUI の詳細ダイアログ用、Phase C)
        self._push_recent_durations(record)
        try:
            if self._translation_logger is not None:
                self._translation_logger.write_record(record)
        except Exception:  # noqa: BLE001
            self._logger.exception("翻訳ログ(jsonl)書き出しに失敗")
        try:
            if self._process_time_logger is not None:
                self._process_time_logger.write_record(record)
        except Exception:  # noqa: BLE001
            self._logger.exception("処理時間ログ(csv)書き出しに失敗")
        self._emit("utterance_done", record)

    def _push_recent_durations(self, record: dict) -> None:
        """`timeline` から各レイヤの処理時間(ms)を取り出してリングバッファに積む。

        欠損したマーカー(失敗等)は当該レイヤだけスキップ。CAPTURE は明確な開始時刻が
        ないため VAD と一体扱いとし、`t_capture → t_vad_end` を VAD レイヤに記録する。
        """
        tl = record.get("timeline", {}) or {}

        def _push(layer: LayerKind, start_key: str, end_key: str) -> None:
            start = tl.get(start_key)
            end = tl.get(end_key)
            if start is None or end is None:
                return
            ms = (end - start) * 1000.0
            self._recent_durations[layer].append(ms)

        _push(LayerKind.VAD, "t_capture", "t_vad_end")
        _push(LayerKind.ASR, "t_asr_start", "t_asr")
        _push(LayerKind.TRANSLATOR, "t_translate_start", "t_translate")
        _push(LayerKind.TTS, "t_tts_start", "t_tts")
        _push(LayerKind.OUTPUT, "t_playback_start", "t_playback")

    def _handle_dropped(self, seq_ids: list[int], stage_name: str) -> None:
        """キューあふれで捨てられた発話の seq_id 通知。

        テキストログは各段で既に書かれているので、ここでは UI 通知やログのみ。
        現状はログだけ。
        """
        if not seq_ids:
            return
        self._logger.info(
            "dropped seq_ids=%s at %s", seq_ids, stage_name
        )
