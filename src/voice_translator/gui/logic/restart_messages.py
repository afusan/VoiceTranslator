"""restart_messages: 自動 restart バナーの文言を決める純関数。

役割: `PipelineRestartEvent` から通知バナーに出す文言を組み立てる。文言は i18n
カタログ(`gui/i18n.py`)の `restart.*` キーから引く。
"""

from __future__ import annotations

from ..i18n import tr


def device_label(device_key: str) -> str:
    """devices キーを表示種別に変換する(未知キーはそのまま)。"""
    if device_key == "input":
        return tr("restart.device.input")
    if device_key == "output":
        return tr("restart.device.output")
    return device_key


def format_restart_started(device_key: str) -> str:
    """restart 開始バナー(永続表示、完了で dismiss される)の文言。"""
    return tr("restart.started", device=device_label(device_key))


def format_restart_failed(device_key: str, message: str) -> str:
    """restart 失敗バナーの文言。"""
    return tr("restart.failed", device=device_label(device_key), message=message)
