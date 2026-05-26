"""AppController: GUI と各レイヤ/Coordinator を仲介する制御層。

役割: GUI のイベント(開始ボタン、設定変更等)を受け、BackendRegistry から
バックエンドを取り出し、DeviceValidator でチェック、PipelineCoordinator を
組み立てて起動・停止する。設定の保存/読込もここを経由する。
GUI から切り離すことで AppController 自体をテストしやすくする。
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Callable

from voice_translator.capture.backend import AudioCaptureBackend
from voice_translator.output.backend import AudioOutputBackend

from .backend_registry import BackendRegistry
from .config_store import ConfigStore
from .device_validator import DeviceValidator
from .error_handler import ErrorHandler
from .logger import TranslationLogger
from .pipeline import PipelineCoordinator
from .types import CaptureSource, LayerKind, OutputDevice
from .utterance import Utterance


class AppController:
    """GUI と内部モジュールを橋渡しする制御クラス。

    役割: 設定値に基づいてバックエンドを生成しパイプラインを起動/停止する。
    GUI からは set_callbacks() で UI 更新コールバックを受け取り、
    パイプラインの結果やエラーを GUI に伝える。
    """

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

        # UI コールバック(デフォルトは no-op)
        self._on_utterance_done: Callable[[Utterance], None] = lambda u: None
        self._on_fatal: Callable[[str], None] = lambda m: None
        self._on_warn: Callable[[str], None] = lambda m: None

    # ============================================================
    # コールバック登録
    # ============================================================
    def set_callbacks(
        self,
        *,
        on_utterance_done: Callable[[Utterance], None] | None = None,
        on_fatal: Callable[[str], None] | None = None,
        on_warn: Callable[[str], None] | None = None,
    ) -> None:
        """GUI 側の更新コールバックを登録する。"""
        if on_utterance_done is not None:
            self._on_utterance_done = on_utterance_done
        if on_fatal is not None:
            self._on_fatal = on_fatal
        if on_warn is not None:
            self._on_warn = on_warn

    # ============================================================
    # 列挙
    # ============================================================
    def list_capture_sources(self) -> list[CaptureSource]:
        """設定で選ばれた capture バックエンドを使ってソース一覧を返す。"""
        backend: AudioCaptureBackend = self._create(LayerKind.CAPTURE)
        return backend.list_sources()

    def list_output_devices(self) -> list[OutputDevice]:
        """設定で選ばれた output バックエンドを使ってデバイス一覧を返す。"""
        backend: AudioOutputBackend = self._create(LayerKind.OUTPUT)
        return backend.list_devices()

    def list_backends(self, layer: LayerKind) -> list[str]:
        """指定レイヤに登録されているバックエンド名一覧。"""
        return self._registry.list_names(layer)

    # ============================================================
    # 設定アクセス
    # ============================================================
    def get_setting(self, *keys: str, default: Any = None) -> Any:
        return self._config.get(*keys, default=default)

    def set_setting(self, *keys_and_value: Any) -> None:
        self._config.set(*keys_and_value)

    def save_settings(self) -> None:
        self._config.save()

    def load_settings(self) -> None:
        self._config.load()

    # ============================================================
    # パイプライン起動/停止
    # ============================================================
    @property
    def is_running(self) -> bool:
        return self._coord is not None and self._coord.is_running

    def start_pipeline(self) -> None:
        """設定値に基づいてパイプラインを組み立て、起動する。

        - DeviceValidator で入出力デバイスをチェック → 違反は FatalError(callback 経由で通知)。
        - 既に動作中なら何もしない。
        """
        if self.is_running:
            return

        input_id = self._config.get("devices", "input")
        output_id = self._config.get("devices", "output")
        DeviceValidator.validate(input_id, output_id)

        capture = self._create(LayerKind.CAPTURE)
        vad = self._create(LayerKind.VAD)
        asr = self._create(LayerKind.ASR)
        translator = self._create(LayerKind.TRANSLATOR)
        tts = self._create(LayerKind.TTS)
        output = self._create(LayerKind.OUTPUT)

        # 翻訳ログ(jsonl)
        log_dir = Path(self._config.get("log", "directory", default="./logs"))
        jsonl_enabled = bool(self._config.get("log", "jsonl_enabled", default=True))
        self._translation_logger = TranslationLogger(
            log_dir / "translations.jsonl", enabled=jsonl_enabled
        )

        # エラーハンドラ(致命/警告は UI へ転送)
        error_handler = ErrorHandler(
            logger=self._logger,
            on_fatal=self._on_fatal,
            on_warn=self._on_warn,
        )

        src_lang = self._config.get("languages", "src", default="auto")
        tgt_lang = self._config.get("languages", "tgt", default="ja")

        self._coord = PipelineCoordinator(
            capture=capture,
            vad=vad,
            asr=asr,
            translator=translator,
            tts=tts,
            output=output,
            error_handler=error_handler,
            src_lang=src_lang,
            tgt_lang=tgt_lang,
            on_utterance_done=self._handle_utterance_done,
        )
        self._coord.start(
            capture_source_id=input_id, output_device_id=output_id
        )

    def stop_pipeline(self) -> None:
        """パイプライン停止。複数回呼ばれても安全。"""
        if self._coord is not None:
            self._coord.stop()
            self._coord = None

    # ---- 内部 ----
    def _create(self, layer: LayerKind):
        """ConfigStore の現在の選択でバックエンドインスタンスを生成。"""
        name = self._config.get("backends", layer.value)
        if not name:
            raise KeyError(f"backends.{layer.value} が設定されていません")
        return self._registry.create(layer, name)

    def _handle_utterance_done(self, utterance: Utterance) -> None:
        """Coordinator スレッドから呼ばれる: jsonl 書き出し + UI通知。"""
        try:
            if self._translation_logger is not None:
                self._translation_logger.write(utterance)
        except Exception:  # noqa: BLE001
            self._logger.exception("翻訳ログ書き出しに失敗")
        # UI コールバック(GUI側で after() を使ってメインスレッドに戻すこと)
        try:
            self._on_utterance_done(utterance)
        except Exception:  # noqa: BLE001
            self._logger.exception("UI 通知コールバックで例外")
