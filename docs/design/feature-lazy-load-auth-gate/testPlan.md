# feature/lazy-load-auth-gate テスト計画

すべて small(モック/純関数)。実モデル・実 API は使わない。

## Phase 1: 変更即ロードの廃止
- [ ] `set_setting("backends", layer, name)` で該当レイヤが evict され INIT が emit される(既存挙動の温存)
- [ ] 変更後に**ロードが自動で走らない**(`_backends` に新インスタンスが入らない / ロードスレッドが起動しない)
- [ ] 変更後の Start(`start_pipeline_async`)で未ロード分がロードされて起動する(既存の押下時ロードで吸収)
- [ ] text_only の TTS/Output 変更でも例外なく evict + INIT のみ(特例分岐撤去の確認)
- [ ] 旧テスト「バックエンド変更時の単一レイヤ再ロード」は新挙動の検証に書き換え(消さない)

## Phase 2: 認証状態の静的計算と表示
### CredentialsService.get_auth_state(純ロジック)
- [ ] requires_credentials=False の backend → NOT_REQUIRED
- [ ] 未登録 backend / spec 無し → NOT_REQUIRED
- [ ] spec のキーが 1 つでも未保存 → MISSING
- [ ] 全キー保存済み + verified=False → UNVERIFIED
- [ ] 全キー保存済み + verified=True → VERIFIED

### gui/logic/auth_display(固定文字列)
- [ ] MISSING → ("Missing Credentials", 赤)
- [ ] UNVERIFIED → ("Not Verified", 琥珀)
- [ ] NOT_REQUIRED / VERIFIED → None(通常表示に委譲)
- [ ] palette: ModelStatus.MISSING_CREDENTIALS に赤が定義されている

### ready_state(固定文字列)
- [ ] auth=MISSING のレイヤあり → toggle 無効 + 「認証情報未設定」
- [ ] auth=UNVERIFIED のレイヤあり → toggle 無効 + 「認証未検証」
- [ ] instance 状態 MISSING_CREDENTIALS でも従来どおり無効(後方互換)
- [ ] 優先順位: MISSING > UNVERIFIED > DOWNLOADING > PROCESS未選択 > 通常
- [ ] text_only / absorbed のレイヤの auth は判定対象外

### 配線(shim / smoke)
- [ ] SettingsPanel._apply_status: auth=MISSING のレイヤは INIT でも "Missing Credentials"(赤)表示
- [ ] auth=UNVERIFIED + instance LOADED → "Not Verified"(琥珀)が優先
- [ ] 吸収(空表示)/(なし)の上書きは auth より優先
- [ ] verify_and_save_credentials 成功 → settings イベント(credentials)が emit される
- [ ] invalidate_verification → 同イベント emit
- [ ] 認証成功後の後処理が evict + INIT になっている(reload しない)

## Phase 3: ロックエンジン再構成
- [ ] 構築中(_create ブロック中)でも set_setting の evict が**待たずに**完了する
      (イベントで構築を堰き止め、別スレッドの evict がタイムアウトなしで返ることを検証)
- [ ] 構築中に evict(世代+1)→ 構築完了結果は破棄され、最新選択がロードし直される(last-write-wins)
- [ ] 同一レイヤの並行ロード要求は二重構築にならない(1 回の構築を共有)
- [ ] 構築失敗時: in-flight が解除され NOT_DOWNLOADED が emit される(既存挙動の温存)
- [ ] load_models の冪等性(既ロードはスキップ)が維持される
- [ ] stop 後もバックエンド常駐(既存テストの温存)

## 回帰(全 Phase 共通)
- [ ] 既存 small スイート全件 pass(1,257 件 + 本ブランチ追加分)
