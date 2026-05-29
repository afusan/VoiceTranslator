# knownRisks: feature/backend-mgmt 実装にあたっての既知リスクと未解決論点

[Plan.md](Plan.md) 着手前に挙げておくリスク。各 Phase の作業中・レビュー時に
ここを見直して、対処済み/見送りを判断する。優先度は **高 = Phase 着手前か当該
Phase 開始時に決める / 中 = Phase 進行中に直面する / 低 = 完了後の改善対象**。

---

## 高優先度(Phase 着手前に方針を固めたい)

### R-1. 既存テストへの影響範囲を見積もれていない [解消 2026-05-29]
- **問題**: Phase B(auto-load OFF)と Phase E(リトライ追加)はパイプラインの中核を触る。`test_app_controller.py` / `test_pipeline.py` / `test_main_window.py` 等は「起動時に load_models が呼ばれる」「全 LOADED で start 可能」を前提にしている可能性が高い
- **解消理由**: テスト変更時の方針が明文化され(CLAUDE.md「テスト変更時の方針」)、未知数が「予測可能な移行作業」に変わった
  - 後方互換は気にしない(prePlan の決定通り auto-load 既定 OFF)
  - 安易にテストを消さない、設計シナリオに沿って書き換える
  - 必要な初期化(`auto_load=True` 等)は fixture に追加
  - 1 件 5 秒超の test は報告(性能劣化の早期検出)
- **Phase B/E 着手時の追加タスク**: 該当テストの grep + 影響範囲リスト化(必須ではないが、最初に作ると見通しが良くなる)

### R-2. `LayerSettingsDialog` / `layer_settings_schema` の現行能力が未確認
- **問題**: Plan は「詳細ダイアログにモデル選択ドロップダウン + Load ボタン + Auto-load トグル + 直近処理時間表示」を追加する前提だが、現行のスキーマ駆動 UI が動的ボタン / 非同期操作(Load ボタン押下中の状態管理)をどこまでサポートしているか未確認
- **影響**: Phase C 着手時に「スキーマ拡張だけでは無理 → 大幅な UI リファクタリングが必要」が発覚する可能性
- **対処**: Phase C 着手の最初に `gui/layer_settings_*.py` を読んで、既存スキーマで足りるか、別途 widget 直書きが必要か判断。Plan に追記

### R-3. モデルダウンロード(DL)の扱いが未定義
- **問題**: 「Load Model ボタン」を押した時、まだダウンロードされていないモデルはどうなる? 自動 DL する? 失敗する? DL は数分〜十数分かかり、UI に進捗表示が必要
- **影響**: 現アーキテクチャに DL 進捗報告の仕組みが無い。UX が破綻するリスク
- **対処**: Phase C 着手前に決める。案: (a) Load ボタン = キャッシュ確認 + ロードのみ、未 DL は `NOT_DOWNLOADED` で停止、別ボタン「Download」を用意 (b) Load ボタンが DL も実行、進捗をステータステキストボックスに表示 — どっちか決める

### R-4. リトライ機構がパイプライン全体に与える影響
- **問題**: ASR/Translator ループ内でリトライ(3 回・指数バックオフ)すると、その間も capture は audio を捕捉し続けてキューが詰まる。既存の drop メカニズムが不適切に発火する可能性
- **影響**: 「ネット瞬断時の挙動」が予想と違う(発話が大量にドロップされる等)
- **対処**: Phase E 着手前に「リトライ中の上流の扱い」を決める。案: (a) リトライ中は capture を pause (b) キューを溢れさせず drop に任せる(現行通り)(c) リトライ間隔を 0.5s 程度に抑えて影響最小化

### R-5. `keyring` の依存と CI / 開発環境
- **問題**: keyring は OS の secret service を要求。Linux で headless / SSH / Docker では失敗する。CI も実 keyring を持たない
- **影響**: Phase D のテストが組めない / 開発者の環境差で動作差が出る
- **対処**: Phase D で `keyrings.alt`(ファイルバックエンド)か in-memory backend をテスト時のみ注入する仕組みを用意。`pyproject.toml` の依存を `[tool.uv.dev-dependencies]` で分ける検討

### R-6. `is_retryable_on_error: bool` が単純すぎる
- **問題**: 現実のクラウド API エラーは「5xx → retry すべき」「4xx → 認証問題なので retry 不要」「429 → backoff 必須」「timeout → retry すべき」「ネットワーク切断 → retry すべき」等、種類で扱いが違う。bool 1 つでは足りない
- **影響**: 4xx エラーで retry してしまうと無駄な API コール / 5xx で retry しないと耐久性が損なわれる
- **対処**: Phase A の設計時に再検討。案: (a) bool ではなく `retry_predicate(exc) -> bool` を backend が提供 (b) backend が「retry すべき例外型のリスト」を申告 — どちらか採用

---

## 中優先度(Phase 進行中に直面、対処可能)

### R-7. `ModelStatus` enum 変更の波及
- **問題**: `MISSING_CREDENTIALS` 追加で、既存コードの `if status == NOT_DOWNLOADED:` 系判定や `is_loaded` ロジックが新ステータスを取りこぼす
- **対処**: Phase A 完了直後に grep して全箇所を確認 + ステータス遷移図を作る

### R-8. backend のリソース申告値の現実性・陳腐化
- **問題**: faster-whisper の small が必要とする RAM は compute_type(int8 / float16)で違うし、ライブラリバージョンで変わる。「medium = 5GB」のような決め打ち値はすぐ古くなる
- **対処**: 申告値は「目安(approximate)」とラベルし、誤差を許容するレンジ表示(`{ram_gb_min, ram_gb_typ}`)を検討。判定もしきい値は厳しすぎない(0.9 倍程度の余裕)

### R-9. 「直近 N 件処理時間」の保持責任が不明
- **問題**: prePlan は「メモリ内のリングバッファ」と書いたが、所有者(backend? AppController? PipelineCoordinator?)を明示していない。複数所有でデータが重複する恐れ
- **対処**: Phase A or C 着手時に決める。案: `AppController` がレイヤ別の `deque(maxlen=5)` を持ち、Coordinator の `on_utterance_done` で push、UI が pull

### R-10. backend swap 中のパイプライン状態
- **問題**: ユーザが GUI で backend を切替えた瞬間、パイプラインが動作中だと何が起きるか曖昧。既存実装は backend 設定変更時に自動リロードが走るが、Coordinator が古い backend を握ったまま回り続ける可能性
- **対処**: Phase B / C 着手時に挙動を確定。案: 動作中の swap は「停止 → swap → 開始」を促す確認ダイアログ、または backend 設定は「停止中のみ変更可」にロック

### R-11. ログ・エラーメッセージ経由の API key 漏洩
- **問題**: クラウド backend が HTTP エラーを raise したとき、例外の文字列化で Authorization ヘッダや key 自体がログに出る可能性。`app.log` や `record_error()` が key を平文で残してしまう
- **対処**: Phase A or D で「エラー記録時の sensitive value マスキング」を一段噛ます。正規表現で `sk-`/`Bearer ` 系を `***` 置換

### R-12. ConfigStore のスキーマ migration
- **問題**: 新規キー(`backends_config.<backend>.auto_load` / `consents.*` 等)が増える。既存ユーザの `config.yaml` には無いので、不足キーが KeyError や default fallback で扱われる必要がある
- **対処**: Phase A or B で確認。`ConfigStore.get(*keys, default=...)` パターンが既に使われていれば概ね大丈夫だが、新規 setter 経路もテスト

---

## 低優先度(完了後・運用フェーズで改善)

### R-13. 平文 secrets ファイルの形式・名称が未確定
- **問題**: prePlan で「`.env` 以外の名前」までは決まったが、具体名(`local.secrets` 等)とフォーマット(YAML / KEY=VALUE / JSON)が未確定
- **対処**: Phase D 開始時に決める。BikesShed 化しないよう「YAML、`local.secrets`」を**仮置き**して実装、レビューで修正

### R-14. consent 粒度・取消 UI
- **問題**: consent は backend 単位だが、(a) 新 backend が後から追加された時 suppress_dialogs=ON でも consent 確認すべきか、(b) consent 取消 UI 無しでよいか、が中長期で課題
- **対処**: 当面は「suppress_dialogs ON なら全て同意扱い」「取消 UI 無し」で割り切る。要望が出たら別ブランチ

### R-15. middle テストの実行運用
- **問題**: クラウド backend の middle テストは認証 key が必要で、CI から実行不可。開発者が手動で回す運用だが、定着しないと検証されないまま放置されがち
- **対処**: `docs/forRunner/testscript.txt` に「クラウド backend 検証セクション」を追加。新 backend 追加時のレビューチェックリストに「forRunner で実機確認したか」を入れる

### R-16. 関連ドキュメントの同時更新
- **問題**: Phase C/D/E で新 UI・新ステータス・新キーが増える度に Class.md / Architecture.html / pendList.md の更新が必要だが、Plan に明記されていない
- **対処**: 各 Phase の「作業」末尾に「関連ドキュメント更新」を 1 項目入れる(Phase 着手時に Plan を更新)

### R-17. ローカル backend が複数になるケースの設計
- **問題**: Plan は主にクラウド対応を見ているが、「同じレイヤに faster-whisper と whisper.cpp」のような複数ローカル backend が現実化したとき、UI / 設定が cloud-only assumption になっていないか
- **対処**: 設計レビュー時に「cloud / local の対称性」を意識的にチェック。新 capability は cloud 固有の意味付けにしない

### R-18. SAPI に「モデル」概念が無い問題
- **問題**: SAPI のような backend にはモデル選択肢が無い。代わりに「voice」がある(Microsoft Haruka 等)。voice を「モデル相当」として扱うか、別概念として扱うか
- **対処**: Phase C で詳細ダイアログを作るときに決める。案: SAPI は `list_recommended_models()` が空、代わりに `list_voices()` を別途公開 → UI 側で SAPI 専用フィールドとして扱う

### R-19. 「Auto-load 既定 OFF」が新規ユーザに不親切な可能性
- **問題**: 初回起動 → 開始ボタン押下 → 全レイヤ「Loading...」で長時間待たされる、を新規ユーザが理解できるか?
- **対処**: 初回起動時のヒント / Tutorial / もしくは「初回だけ Auto-load ON で書いておく」案を Phase C で検討

---

## 横断的な注意

- **新規セッション開始時に本ファイルも目を通す** — Plan.md の §3 チェックリストに追記してもいい
- **各 Phase 完了時にここを見直し** — 該当リスクが顕在化したか、解消されたか、次の Phase に持ち越すかを判断
- 解消したリスクは項目末尾に **[解消 YYYY-MM-DD コミット-id]** を追記して履歴を残す
