"""credentials: クラウド backend の認証情報(API key 等)の保管。

役割: OS keychain(keyring)を第一とし、不可なら同じプロジェクト直下の平文ファイル
`local.secrets` に fallback する(R-5)。3 層運用:
- **テスト**: `keyring.set_keyring(InMemoryKeyring())` 等の test double で実 keyring に
  触らない(`tests/_fixtures.py`)。
- **開発者ローカル**: ConfigStore で `credentials.use_local_file: true` を立てると
  keyring を経由せず常に平文ファイルを読む。実 API 検証のオン/オフを 1 フラグで切替。
- **エンドユーザ**: 既定では keyring(成功)→ 平文ファイル(失敗時 fallback)。

設計判断:
- `local.secrets` という名前を採用(`.env` という名前は web scanner のターゲットなので NG、
  プロジェクト直下の慣習的なファイル名を避ける)。`.gitignore` で除外する。
- ファイル内容は JSON(`{"<backend>": {"<key>": "<value>"}}`)。
- backend 実装は本モジュールの関数だけを呼べばよい。バックエンド/キー名は文字列で渡す。
- セキュリティ: ログにキーを書かない、エラーメッセージにキーを書かないこと(R-5 / 横断方針)。
"""

from __future__ import annotations

import json
import logging
import threading
from pathlib import Path
from typing import Any

# 平文ファイル fallback の既定パス(`.gitignore` で除外する)。
DEFAULT_SECRETS_FILE: str = "local.secrets"

# keyring に保存するときの「サービス名」プレフィックス。
# キー名を「<service_prefix>:<backend>」、ユーザ名を「<key_name>」とする。
_KEYRING_SERVICE_PREFIX = "voice-translator"

# 起動時の keyring 死活確認に使うダミー値(誰のデータも上書きしないよう専用キー)。
_PROBE_BACKEND = "__probe__"
_PROBE_KEY = "__probe__"

_logger = logging.getLogger("voice_translator.credentials")


class CredentialsStore:
    """認証情報の保管/取得を担うクラス。

    役割: `mode` に応じて keyring と平文ファイルを使い分け、backend 実装からは
    `get/set/delete` の 3 メソッドで透過的に扱える形を提供する。
    `mode` は `__init__` 時に確定(以後は固定)するので、起動ログで「現在の保管経路」を
    記録できる(R2-7)。
    """

    def __init__(
        self,
        *,
        use_local_file: bool = False,
        file_path: Path | str = DEFAULT_SECRETS_FILE,
    ) -> None:
        self._file_path = Path(file_path)
        self._lock = threading.Lock()
        self._mode = self._detect_mode(use_local_file)
        # 起動時の選択結果を 1 行ログに残す(問題切り分けの起点。R2-7)
        _logger.info("credentials backend = %s (file=%s)", self._mode, self._file_path)

    @property
    def mode(self) -> str:
        """`"keyring"` か `"file"`。起動時に確定し以後変わらない。"""
        return self._mode

    @property
    def file_path(self) -> Path:
        return self._file_path

    # ============================================================
    # 公開 API
    # ============================================================
    def get(self, backend: str, key: str) -> str | None:
        """指定 backend / key の値を返す。未設定なら None。"""
        if self._mode == "keyring":
            value = self._keyring_get(backend, key)
            if value is not None:
                return value
            # keyring が個別 key で None を返すケースもあるので、保険で file も覗く
            return self._file_get(backend, key)
        return self._file_get(backend, key)

    def set(self, backend: str, key: str, value: str) -> None:
        """指定 backend / key に値を保存する。空文字 / None は delete と同義。"""
        if value is None or value == "":
            self.delete(backend, key)
            return
        if self._mode == "keyring":
            try:
                self._keyring_set(backend, key, value)
                return
            except Exception:  # noqa: BLE001
                # 個別 set で失敗したら file 経路で残す(運用継続を優先)
                _logger.warning(
                    "keyring set 失敗、平文ファイルに退避します backend=%s", backend
                )
        self._file_set(backend, key, value)

    def delete(self, backend: str, key: str) -> None:
        """指定 backend / key を削除する。存在しなくても例外は出さない。"""
        if self._mode == "keyring":
            try:
                self._keyring_delete(backend, key)
            except Exception:  # noqa: BLE001
                pass
        # file 側にも残骸があれば消す
        self._file_delete(backend, key)

    # ============================================================
    # 内部: モード判定
    # ============================================================
    def _detect_mode(self, use_local_file: bool) -> str:
        """`use_local_file=True` なら強制 file、それ以外は keyring を probe して使えるなら keyring。"""
        if use_local_file:
            return "file"
        try:
            import keyring  # type: ignore  # noqa: F401
        except Exception:  # noqa: BLE001
            return "file"
        try:
            self._keyring_get(_PROBE_BACKEND, _PROBE_KEY)
            return "keyring"
        except Exception:  # noqa: BLE001
            return "file"

    # ============================================================
    # 内部: keyring 経路
    # ============================================================
    @staticmethod
    def _keyring_service_name(backend: str) -> str:
        return f"{_KEYRING_SERVICE_PREFIX}:{backend}"

    def _keyring_get(self, backend: str, key: str) -> str | None:
        import keyring  # type: ignore
        return keyring.get_password(self._keyring_service_name(backend), key)

    def _keyring_set(self, backend: str, key: str, value: str) -> None:
        import keyring  # type: ignore
        keyring.set_password(self._keyring_service_name(backend), key, value)

    def _keyring_delete(self, backend: str, key: str) -> None:
        import keyring  # type: ignore
        try:
            keyring.delete_password(self._keyring_service_name(backend), key)
        except keyring.errors.PasswordDeleteError:
            # 存在しないキーの削除は無視
            pass

    # ============================================================
    # 内部: 平文ファイル経路
    # ============================================================
    def _read_file(self) -> dict[str, dict[str, str]]:
        if not self._file_path.exists():
            return {}
        try:
            with self._file_path.open("r", encoding="utf-8") as f:
                data: Any = json.load(f)
        except (OSError, json.JSONDecodeError):
            return {}
        if not isinstance(data, dict):
            return {}
        # 内部構造を normalize
        result: dict[str, dict[str, str]] = {}
        for backend, kv in data.items():
            if isinstance(kv, dict):
                result[str(backend)] = {str(k): str(v) for k, v in kv.items()}
        return result

    def _write_file(self, data: dict[str, dict[str, str]]) -> None:
        try:
            self._file_path.parent.mkdir(parents=True, exist_ok=True)
            with self._file_path.open("w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except OSError as e:
            _logger.error("credentials ファイル書き出し失敗: %s", e)

    def _file_get(self, backend: str, key: str) -> str | None:
        with self._lock:
            data = self._read_file()
        return data.get(backend, {}).get(key)

    def _file_set(self, backend: str, key: str, value: str) -> None:
        with self._lock:
            data = self._read_file()
            data.setdefault(backend, {})[key] = value
            self._write_file(data)

    def _file_delete(self, backend: str, key: str) -> None:
        with self._lock:
            data = self._read_file()
            if backend in data and key in data[backend]:
                del data[backend][key]
                if not data[backend]:
                    del data[backend]
                self._write_file(data)
