# P3: controller-slim — 無状態 2 切片の分離(処方箋)

作成: 2026-06-10。ブランチ: `refactor/ui-phase3-controller-slim`(P2 ブランチから派生)。
上位: [../Roadmap.md](../Roadmap.md)

---

## 1. 目的と完了条件

**目的**: AppController から「状態を持たない 2 切片」(レジストリのメタ問合せ / 認証)の
**実装本体**を分離し、AppController を「設定 + ロード + パイプライン実行のランタイム」に近づける。

**完了条件**:
1. `py -m uv run pytest` 全 pass(既存テストは無修正で通る = 委譲の正しさも検証される)
2. `common/backend_catalog.py` / `common/credentials_service.py` が新設され、
   それぞれ専用の small テストを持つ
3. AppController のメタ問合せ・認証メソッドが 1 行委譲(互換窓)になっている
4. 契約 §2 / §8 の確認記録

## 2. スコープ判断(Roadmap Move 3 からの調整)

**互換窓(1 行委譲)を残す**。理由: 移管対象 API の参照は GUI 4 ファイル 17 箇所 +
テスト 10 ファイル超に及び、全付け替えは挙動上の利得ゼロのまま大規模な機械的churn になる。
P3 の本質(実装の置き場を分離し、以後のメタ問合せ系の追加先を catalog に固定する)は
互換窓があっても成立する。

- 新規コードは `controller.catalog` / `controller.credentials` を直接使うこと(規約)
- GUI 既存参照の直接付け替え + 互換窓の削除は **P4(任意)** に積む

## 3. 設計

### 3.1 `common/backend_catalog.py` — BackendCatalog

役割: **backend クラスのメタ情報問合せ口(状態なし)**。インスタンス化せずクラスメソッドを
呼ぶだけ。未登録 / 例外時は安全側の既定値に縮退(現挙動を 1:1 移管)。

```python
class BackendCatalog:
    def __init__(self, registry: BackendRegistry, logger: logging.Logger | None = None)
    def get_capture_kind(self, backend_name) -> CaptureKind          # 縮退: DEVICE
    def get_supported_input_languages(self, backend_name) -> list[str]   # 縮退: []
    def supports_auto_detect(self, backend_name) -> bool             # 縮退: False
    def get_supported_target_languages(self, backend_name) -> list[str]  # 縮退: []
    def get_supported_output_languages(self, backend_name) -> list[str]  # 縮退: []
    def get_credential_spec(self, layer, name) -> list[CredentialField]  # 縮退: []
    def get_capability_hint(self, layer, name) -> BackendCapabilities | None
```

### 3.2 `common/credentials_service.py` — CredentialsService

役割: **認証情報の保管・疎通確認・verified フラグ管理**。CredentialsStore(lazy 初期化)+
ConfigStore の `credentials.*` キーに閉じる。backend キャッシュには触らない。

```python
class CredentialsService:
    def __init__(self, *, registry, config, logger=None)
    def get(self, backend, key) -> str | None
    def set(self, backend, key, value) -> None        # verified を False に戻す(現挙動)
    def delete(self, backend, key) -> None
    def has(self, backend, key) -> bool
    def is_backend_verified(self, backend_name) -> bool
    def invalidate_verification(self, backend_name) -> None
    def verify_and_save(self, layer, backend_name, values) -> VerifyResult
        # verify_credentials 呼び出し → 成功時のみ保存 + verified=True(現挙動を 1:1 移管)
```

**Phase F1 の後処理**(認証成功時、該当レイヤが MISSING_CREDENTIALS なら reload)は
**ランタイムの責務**のため AppController 側の互換窓 `verify_and_save_credentials` に残す:
`result = self.credentials.verify_and_save(...)` → ok なら従来どおり reload 判定 → result 返却。

### 3.3 AppController

- `__init__` で両者を構築し、`catalog` / `credentials` プロパティで公開
- 既存メソッド(get_capture_kind / get_supported_* / supports_auto_detect /
  get_credential_spec / get_backend_capability_hint / get_credential / set_credential /
  delete_credential / has_credential / is_backend_verified / invalidate_verification /
  verify_and_save_credentials)は **1 行委譲 + 1 行 docstring**(実体クラスへの参照を明記)
- 内部利用(`_check_missing_credentials_gate` / `_clear_process_input_if_applicable` 等)は
  catalog / credentials を直接使う
- `_credentials_store` / `_credentials` フィールドは service へ移動

## 4. テスト(testPlan.md 参照)

新規: `tests/test_backend_catalog.py` / `tests/test_credentials_service.py`
(test_app_controller の該当シナリオを移植。**移植元のテストは互換窓の検証として残す**)。

## 5. ガードレール

P1 Plan §5 と同一。追加: 縮退挙動(未登録 → 既定値、例外 → 既定値 + ログ)を
変更しないこと(GUI の防御縮退が依存している)。
