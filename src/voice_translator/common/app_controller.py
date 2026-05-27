"""AppController: GUI と各レイヤ/Coordinator を仲介する制御層。

役割: GUI のイベント(開始/停止/設定変更)を受け、BackendRegistry から
バックエンドを取り出し、DeviceValidator でチェック、PipelineCoordinator を
組み立てて起動・停止する。Loader スレッドでモデル初期化を非同期化し、
UI をブロックしないようにする。モデルの状態は ModelStatus で公開し、
GUI から listener 経由で受け取れる。

R-3 でコールバックシグネチャを更新:
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

from . import cache_check
from .backend_registry import BackendRegistry
from .config_store import ConfigStore
from .device_validator import DeviceValidator
from .error_handler import ErrorHandler
from .ledger import UtteranceLedger
from .logger import TextLogger, TranslationLogger
from .pipeline import PipelineCoordinator
from .sequence import SequenceGenerator
from .types import CaptureSource, LayerKind, ModelStatus, OutputDevice


# レイヤ + 選択中バックエンド名 → cache_check モジュール内の関数名
# (直接 callable を保持しないことで monkeypatch を効くようにする)
_CACHE_CHECKER_NAMES: dict[tuple[LayerKind, str], str] = {
    (LayerKind.CAPTURE, "soundcard"): "check_soundcard",
    (LayerKind.VAD, "silero"): "check_silero",
    (LayerKind.ASR, "faster_whisper"): "check_faster_whisper",
    (LayerKind.TRANSLATOR, "nllb200"): "check_nllb200",
    (LayerKind.TTS, "sapi"): "check_sapi",
    (LayerKind.OUTPUT, "soundcard"): "check_soundcard",
}


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
        self._ledger: UtteranceLedger | None = None
        self._sequence: SequenceGenerator | None = None
        self._loader_thread: threading.Thread | None = None

        # UI コールバック(既定は no-op)
        # record は UtteranceLedger.pop() の戻り値 dict(seq_id, timeline, src/tgt 各種)。
        # on_fatal/on_warn は (message, *, exc, stage, seq_id) を受ける。
        # GUI 側で stage / seq_id を使わなければ **kwargs で吸収可。
        self._on_utterance_done: Callable[[dict], None] = lambda r: None
        self._on_fatal: Callable[..., None] = lambda m, **_kw: None
        self._on_warn: Callable[..., None] = lambda m, **_kw: None
        self._on_status_change: Callable[[LayerKind, ModelStatus], None] = lambda l, s: None

        # モデル状態の初期化(キャッシュチェック)
        self._model_status: dict[LayerKind, ModelStatus] = {}
        self._refresh_model_status_from_cache()

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
        return self._model_status.get(layer, ModelStatus.NOT_DOWNLOADED)

    def get_all_model_statuses(self) -> dict[LayerKind, ModelStatus]:
        return dict(self._model_status)

    def _refresh_model_status_from_cache(self) -> None:
        """設定中のバックエンドに対するキャッシュ状況で状態を初期化する。"""
        for layer in LayerKind:
            name = self._config.get("backends", layer.value, default="")
            func_name = _CACHE_CHECKER_NAMES.get((layer, name))
            checker = getattr(cache_check, func_name, None) if func_name else None
            status = checker() if checker is not None else ModelStatus.NOT_DOWNLOADED
            self._model_status[layer] = status

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
        # バックエンド変更ならステータスを更新
        if len(keys_and_value) == 3 and keys_and_value[0] == "backends":
            self._refresh_model_status_from_cache()
            for layer in LayerKind:
                self._on_status_change(layer, self._model_status[layer])

    def save_settings(self) -> None:
        self._config.save()

    def load_settings(self) -> None:
        self._config.load()
        self._refresh_model_status_from_cache()
        for layer in LayerKind:
            self._on_status_change(layer, self._model_status[layer])

    # ============================================================
    # 起動/停止
    # ============================================================
    @property
    def is_running(self) -> bool:
        return self._coord is not None and self._coord.is_running

    @property
    def is_loading(self) -> bool:
        return self._loader_thread is not None and self._loader_thread.is_alive()

    def start_pipeline_async(
        self,
        *,
        on_started: Callable[[], None] | None = None,
        on_failed: Callable[[str], None] | None = None,
    ) -> None:
        """Loader スレッドでモデルをロードしパイプラインを起動する(非同期)。

        - 既に動作中 / ロード中なら何もしない。
        - DeviceValidator は呼び出し元スレッドで先にチェックする(即時に失敗を返したい)。
        - ロード成功時に on_started、失敗時に on_failed が(Loader スレッド上で)呼ばれる。
        """
        if self.is_running or self.is_loading:
            return

        # 同期で先に検証(呼び出し側で即時例外を受け取れる)
        input_id = self._config.get("devices", "input")
        output_id = self._config.get("devices", "output")
        DeviceValidator.validate(input_id, output_id)

        on_started = on_started or (lambda: None)
        on_failed = on_failed or (lambda msg: None)

        def _loader_target() -> None:
            try:
                self._loader_body(input_id, output_id)
            except Exception as exc:  # noqa: BLE001
                self._logger.exception("Loader 失敗")
                on_failed(str(exc))
                return
            on_started()

        self._loader_thread = threading.Thread(
            target=_loader_target, name="vt_loader", daemon=True
        )
        self._loader_thread.start()

    def _loader_body(self, input_id: str, output_id: str) -> None:
        """Loader スレッド本体: バックエンドを順に作って Coordinator を起動。"""
        # 全レイヤを Loading にしてから着手
        for layer in LayerKind:
            self._set_status(layer, ModelStatus.LOADING)

        # 順次インスタンス化(成功するごとに LOADED)
        capture = self._create(LayerKind.CAPTURE)
        self._set_status(LayerKind.CAPTURE, ModelStatus.LOADED)

        vad = self._create(LayerKind.VAD)
        self._set_status(LayerKind.VAD, ModelStatus.LOADED)

        asr = self._create(LayerKind.ASR)
        self._set_status(LayerKind.ASR, ModelStatus.LOADED)

        translator = self._create(LayerKind.TRANSLATOR)
        self._set_status(LayerKind.TRANSLATOR, ModelStatus.LOADED)

        tts = self._create(LayerKind.TTS)
        self._set_status(LayerKind.TTS, ModelStatus.LOADED)

        output = self._create(LayerKind.OUTPUT)
        self._set_status(LayerKind.OUTPUT, ModelStatus.LOADED)

        # 翻訳jsonl + 個別テキストログ
        log_dir = Path(self._config.get("log", "directory", default="./logs"))
        jsonl_enabled = bool(self._config.get("log", "jsonl_enabled", default=True))
        src_text_enabled = bool(self._config.get("log", "src_text_enabled", default=False))
        tgt_text_enabled = bool(self._config.get("log", "tgt_text_enabled", default=False))
        self._translation_logger = TranslationLogger(
            log_dir / "translations.jsonl", enabled=jsonl_enabled
        )
        self._text_logger = TextLogger(
            src_path=log_dir / "soundsrc.txt",
            tgt_path=log_dir / "translated.txt",
            src_enabled=src_text_enabled,
            tgt_enabled=tgt_text_enabled,
        )

        error_handler = ErrorHandler(
            logger=self._logger,
            on_fatal=self._on_fatal,
            on_warn=self._on_warn,
        )

        src_lang = self._config.get("languages", "src", default="auto")
        tgt_lang = self._config.get("languages", "tgt", default="ja")

        # ledger + sequence は Coordinator にも保持されるが、AppController からも参照したいので持つ
        self._ledger = UtteranceLedger()
        self._sequence = SequenceGenerator()

        self._coord = PipelineCoordinator(
            capture=capture,
            vad=vad,
            asr=asr,
            translator=translator,
            tts=tts,
            output=output,
            error_handler=error_handler,
            ledger=self._ledger,
            sequence=self._sequence,
            text_logger=self._text_logger,
            src_lang=src_lang,
            tgt_lang=tgt_lang,
            on_utterance_done=self._handle_utterance_done,
            on_dropped=self._handle_dropped,
        )
        self._coord.start(
            capture_source_id=input_id, output_device_id=output_id
        )

    # 旧API互換(同期版)も残す: テスト互換用
    def start_pipeline(self) -> None:
        """同期版: テスト・スクリプト用。GUI からは start_pipeline_async を使う。"""
        if self.is_running:
            return
        input_id = self._config.get("devices", "input")
        output_id = self._config.get("devices", "output")
        DeviceValidator.validate(input_id, output_id)
        self._loader_body(input_id, output_id)

    def stop_pipeline(self) -> None:
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
