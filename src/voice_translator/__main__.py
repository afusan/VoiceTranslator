"""エントリポイント。`python -m voice_translator` で GUI を起動する。

役割: 設定読込 → アプリロガー初期化 → BackendRegistry 構築 →
AppController を作って MainWindow を立ち上げ、mainloop を回す。
"""

from __future__ import annotations

from pathlib import Path

from voice_translator.common.app_controller import AppController
from voice_translator.common.backend_registry import BackendRegistry
from voice_translator.common.backend_setup import register_default_backends
from voice_translator.common.config_store import ConfigStore
from voice_translator.common.logger import setup_app_logger


def _default_config_path() -> Path:
    """設定ファイルの既定パス: カレントディレクトリの config.yaml。"""
    return Path("./config.yaml")


def main() -> None:
    """GUI を起動するエントリポイント。"""
    # 1) 設定
    config = ConfigStore(_default_config_path())
    try:
        config.load()
    except Exception as e:  # noqa: BLE001 - 起動時は既定値で続行
        print(f"[起動] 設定読込で例外。既定値で続行: {e}")

    # 2) ロガー(level は config から)
    log_dir = Path(str(config.get("log", "directory", default="./logs")))
    log_level = str(config.get("log", "level", default="INFO"))
    logger = setup_app_logger(log_dir=log_dir, level=log_level)
    logger.info("voice_translator 起動 (log level=%s)", log_level)

    # 3) バックエンド登録(config 連携で SAPI rate 等を反映)
    registry = BackendRegistry()
    register_default_backends(registry, config)

    # 4) AppController と MainWindow
    controller = AppController(registry=registry, config=config, app_logger=logger)

    # GUI 起動(customtkinter は実機で評価する。テストでは別途モック)
    from voice_translator.gui.main_window import MainWindow

    window = MainWindow(controller)
    window.mainloop()


if __name__ == "__main__":
    main()
