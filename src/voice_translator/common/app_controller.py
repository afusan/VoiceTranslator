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
- UI 側の購読は `add_status_listener(callback) -> Subscription`(multi-listener、R2-6)。
- 旧 `on_status_change` callback は `set_callbacks` 経由で互換維持。

処理時間バッファ(Phase A2):
- レイヤ別に直近 5 件の処理時間(ms)をリングバッファで保持。
- `_handle_utterance_done(record)` から timeline を読んで push。
- `get_recent_durations(layer)` で UI が参照(Phase C の詳細ダイアログ等)。

コールバックシグネチャ:
- on_utterance_done(record: dict)  : UtteranceLedger.pop() の戻り値 dict を受ける
- on_dropped(seq_ids: list[int], stage: str): 捨てられた発話の seq_id を受ける
TextLogger / TranslationLogger も新 I/F (write_src/write_tgt/write_record) に追従。
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
from .backend_registry import BackendRegistry
from .config_store import ConfigStore
from .credentials import CredentialsStore
from .device_validator import DeviceValidator
from .error_handler import ErrorHandler
from .ledger import UtteranceLedger
from .logger import TextLogger, TranslationLogger
from .notification_throttle import NotificationThrottle
from .process_time_logger import ProcessTimeLogger
from .pipeline import PipelineCoordinator
from .sequence import SequenceGenerator
from .stage_dump import NullStageDumpWriter, StageDumpWriter
from .types import (
    CaptureSource,
    CredentialField,
    LayerKind,
    ModelStatus,
    OutputDevice,
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
        # load_models() で埋め、backends 設定の変更時に該当レイヤを破棄して再ロードする。
        self._backends: dict[LayerKind, Any] = {}
        # backend ごとの状態変化購読(load 時に subscribe、eviction で unsubscribe)。
        self._backend_subscriptions: dict[LayerKind, Subscription] = {}
        # _backends と self._coord の生成・破棄を排他的に行う(ロード/起動の競合防止)
        self._load_lock = threading.Lock()

        # UI コールバック(既定は no-op)
        # record は UtteranceLedger.pop() の戻り値 dict(seq_id, timeline, src/tgt 各種)。
        # on_fatal/on_warn は (message, *, exc, stage, seq_id) を受ける。
        # GUI 側で stage / seq_id を使わなければ **kwargs で吸収可。
        self._on_utterance_done: Callable[[dict], None] = lambda r: None
        self._on_fatal: Callable[..., None] = lambda m, **_kw: None
        self._on_warn: Callable[..., None] = lambda m, **_kw: None
        # 旧 single callback(後方互換)。新規コードは add_status_listener() を使う。
        self._on_status_change: UiStatusListener = lambda l, s: None

        # UI 側からの multi-listener(R2-6)。トークン辞書 + ロック。
        self._ui_status_listeners: dict[int, UiStatusListener] = {}
        self._next_listener_token: int = 0
        self._listeners_lock = threading.Lock()

        # レイヤ別 直近処理時間(ms)のリングバッファ。Phase C の詳細ダイアログで使う。
        self._recent_durations: dict[LayerKind, deque[float]] = {
            layer: deque(maxlen=_RECENT_DURATIONS_MAXLEN) for layer in LayerKind
        }

        # Phase D: クラウド backend 認証情報の保管。初回利用時に遅延初期化する
        # (テスト時の `keyring.set_keyring(InMemoryKeyring())` のタイミングを尊重)。
        self._credentials: CredentialsStore | None = None

    # ============================================================
    # コールバック登録
    # ============================================================
    def set_callbacks(
        self,
        *,
        on_utterance_done: Callable[[dict], None] | None = None,
        on_fatal: Callable[..., None] | None = None,
        on_warn: Callable[..., None] | None = None,
        on_status_change: UiStatusListener | None = None,
    ) -> None:
        if on_utterance_done is not None:
            self._on_utterance_done = on_utterance_done
        if on_fatal is not None:
            self._on_fatal = on_fatal
        if on_warn is not None:
            self._on_warn = on_warn
        if on_status_change is not None:
            self._on_status_change = on_status_change

    # ---- UI 側の multi-listener(R2-6 / Phase A2)----
    def add_status_listener(self, callback: UiStatusListener) -> Subscription:
        """UI 側から状態変化を購読する(複数同時 OK)。解除は `Subscription.unsubscribe()`。"""
        with self._listeners_lock:
            token = self._next_listener_token
            self._next_listener_token += 1
            self._ui_status_listeners[token] = callback
        return Subscription(self, token)

    def _remove_listener(self, token: int) -> None:
        """`Subscription` の解除フック。AppController 直結 listener 用。"""
        with self._listeners_lock:
            self._ui_status_listeners.pop(token, None)

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

    def get_status_summary(self) -> str:
        """全レイヤの状態 + 直近エラーを 1 つのテキストにまとめる(Phase C3)。

        ステータステキストボックスに 1 ブロックで貼れる形を返す。
        - 各レイヤの「現状態 + (DOWNLOADING 時は DL サイズ目安)」を 1 行ずつ
        - 末尾に「最近のエラー」を新しい順で集約(最大 5 件)

        backend が `BackendBase` 由来でない場合(モック等)はベストエフォートで縮退する。
        """
        lines: list[str] = []
        for layer in LayerKind:
            backend_name = self._config.get("backends", layer.value, default="-")
            status = self.get_model_status(layer)
            tail = self._dl_size_hint(layer) if status == ModelStatus.DOWNLOADING else ""
            lines.append(
                f"[{layer.value}] {backend_name}: {status.value}{tail}"
            )

        errors = self._collect_recent_errors()
        if errors:
            lines.append("")
            lines.append("最近のエラー:")
            # 新しい順に最大 5 件
            for layer, rec in errors[:5]:
                ctx = f" ({rec.context})" if rec.context else ""
                lines.append(
                    f"  [{layer.value}] {rec.exc_type}: {rec.message}{ctx}"
                )
        return "\n".join(lines)

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
    # 認証情報(Phase D)
    # ============================================================
    def _credentials_store(self) -> CredentialsStore:
        """`CredentialsStore` を遅延初期化して返す。

        ConfigStore の `credentials.use_local_file` フラグを反映。テスト時に
        `keyring.set_keyring(InMemoryKeyring())` した直後の状態を捕捉できるよう、
        ロード/初回呼び出しのタイミングで生成する。
        """
        if self._credentials is None:
            use_local = bool(
                self._config.get("credentials", "use_local_file", default=False)
            )
            self._credentials = CredentialsStore(use_local_file=use_local)
        return self._credentials

    def get_credential(self, backend: str, key: str) -> str | None:
        """指定 backend / key の認証情報を返す。未設定なら None。"""
        return self._credentials_store().get(backend, key)

    def set_credential(self, backend: str, key: str, value: str) -> None:
        """指定 backend / key に認証情報を保存する。空文字は delete と同義。

        Phase E-2: キーが変わったら `verified` フラグを自動で False に戻す
        (再認証必須にして、古い verified 状態を引きずらない)。
        """
        self._credentials_store().set(backend, key, value)
        self._config.set("credentials", "verified", backend, False)

    def delete_credential(self, backend: str, key: str) -> None:
        """指定 backend / key の認証情報を削除する。"""
        self._credentials_store().delete(backend, key)

    def has_credential(self, backend: str, key: str) -> bool:
        """指定 backend / key の認証情報が設定済みか。"""
        return self._credentials_store().get(backend, key) is not None

    def get_backend_capability_hint(self, layer: LayerKind, name: str):
        """登録時に指定された capability ヒントを返す(Phase D)。

        backend 未生成でも `is_cloud` / `requires_credentials` 等を引ける。
        ヒントが無ければ None(GUI は「不明だが既定 OFF 扱い」で動かす想定)。
        """
        return self._registry.get_capability_hint(layer, name)

    # ---- Phase E-2: 認証フロー(spec / verify / verified 管理) ----
    def get_credential_spec(self, layer: LayerKind, name: str) -> list[CredentialField]:
        """指定 backend の認証情報スペック。

        backend クラスが登録されていればその `credential_spec()` を呼ぶ。
        登録なし or 例外時は空リスト(GUI は「認証情報なし」として扱う)。
        """
        cls = self._registry.get_backend_class(layer, name)
        if cls is None:
            return []
        try:
            spec = cls.credential_spec()
        except Exception:  # noqa: BLE001
            self._logger.exception("credential_spec の呼び出し失敗 backend=%s", name)
            return []
        return list(spec)

    def is_backend_verified(self, backend_name: str) -> bool:
        """指定 backend が認証済みかを返す(ConfigStore で永続化)。

        `set_credential` 後に `verify_and_save_credentials` が成功すると True になる。
        キー再入力 / `invalidate_verification` で False に戻る。
        """
        return bool(
            self._config.get("credentials", "verified", backend_name, default=False)
        )

    def verify_and_save_credentials(
        self,
        layer: LayerKind,
        backend_name: str,
        values: dict[str, str],
    ) -> VerifyResult:
        """backend の `verify_credentials` を呼び、成功なら認証情報を保存する。

        1. backend クラスの `verify_credentials(values)` を呼ぶ
        2. 成功 → 各キーを `CredentialsStore` に保存、`credentials.verified.<backend>=True`
        3. 失敗 → 何も保存せず `VerifyResult` を返す(message を UI に表示)
        """
        cls = self._registry.get_backend_class(layer, backend_name)
        if cls is None:
            return VerifyResult(
                ok=False,
                message=f"backend クラス未登録: layer={layer.value}, name={backend_name}",
            )
        try:
            result = cls.verify_credentials(values)
        except Exception as e:  # noqa: BLE001
            self._logger.exception(
                "verify_credentials で例外 backend=%s", backend_name
            )
            return VerifyResult(ok=False, message=f"検証中に例外: {e}")

        if not result.ok:
            return result

        # 保存。`set_credential` は内部で verified=False に戻すので、後で True を立て直す。
        for key_name, value in values.items():
            if value == "":
                # 空欄(=未編集)はスキップ。既存値を消さない
                continue
            self.set_credential(backend_name, key_name, value)
        self._config.set("credentials", "verified", backend_name, True)
        return result

    def invalidate_verification(self, backend_name: str) -> None:
        """サブスク切れ / API 401 等を観測したとき呼ぶ。`verified=False` に戻す。

        backend 実装の例外ハンドラ から呼ばれて、次回 Start を gate する仕組み。
        起こり得るケース:
        - 初回認証時は OK だが、課金 / サブスクが切れた → 401/402 で停止 → invalidate
        - API key がローテーションされた → 401 で停止 → invalidate
        """
        self._config.set("credentials", "verified", backend_name, False)

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
        """状態変化を UI 側 listener へ伝搬する。

        旧 single callback と新 multi-listener の両方に届ける。listener の例外は
        他の listener / 本体を止めない(ログだけ残す)。
        """
        # 旧 single callback(後方互換)
        try:
            self._on_status_change(layer, status)
        except Exception:  # noqa: BLE001
            self._logger.exception("on_status_change callback で例外")
        # 新 multi-listener
        with self._listeners_lock:
            listeners = list(self._ui_status_listeners.values())
        for cb in listeners:
            try:
                cb(layer, status)
            except Exception:  # noqa: BLE001
                self._logger.exception("UI status listener で例外")

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
        # バックエンド名が変わったら、該当レイヤのキャッシュを破棄してステータスを更新する。
        # (実体ロードはバックグラウンドで自動的に走らせる: ユーザは設定を変えたら
        # すぐに「準備中→完了」が見える方が自然)
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
                # バックグラウンドで即座にロードを試みる(GUI 側で進捗が見える)
                threading.Thread(
                    target=lambda: self._safe_load_layer(layer_changed),
                    daemon=True,
                    name=f"vt_reload_{layer_changed.value}",
                ).start()

    def save_settings(self) -> None:
        self._config.save()

    def load_settings(self) -> None:
        self._config.load()
        # 設定再読込ではバックエンド名が変わっている可能性があるので、キャッシュを全破棄して
        # INIT に戻す。GUI 側で自動ロードを再度発火する想定。
        with self._load_lock:
            for layer in list(self._backends.keys()):
                self._evict_backend_locked(layer)
        for layer in LayerKind:
            self._emit_status(layer, ModelStatus.INIT)

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
    def load_models(self) -> None:
        """全レイヤのバックエンドを生成しキャッシュする(冪等)。

        既にロード済みのレイヤは触らない。各レイヤごとに `_emit_status` で LOADING →
        (backend 由来の最終状態) を通知するので、GUI 側で進捗を観測できる。
        """
        with self._load_lock:
            for layer in LayerKind:
                self._load_layer_locked(layer)

    def load_model_layer(self, layer: LayerKind) -> None:
        """単一レイヤだけをロードする(冪等)。Phase B 以降の手動ロードボタンの入口。

        既にロード済みなら何もしない。失敗時は例外を伝播し、状態は NOT_DOWNLOADED に戻す。
        """
        with self._load_lock:
            self._load_layer_locked(layer)

    def reload_model_layer(self, layer: LayerKind) -> None:
        """単一レイヤを強制的に作り直す(既ロードでも evict してから load)。

        backends_config の値(faster-whisper model_size 等)を変更したあとに、設定を
        反映させるために使う。パイプライン動作中はキャッシュ参照されている backend を
        差し替えないので、停止 → 再開で新インスタンスが入る。
        """
        with self._load_lock:
            self._evict_backend_locked(layer)
            self._emit_status(layer, ModelStatus.INIT)
            self._load_layer_locked(layer)

    def _load_layer_locked(self, layer: LayerKind) -> None:
        """`load_lock` を保持中の caller から呼ぶ実体。

        既ロードならスキップ。LOADING を emit → backend 生成 → subscribe → backend 現状を emit。
        失敗時は NOT_DOWNLOADED を emit して例外を伝播。
        """
        if layer in self._backends:
            return
        self._emit_status(layer, ModelStatus.LOADING)
        try:
            inst = self._create(layer)
        except Exception:
            self._emit_status(layer, ModelStatus.NOT_DOWNLOADED)
            raise
        self._backends[layer] = inst
        # backend のその後の状態変化を購読(将来 DOWNLOADING on reload / MISSING_CREDENTIALS 等)
        self._subscribe_backend(layer, inst)
        # 生成時点での backend 状態(通常 LOADED)を最終通知
        final_status = self._safe_backend_status(inst)
        self._emit_status(layer, final_status)

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
        """`load_lock` 保持中の caller から呼ぶ。subscribe 解除 + キャッシュ削除。"""
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

    def _safe_load_layer(self, layer: LayerKind) -> None:
        """指定レイヤを単独でロードし直す(バックエンド差し替え時に使う)。

        例外は握りつぶし(ログのみ)・status は `_load_layer_locked` 側で適切に発火する。
        """
        try:
            self.load_model_layer(layer)
        except Exception:  # noqa: BLE001
            self._logger.exception("レイヤ %s の再ロードに失敗", layer.value)

    # ---- 起動 ----
    def get_auto_load_layers(self) -> list[LayerKind]:
        """選択中 backend に `auto_load=True` が指定されているレイヤを返す(Phase B)。

        ユーザは詳細ダイアログから per-backend で auto_load を ON にする。
        該当する backend が選ばれているレイヤだけが起動時に自動ロードされる。
        """
        layers: list[LayerKind] = []
        for layer in LayerKind:
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
        for layer in LayerKind:
            # 1) backend 側が明示的に MISSING_CREDENTIALS を立てている場合
            if self.get_model_status(layer) == ModelStatus.MISSING_CREDENTIALS:
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
                problems.append(
                    f"{layer.value} ({backend_name}: 認証情報未入力 — {', '.join(missing_keys)})"
                )
                continue

            # 3) 検証(verified)を通過しているか
            if not self.is_backend_verified(backend_name):
                problems.append(f"{layer.value} ({backend_name}: 未検証)")

        if not problems:
            return
        from .errors import FatalError
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
            return

        # 同期で先に検証(呼び出し側で即時例外を受け取れる)
        input_id = self._config.get("devices", "input")
        output_id = self._config.get("devices", "output")
        DeviceValidator.validate(input_id, output_id)
        self._check_missing_credentials_gate()

        on_started = on_started or (lambda: None)
        on_failed = on_failed or (lambda _msg: None)

        def _loader_target() -> None:
            try:
                self.load_models()  # Phase B: 実質ロードが走る局面が増える
                self._start_coord(input_id, output_id)
            except Exception as exc:  # noqa: BLE001
                self._logger.exception("Loader 失敗")
                on_failed(str(exc))
                return
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
                on_fatal=self._on_fatal,
                on_warn=self._on_warn,
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

            self._coord = PipelineCoordinator(
                capture=self._backends[LayerKind.CAPTURE],
                vad=self._backends[LayerKind.VAD],
                asr=self._backends[LayerKind.ASR],
                translator=self._backends[LayerKind.TRANSLATOR],
                tts=self._backends[LayerKind.TTS],
                output=self._backends[LayerKind.OUTPUT],
                error_handler=error_handler,
                ledger=self._ledger,
                sequence=self._sequence,
                text_logger=self._text_logger,
                src_lang=src_lang,
                tgt_lang=tgt_lang,
                on_utterance_done=self._handle_utterance_done,
                on_dropped=self._handle_dropped,
                captured_queue_max_bytes=captured_max_bytes,
                synthesized_queue_max_bytes=synthesized_max_bytes,
                recognized_queue_size=recognized_size,
                translated_queue_size=translated_size,
                max_retries=max_retries,
                retry_base_sec=retry_base_sec,
                retry_max_sec=retry_max_sec,
                dump=self._stage_dump,
            )
        self._coord.start(
            capture_source_id=input_id, output_device_id=output_id
        )

    def stop_pipeline(self) -> None:
        """Coordinator を停止する。バックエンド実体は常駐させたまま残す。"""
        if self._coord is not None:
            self._coord.stop()
            self._coord = None
        if self._stage_dump is not None:
            try:
                self._stage_dump.stop_run()
            except Exception:  # noqa: BLE001 - ダンプ停止失敗で本体は止めない
                self._logger.exception("StageDumpWriter.stop_run に失敗")
            self._stage_dump = None

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
        try:
            self._on_utterance_done(record)
        except Exception:  # noqa: BLE001
            self._logger.exception("UI 通知コールバックで例外")

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
