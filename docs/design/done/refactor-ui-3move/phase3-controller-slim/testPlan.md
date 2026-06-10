# P3: controller-slim — テスト計画

作成: 2026-06-10。全テスト small。**既存テストは原則無修正**(互換窓経由で従来どおり
通ることが、委譲の正しさの検証を兼ねる)。

## 1. tests/test_backend_catalog.py(新規)

test_app_controller.py の TestAsrSupportedLanguages / TestTranslatorSupportedLanguages /
TestTtsSupportedOutputLanguages / TestPhaseDCapabilityHint / capture_kind 系のシナリオを
BackendCatalog 直叩きで移植:

| # | ケース | 期待 |
|---|---|---|
| 1 | 登録済み backend_cls のクラスメソッド呼び出し(各メタ API) | 宣言値が返る |
| 2 | 未登録 backend | 縮退既定値(DEVICE / [] / False / None) |
| 3 | backend_cls 未提供(factory のみ register) | 縮退既定値 |
| 4 | クラスメソッドが例外 | 縮退既定値 + 例外を飲む(ログ) |
| 5 | capture_kind が CaptureKind 以外を返す | そのまま返す(判定は呼び出し側。現挙動維持) |

## 2. tests/test_credentials_service.py(新規)

test_credentials.py / test_app_controller.py(Phase D/E-2 系)のシナリオを
CredentialsService 直叩きで移植(InMemoryKeyring fixture を使用):

| # | ケース | 期待 |
|---|---|---|
| 1 | set → get / has / delete の基本動作 | CredentialsStore に反映 |
| 2 | set で verified が False に戻る | config の `credentials.verified.<backend>` |
| 3 | 空文字 set はスキップ(verify_and_save 経由) | 既存値を消さない |
| 4 | verify_and_save: 成功 → 保存 + verified=True | VerifyResult(ok=True) |
| 5 | verify_and_save: 失敗 → 何も保存しない | VerifyResult(ok=False) |
| 6 | verify_and_save: backend クラス未登録 | ok=False + message に未登録の旨 |
| 7 | verify_and_save: verify_credentials が例外 | ok=False(「検証中に例外」) |
| 8 | invalidate_verification | verified=False |
| 9 | CredentialsStore の lazy 初期化(`credentials.use_local_file` 反映) | keyring 注入タイミングを尊重 |

## 3. 互換窓の確認(test_app_controller.py に追加)

| # | ケース | 期待 |
|---|---|---|
| 1 | `ctrl.catalog` / `ctrl.credentials` プロパティが実体を返す | isinstance 確認 |
| 2 | verify_and_save_credentials 成功 + 該当レイヤ MISSING_CREDENTIALS | reload が走る(Phase F1 後処理がランタイム側に残っていること。既存テストがあれば流用) |

## 4. 手動チェック(契約)

- §2.4(MISSING_CREDENTIALS 表示)/ §8 全章(認証フロー一式)
- 既存の `tests/test_credential_flow.py`(22 契約テスト)が無修正で pass することを完了条件に含める
