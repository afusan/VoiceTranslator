# feature/lazy-load-auth-gate テスト計画

すべて small(モック/純関数)。実モデル・実 API は使わない。

## Phase 1: 変更即ロードの廃止
- [x] `set_setting("backends", layer, name)` で該当レイヤが evict され INIT が emit される(既存挙動の温存)
- [x] 変更後に**ロードが自動で走らない**(`_backends` に新インスタンスが入らない / ロードスレッドが起動しない)
- [x] 変更後の Start(`start_pipeline_async`)で未ロード分がロードされて起動する(既存の押下時ロードで吸収)
- [x] text_only の TTS/Output 変更でも例外なく evict + INIT のみ(特例分岐撤去の確認)
- [x] 旧テスト「バックエンド変更時の単一レイヤ再ロード」は新挙動の検証に書き換え(消さない)

## Phase 2: 認証状態の静的計算と表示
### CredentialsService.get_auth_state(純ロジック)
- [x] requires_credentials=False の backend → NOT_REQUIRED
- [x] 未登録 backend / spec 無し → NOT_REQUIRED
- [x] spec のキーが 1 つでも未保存 → MISSING
- [x] 全キー保存済み + verified=False → UNVERIFIED
- [x] 全キー保存済み + verified=True → VERIFIED

### gui/logic/auth_display(固定文字列)
- [x] MISSING → ("Missing Credentials", 赤)
- [x] UNVERIFIED → ("Not Verified", 琥珀)
- [x] NOT_REQUIRED / VERIFIED → None(通常表示に委譲)
- [x] palette: ModelStatus.MISSING_CREDENTIALS に赤が定義されている

### ready_state(固定文字列)
- [x] auth=MISSING のレイヤあり → toggle 無効 + 「認証情報未設定」
- [x] auth=UNVERIFIED のレイヤあり → toggle 無効 + 「認証未検証」
- [x] instance 状態 MISSING_CREDENTIALS でも従来どおり無効(後方互換)
- [x] 優先順位: MISSING > UNVERIFIED > DOWNLOADING > PROCESS未選択 > 通常
- [x] text_only / absorbed のレイヤの auth は判定対象外

### 配線(shim / smoke)
- [x] SettingsPanel._apply_status: auth=MISSING のレイヤは INIT でも "Missing Credentials"(赤)表示
- [x] auth=UNVERIFIED + instance LOADED → "Not Verified"(琥珀)が優先
- [x] 吸収(空表示)/(なし)の上書きは auth より優先
- [x] verify_and_save_credentials 成功 → settings イベント(credentials)が emit される
- [x] invalidate_verification → 同イベント emit
- [x] 認証成功後の後処理が evict + INIT になっている(reload しない)

## Phase 3: ロックエンジン再構成
- [x] 構築中(_create ブロック中)でも set_setting の evict が**待たずに**完了する
      (イベントで構築を堰き止め、別スレッドの evict がタイムアウトなしで返ることを検証)
- [x] 構築中に evict(世代+1)→ 構築完了結果は破棄され、最新選択がロードし直される(last-write-wins)
- [x] 同一レイヤの並行ロード要求は二重構築にならない(1 回の構築を共有)
- [x] 構築失敗時: in-flight が解除され NOT_DOWNLOADED が emit される(既存挙動の温存)
- [x] load_models の冪等性(既ロードはスキップ)が維持される
- [x] stop 後もバックエンド常駐(既存テストの温存)

## 回帰(全 Phase 共通)
- [x] 既存 small スイート全件 pass(1,257 件 + 本ブランチ追加分)
