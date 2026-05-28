"""AppController: GUI と各レイヤ/Coordinator を仲介する制御層。

役割: GUI のイベント(開始/停止/設定変更)を受け、BackendRegistry から
バックエンドを取り出し、DeviceValidator でチェック、PipelineCoordinator を
組み立てて起動・停止する。Loader スレッドでモデル初期化を非同期化し、
UI をブロックしないようにする。モデルの状態は ModelStatus で公開し、
GUI から listener 経由で受け取れる。

ロード/開始-停止 分離(本変更):
- バックエンド実体は `self._backends` にキャッシュし、毎回の Start で作り直さない。
- `load_models[_async]()` でまとめてロード(冪等)。
- `start_pipeline[_async]()` はロード済みのバックエンドで Coordinator を組み立てるだけ。
- `stop_pipeline()` は Coordinator を止めるだけ(バックエンドは在駐継続)。
- `set_setting("backends", layer, name)` は該当レイヤのキャッシュを破棄し、新名で再ロードを発火する。

コールバックシグネチャ:
- on_utterance_done(record: dict)  : UtteranceLedger.pop() の戻り値 dict を受ける
- on_dropped(seq_ids: list[int], stage: str): 捨てられた発話の seq_id を受ける
TextLogger / TranslationLogger も新 I/F (write_src/write_tgt/write_record) に追従。
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Any, Callable

from voice_translator.capture.backend import AudioCaptureBackend
from voice_translator.output.backend import AudioOutputBackend

from .backend_registry import BackendRegistry
from .config_store import ConfigStore
from .device_validator import DeviceValidator
from .error_handler import ErrorHandler
from .ledger import UtteranceLedger
from .logger import TextLogger, TranslationLogger
from .notification_throttle import NotificationThrottle
from .process_time_logger import ProcessTimeLogger
from .pipeline import PipelineCoordinator
from .sequence import SequenceGenerator
from .types import CaptureSource, LayerKind, ModelStatus, OutputDevice


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

        # バックエンド実体のキャッシュ(レイヤごとに1つ)。
        # load_models() で埋め、backends 設定の変更時に該当レイヤを破棄して再ロードする。
        self._backends: dict[LayerKind, Any] = {}
        # _backends と self._coord の生成・破棄を排他的に行う(ロード/起動の競合防止)
        self._load_lock = threading.Lock()

        # UI コールバック(既定は no-op)
        # record は UtteranceLedger.pop() の戻り値 dict(seq_id, timeline, src/tgt 各種)。
        # on_fatal/on_warn は (message, *, exc, stage, seq_id) を受ける。
        # GUI 側で stage / seq_id を使わなければ **kwargs で吸収可。
        self._on_utterance_done: Callable[[dict], None] = lambda r: None
        self._on_fatal: Callable[..., None] = lambda m, **_kw: None
        self._on_warn: Callable[..., None] = lambda m, **_kw: None
        self._on_status_change: Callable[[LayerKind, ModelStatus], None] = lambda l, s: None

        # モデル状態の初期化: 全レイヤ INIT(まだロード処理を起動していない)。
        # キャッシュ有無で初期 LOADED を出すと "Loaded→Loading→Loaded" の不自然な
        # 遷移になるため、in-memory ロードの実態を素直に表す。
        self._model_status: dict[LayerKind, ModelStatus] = {
            layer: ModelStatus.INIT for layer in LayerKind
        }

    # ============================================================
    # コールバック登録
    # ============================================================
    def set_callbacks(
        self,
        *,
        on_utterance_done: Callable[[dict], None] | None = None,
        on_fatal: Callable[..., None] | None = None,
        on_warn: Callable[..., None] | None = None,
        on_status_change: Callable[[LayerKind, ModelStatus], None] | None = None,
    ) -> None:
        if on_utterance_done is not None:
            self._on_utterance_done = on_utterance_done
        if on_fatal is not None:
            self._on_fatal = on_fatal
        if on_warn is not None:
            self._on_warn = on_warn
        if on_status_change is not None:
            self._on_status_change = on_status_change

    # ============================================================
    # モデルステータス
    # ============================================================
    def get_model_status(self, layer: LayerKind) -> ModelStatus:
        return self._model_status.get(layer, ModelStatus.INIT)

    def get_all_model_statuses(self) -> dict[LayerKind, ModelStatus]:
        return dict(self._model_status)

    def _set_status(self, layer: LayerKind, status: ModelStatus) -> None:
        self._model_status[layer] = status
        try:
            self._on_status_change(layer, status)
        except Exception:  # noqa: BLE001
            self._logger.exception("status listener error")

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
                    self._backends.pop(layer_changed, None)
                # 該当レイヤは INIT に戻す(まだロード起動前の状態)
                self._set_status(layer_changed, ModelStatus.INIT)
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
            self._backends.clear()
        for layer in LayerKind:
            self._set_status(layer, ModelStatus.INIT)

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

        既にロード済みのレイヤは触らない。各レイヤごとに status を LOADING → LOADED に
        更新するので、GUI 側で進捗を観測できる。
        """
        with self._load_lock:
            for layer in LayerKind:
                if layer in self._backends:
                    continue
                self._set_status(layer, ModelStatus.LOADING)
                try:
                    inst = self._create(layer)
                except Exception:
                    # 失敗したらステータスを戻して例外を伝播(GUI 側で on_failed が拾う)
                    self._set_status(layer, ModelStatus.NOT_DOWNLOADED)
                    raise
                self._backends[layer] = inst
                self._set_status(layer, ModelStatus.LOADED)

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

        例外は握りつぶし(ログのみ)・status は元に戻す。
        """
        with self._load_lock:
            if layer in self._backends:
                return
            self._set_status(layer, ModelStatus.LOADING)
            try:
                inst = self._create(layer)
            except Exception:  # noqa: BLE001
                self._logger.exception("レイヤ %s の再ロードに失敗", layer.value)
                self._set_status(layer, ModelStatus.NOT_DOWNLOADED)
                return
            self._backends[layer] = inst
            self._set_status(layer, ModelStatus.LOADED)

    # ---- 起動 ----
    def start_pipeline_async(
        self,
        *,
        on_started: Callable[[], None] | None = None,
        on_failed: Callable[[str], None] | None = None,
    ) -> None:
        """Loader スレッドでロード(必要なら)+ パイプラインを起動する(非同期)。

        - 既に動作中 / ロード中なら何もしない。
        - DeviceValidator は呼び出し元スレッドで先にチェックする(即時に失敗を返したい)。
        - 既にロード済みなら "起動だけ" になり、ほぼ即座に on_started が呼ばれる。
        """
        if self.is_running or self.is_loading:
            return

        # 同期で先に検証(呼び出し側で即時例外を受け取れる)
        input_id = self._config.get("devices", "input")
        output_id = self._config.get("devices", "output")
        DeviceValidator.validate(input_id, output_id)

        on_started = on_started or (lambda: None)
        on_failed = on_failed or (lambda _msg: None)

        def _loader_target() -> None:
            try:
                self.load_models()  # 未ロードのみ作る
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
            )
        self._coord.start(
            capture_source_id=input_id, output_device_id=output_id
        )

    def stop_pipeline(self) -> None:
        """Coordinator を停止する。バックエンド実体は常駐させたまま残す。"""
        if self._coord is not None:
            self._coord.stop()
            self._coord = None

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
