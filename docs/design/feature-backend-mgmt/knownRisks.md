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

### R-2. `LayerSettingsDialog` / `layer_settings_schema` の現行能力が未確認 [解消 2026-05-29]
- **問題**: Plan は「詳細ダイアログにモデル選択ドロップダウン + Load ボタン + Auto-load トグル + 直近処理時間表示」を追加する前提。現状のスキーマは `int`/`float`/`str`/`bool` の 4 型(全て `CTkEntry`)しか表現できない
- **重要な訂正**: UI ライブラリ(customtkinter / tkinter)は dropdown / toggle / button / label / 動的更新 / 状態反映を全てサポートしている。問題はプロジェクト側のスキーマ抽象層が貧弱なだけ
- **解消方針**: **スキーマ拡張**(案 A 採用)
  - `FieldType` に `"dropdown"` / `"toggle"` / `"button"` / `"label_readonly"` 等を追加
  - `SettingField` に追加属性: `options_fn`(dropdown 候補生成)、`action_fn`(button 実行)、`reactive_to`(状態購読対象)
  - ダイアログに widget builder の dispatch を追加(`_add_dropdown_row` / `_add_toggle_row` / `_add_button_row` / `_add_label_row`)
- **派生タスク**(Phase A 範囲に追加):
  - AppController に `load_model_layer(layer)` 公開メソッド追加(`_safe_load_layer` を公開化 or 新規)
  - 状態変化リスナーの **multi-listener 対応**(ダイアログ複数開閉対応 or dialog 専用フック)
  - 直近処理時間のリングバッファ(AppController が layer 別 `deque(maxlen=5)` を所有、R-9 もこれで解消)

### R-3. モデルダウンロード(DL)の扱いが未定義 [解消 2026-05-29]
- **問題**: 「Load Model ボタン」を押した時、まだダウンロードされていないモデルはどうなる?
- **解消方針**: **(b) Load ボタンが DL も実行**(キャッシュ確認 → 未 DL なら DL → ロード → LOADED、を 1 ボタンで完結)
- **進捗表示**:
  - ステータステキストボックスに「`<layer> (<backend> / <model>): モデルダウンロード中 (~XGB)。しばらくお待ちください`」を表示
  - 進捗 % まで出さない(huggingface_hub の標準 tqdm は app.log に流れる)— 必要になれば後付け
- **キャッシュ**: HuggingFace 標準キャッシュ(`~/.cache/huggingface/`)に任せる。2 回目以降は DL ステップがスキップされて即ロードに進む(既存挙動を流用)
- **派生タスク**:
  - **`ModelStatus.DOWNLOADING` を追加**(Phase A の MISSING_CREDENTIALS と同じタイミングで追加)
  - 各 backend のモデル申告に **`download_size_gb: float | None`** を含める(`ModelInfo` の一部、なければ「サイズ不明」表示)
  - 既存 `cache_check.py` のキャッシュ判定 API を流用 / 必要なら整理
- **NOT_DOWNLOADED の意味の整理**: 引き続き「DL 試行したが失敗」を表す。「まだ DL してない」状態は INIT で表現(Load ボタン押下前は INIT のまま)

### R-4. リトライ機構がパイプライン全体に与える影響 [保留 2026-05-29 / pendList 移送]
- **問題**: ASR/Translator ループ内でリトライ(3 回・指数バックオフ)すると、その間も capture は audio を捕捉し続けてキューが詰まる。既存の drop メカニズムが不適切に発火する可能性
- **保留方針**: 設計段階で結論を出さず、**Phase F で実クラウド backend(DeepL 等)を繋いだ時の実機挙動で判定**
  - キュー詰まりが許容範囲なら採用継続
  - 体感が悪ければ **リトライ機構ごと撤回**(失敗即停止に倒す)
- **pendList に記載**: [pendList.md](../pendList.md) の「リトライ機構の効果検証」エントリ参照

### R-5. `keyring` の依存と CI / 開発環境
- **問題**: keyring は OS の secret service を要求。Linux で headless / SSH / Docker では失敗する。CI も実 keyring を持たない
- **影響**: Phase D のテストが組めない / 開発者の環境差で動作差が出る
- **対処**: Phase D で `keyrings.alt`(ファイルバックエンド)か in-memory backend をテスト時のみ注入する仕組みを用意。`pyproject.toml` の依存を `[tool.uv.dev-dependencies]` で分ける検討

### R-6. `is_retryable_on_error: bool` が単純すぎる [解消 2026-05-29]
- **問題**: 現実のクラウド API エラーは「5xx → retry すべき」「4xx → 認証問題なので retry 不要」「429 → backoff 必須」「timeout → retry すべき」等、種類で扱いが違う。bool 1 つでは足りない
- **解消方針**: **`is_retryable_on_error: bool` を削除**し、**既存の `AppError` severity 階層を活用する方向に変更**
  - 現状の severity は `FATAL` / `RECOVERABLE` / `SKIP` / `WARN`(CLAUDE.md / Class.md 既出)
  - **backend 側の責任**: API 呼び出しで catch した例外を、HTTP コード等を見て**適切な `AppError` サブクラスに包んで raise する**
    - 401/403 → `FatalError("auth failed")`(再認証要)
    - 429 → `RecoverableError("rate limit")`(リトライ可)
    - 5xx → `RecoverableError("server error")`
    - timeout → `RecoverableError("timeout")`
    - 4xx(400 等) → `FatalError("bad request")`(バグなのでリトライ無意味)
  - **ErrorHandler 側**: severity を見て `RECOVERABLE` のみ 3 回リトライ、`FATAL` は即停止、`SKIP` は当該発話のみ無視、`WARN` はログのみ(既存仕様)
- **メリット**:
  - 既存の severity 階層をそのまま使える(新規 enum・bool 不要)
  - backend が自分のエラー種別を最もよく知っているので、責任の所在が綺麗
  - HTTP コード等の細かい判断は backend 実装内に閉じ、ErrorHandler は汎用的に保てる
- **`BackendCapabilities` 側**: `is_retryable_on_error` フィールドは Phase A の時点で **追加しない**(削除されたまま)
- **Phase E への波及**: 「リトライ判定の仕組み」は新規追加せず、既存 `ErrorHandler.handle` の `RECOVERABLE` 分岐に「リトライ実装(3 回・指数バックオフ)」を組み込むだけ

---

## 中優先度(Phase 進行中に直面、対処可能)

### R-7. `ModelStatus` enum 変更の波及
- **問題**: `MISSING_CREDENTIALS` 追加で、既存コードの `if status == NOT_DOWNLOADED:` 系判定や `is_loaded` ロジックが新ステータスを取りこぼす
- **対処**: Phase A 完了直後に grep して全箇所を確認 + ステータス遷移図を作る

### R-8. backend のリソース申告値の現実性・陳腐化
- **問題**: faster-whisper の small が必要とする RAM は compute_type(int8 / float16)で違うし、ライブラリバージョンで変わる。「medium = 5GB」のような決め打ち値はすぐ古くなる
- **対処**: 申告値は「目安(approximate)」とラベルし、誤差を許容するレンジ表示(`{ram_gb_min, ram_gb_typ}`)を検討。判定もしきい値は厳しすぎない(0.9 倍程度の余裕)

### R-9. 「直近 N 件処理時間」の保持責任が不明 [解消 2026-05-29、R-2 と同時]
- **問題**: prePlan は「メモリ内のリングバッファ」と書いたが、所有者(backend? AppController? PipelineCoordinator?)を明示していない。複数所有でデータが重複する恐れ
- **解消方針**: **AppController が layer 別の `deque(maxlen=5)` を所有**。`_handle_utterance_done(record)` で push(既存のフックポイント)、UI(LayerSettingsDialog)は AppController に getter を生やして pull
- **Phase A 範囲**: AppController にリングバッファ + getter 追加(R-2 の派生タスクと同じ)

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
