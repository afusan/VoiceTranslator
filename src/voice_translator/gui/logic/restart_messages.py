"""restart_messages: 自動 restart バナーの文言を決める純関数。

役割: `PipelineRestartEvent` から通知バナーに出す文言を組み立てる。
移行元(P2 / refactor-ui-3move): settings_panel.py の `_trigger_device_restart` /
`_apply_restart_failed` のリテラル。文言は移行元と一字一句同一に保つこと。
"""

from __future__ import annotations

# devices キー → 表示種別(移行元では呼び出し側が "入力"/"出力" を直接渡していた)
DEVICE_KEY_LABELS: dict[str, str] = {
    "input": "入力",
    "output": "出力",
}


def device_label(device_key: str) -> str:
    """devices キーを表示種別に変換する(未知キーはそのまま)。"""
    return DEVICE_KEY_LABELS.get(device_key, device_key)


def format_restart_started(device_key: str) -> str:
    """restart 開始バナー(永続表示、完了で dismiss される)の文言。"""
    return f"{device_label(device_key)}デバイスを切り替えました(再開中…)"


def format_restart_failed(device_key: str, message: str) -> str:
    """restart 失敗バナーの文言。"""
    return (
        f"{device_label(device_key)}デバイス変更後の再開に失敗しました: {message}"
    )
