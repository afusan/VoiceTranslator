# feature/backend-mgmt — Plan

> ブランチ: `feature/backend-mgmt`
> 起票日: 2026-05-29
> 関連: [prePlan.md](prePlan.md)(全論点の決定根拠) / [../Architecture.html](../Architecture.html) / [../Class.md](../Class.md) / [../pendList.md](../pendList.md)

## 1. 目的

レイヤ内のバックエンド/モデル/パラメータ調整 + 認証情報サポート + クラウド backend 対応 + 失敗時のリトライ/停止/可視化、を一式まとめて実装する。

各論点の議論詳細・決定根拠は [prePlan.md](prePlan.md) を参照(本 Plan は実装計画のみ)。

## 2. 全体方針

- **先にソフト構造を整え(Phase A〜E)**、その上で実 backend / モデルを追加(Phase F 以降)
- 各 Phase は **単一セッションで完結する粒度** に分割
- Phase 完了時に小さくコミット → ユーザレビュー → マージ → 次 Phase 着手
- **コンテクストクリア前提** — 各 Phase に「前提 / 作業 / 影響範囲 / 完了条件 / 次への引き継ぎ」を明示

## 3. 新規セッション開始時のチェックリスト

新しい Claude セッションで本ブランチの作業を再開する時に、まずこの節を読む:

1. **ブランチ確認**: `git branch --show-current` が `feature/backend-mgmt` であること
2. **テストが通る状態か**: `py -m uv run pytest -q` → all pass
3. **進捗確認**: 下記「§4 進捗ステータス」を見て、次に着手する Phase を特定
4. **当該 Phase セクションを読む**:
   - **前提**: 完了している Phase / 設計の決定事項
   - **作業**: 具体的なタスク
   - **完了条件**: ここまでできれば Phase 完了
5. **設計の決定根拠が必要なときは** [prePlan.md](prePlan.md) の該当論点を参照
6. **既存コード理解が必要なら**: `src/voice_translator/common/{app_controller,backend_registry,backend_setup,config_store}.py` を読む
7. **着手前にリスク確認** — [knownRisks.md](knownRisks.md)(初期リスク R-x)と [knownRisks2.md](knownRisks2.md)(派生リスク R2-x)の両方を見る

## 4. 進捗ステータス

このセクションは Phase 完了のたびに更新する。

- [x] **Phase A1**: 型 + backend 基盤(状態・エラー履歴・notify を backend 側に集約) — 完了 2026-05-29
- [x] **Phase A2**: AppController 統合(状態購読 + layer 別 load + 処理時間 buffer) — 完了 2026-05-29
- [x] **Phase B**: ロード方式の変更(起動時 auto-load OFF、開始ボタンの動作変更) — 完了 2026-05-29
- [x] **Phase C**: UI 拡張(hw_info + schema 拡張 + dialog dispatch + ステータステキストボックス) — 完了 2026-05-29(☁ バッジは Phase F の実 cloud backend 追加と同時)
- [ ] **Phase D**: 認証情報・同意 UX(keyring + 平文ファイル、同意モーダル) ← **次の着手**
- [ ] **Phase E**: 失敗時リトライ・停止挙動の実装
- [ ] **Phase F**: 実クラウド backend 追加で動作検証(オプション、別ブランチでも可)

---

## Phase A1: 型 + backend 基盤 — 状態とエラーを backend 側に集約

### A1.1 目的
状態・エラー履歴・notify 機構を **backend 側に集約**する(従来 AppController が持っていた `_model_status` を分散化)。既存 UI / AppController の挙動は無変更で、後続 Phase が乗る基盤を整える。

### A1.2 前提
- 本ブランチをチェックアウト済み、全テスト pass
- [prePlan 論点 7](prePlan.md) + [knownRisks2 R2-1](knownRisks2.md) の分散管理方針を把握

### A1.2.5 着手前に読むべき既存コード(参照ファイル)
- `src/voice_translator/common/types.py` — `BackendCapabilities` / `ModelStatus` / `LayerKind` 定義の現状
- `src/voice_translator/common/errors.py` — `AppError` 階層(`FatalError` / `RecoverableError` / `SkipError` / `WarnError`)の定義。docstring 拡張対象
- `src/voice_translator/common/cache_check.py` — モデルキャッシュ判定の既存関数群(`check_faster_whisper` 等)。DOWNLOADING 判定で再利用
- `src/voice_translator/common/app_controller.py` — `_model_status` の現状実装(A2 で簡略化、A1 では触らない)
- `src/voice_translator/{asr,vad,translator,tts,capture,output}/backend.py` — 各レイヤの抽象 I/F。capability/notify を生やす対象
- `src/voice_translator/asr/faster_whisper_backend.py` 等 — 既存 backend の `except` 節 grep 対象(R2-5)
- `tests/test_*.py` — 既存テストの fixture パターンを把握(テスト変更時の方針に従う)

### A1.3 作業
1. **`BackendCapabilities` を拡張**(prePlan 論点 7 + R-6 解消方針):
   - `is_cloud: bool`(既定 False)
   - `requires_credentials: bool`(既定 False)
   - `service_name: str | None`
   - `terms_url: str | None`
   - ~~`is_retryable_on_error: bool`~~ ← 不要(R-6 で削除確定。backend が `AppError` 階層に包んで raise する)
2. **`ModelInfo` dataclass 新規**:
   - `{name, display_name, ram_gb, vram_gb_if_gpu, download_size_gb, target_proc_ms_per_sec_audio}`
   - `download_size_gb` は DL 中の表示用(R-3)。不明なら `None`
3. **`ModelStatus` 拡張**(`types.py` の enum):
   - `MISSING_CREDENTIALS`(認証情報不足、論点 1)
   - `DOWNLOADING`(モデル DL 中、R-3)
   - 想定遷移: `INIT → DOWNLOADING → LOADING → LOADED`(DL 不要なら DOWNLOADING スキップ)
4. **backend ベースクラスに「自分の状態・エラー履歴・notify 機構」を追加**(R2-1 解消方針):
   - **状態**: `current_status: ModelStatus` を保有、`get_status() -> ModelStatus` で公開
   - **状態変化通知**: `subscribe(callback) -> Subscription` で購読、`Subscription.unsubscribe()` で解除(R2-6 の Subscription パターン)
   - **エラー履歴**: `record_error(exc, *, context=None)` で記録、`get_recent_errors() -> list[ErrorRecord]` で取得。内部リングバッファ(暫定 5 件)
   - **モデル一覧**: `list_recommended_models() -> list[ModelInfo]`(モデル選択肢を持つ backend 用、無い backend は空リスト)
5. **既存 backend を新仕様に追従**:
   - 各 backend が `BackendCapabilities` の新フィールドを正しく申告
   - faster-whisper / NLLB は `list_recommended_models()` を実装
   - ロード処理内で `current_status` を `DOWNLOADING → LOADING → LOADED` と更新(キャッシュ判定は `cache_check` モジュール流用、R-3 / R2-1)
   - **R2-5 対応**: 既存 backend の `except` 節を grep して、雑な `FatalError` 包みを HTTP コード / 例外型で `RecoverableError` / `SkipError` に分ける
6. **ドキュメント追記**:
   - `errors.py` docstring に「backend 実装者は HTTP/ネットワークエラーを適切な severity に分けて包むこと」を明記(R2-5)
   - `Class.md` 関連箇所も更新

### A1.4 影響範囲
- `src/voice_translator/common/types.py`(BackendCapabilities / ModelStatus / ModelInfo)
- `src/voice_translator/{asr,vad,translator,tts,capture,output}/backend.py`(ベース I/F)
- 各 backend 実装ファイル
- `src/voice_translator/common/errors.py`(docstring)

### A1.5 完了条件
- 全 backend が新 capability + 状態管理 + notify 機構を持つ
- `ModelStatus` enum に新 2 値が追加
- 既存挙動(GUI / Coordinator / AppController)は無変更で動作 → `pytest -q` 全 pass(初期化手順の追加だけで吸収可能なら fixture 修正で対応、テスト消すのは NG)
- 新規テスト 5〜15 件

### A1.6 次セッションへの引き継ぎ
- 進捗ステータスの Phase A1 にチェック
- 「Phase A2 から開始可能」と書く

---

## Phase A2: AppController 統合 — backend 状態の購読 + layer 別 load + 処理時間 buffer

### A2.1 目的
A1 で backend 側に集約した状態を AppController が購読し、UI に re-broadcast する。layer 単位ロードと処理時間バッファも追加。

### A2.2 前提
- Phase A1 完了
- backend が `subscribe(callback)` / `get_status()` を提供している状態

### A2.3 作業
1. **AppController から `_model_status` dict を削除**:
   - 状態の真実は backend 側にあるので、AppController が dict を持つ必要なし
   - `get_model_status(layer)` は内部で `self._backends[layer].get_status()` を呼ぶよう変更
2. **backend 状態の購読**:
   - backend ロード時に AppController が `backend.subscribe(self._on_backend_status_changed)` を呼ぶ
   - 受け取った変化を `on_status_change` callback として UI 側に re-broadcast
3. **multi-listener 機構**(R2-6):
   - UI 側からの購読を受け付ける `add_status_listener(callback) -> Subscription` / `remove_status_listener` を追加
   - 内部で複数 listener を保持、状態変化時に全 listener に dispatch
4. **layer 単位ロード**:
   - `load_model_layer(layer)` 公開メソッド(既存 `_safe_load_layer` を整理 or 新規)
   - 該当 backend だけをロードするフロー
5. **layer 別 直近処理時間リングバッファ**:
   - `deque(maxlen=5)` × 6 layer を AppController が保有
   - 既存 `_handle_utterance_done(record)` 内で push
   - `get_recent_durations(layer) -> list[float]` で UI に提供

### A2.4 影響範囲
- `src/voice_translator/common/app_controller.py`
- `tests/test_app_controller.py` 等(挙動変更に追従、テスト消すのは NG / シナリオを温存して書き換え)

### A2.5 完了条件
- AppController が backend 状態を購読、UI に re-broadcast できる
- `load_model_layer(layer)` で個別ロード可能
- `get_recent_durations(layer)` で直近 5 件の処理時間を取得可能
- `pytest -q` 全 pass

### A2.6 次セッションへの引き継ぎ
- 進捗ステータスの Phase A2 にチェック
- 「Phase B から開始可能」と書く

---

## Phase B: ロード方式の変更 — auto-load 既定 OFF、開始ボタン挙動

### B.1 目的
モデル選択肢が増えても起動が重くならないように、ロード制御をユーザに渡す。

### B.2 前提
- Phase A 完了(新 ModelStatus と backend API がある)

### B.3 作業
1. **ConfigStore 拡張**:
   - `backends_config.<backend>.auto_load: bool`(既定 False)を許容(全 backend 共通の新キー)
   - `consents.*` の場所もここで先に予約(Phase D で使う): `consents.suppress_dialogs: bool` 等
2. **AppController の起動シーケンス変更**:
   - 既存 `load_models_async` の自動発火(MainWindow 起動時)を**廃止**
   - 各レイヤの `auto_load=True` のときだけ、起動時にそのレイヤだけロード
3. **`start_pipeline_async` の挙動変更**:
   - 「全 LOADED でないと開始ボタン無効」を撤回
   - 押された時点で未ロード backend があれば、Loader スレッドでロード → 完了後に Coordinator 起動
   - `MISSING_CREDENTIALS` レイヤがあれば事前 gate(ロードしても意味なし)
4. **既存テストの修正**:
   - 「起動時に load_models が呼ばれる」前提のテストが壊れる可能性 → 修正
   - 新規テストで「未ロードでも start を押せる」「ロード完了後 Coordinator 起動」を確認

### B.4 影響範囲
- `src/voice_translator/common/app_controller.py`
- `src/voice_translator/common/config_store.py`(DEFAULT_CONFIG)
- `src/voice_translator/gui/main_window.py`(起動時 auto-load 呼び出しの削除)
- `src/voice_translator/gui/control_panel.py`(開始ボタンの enable 条件変更)
- `tests/test_app_controller.py`、`tests/test_main_window.py`(該当箇所)

### B.5 完了条件
- 起動時にモデルロードが走らない(全レイヤ INIT のまま)
- `auto_load=True` のレイヤだけ起動時にロードされる
- 開始ボタンが常時押せる(`MISSING_CREDENTIALS` を除く)
- 開始ボタン押下時、未ロードがあれば自動ロード → パイプライン起動
- `pytest -q` → all pass

### B.6 次セッションへの引き継ぎ
- 進捗にチェック
- Phase C で「UI 詳細ダイアログに Load ボタンと Auto-load トグルを追加する」と書く

---

## Phase C: UI 拡張 — SettingsPanel + LayerSettingsDialog + ステータステキストボックス

### C.1 目的
ユーザがモデル選択・ロード操作・状態確認を GUI でできるようにする。

### C.2 前提
- Phase A, B 完了(backend が capability / model 一覧を返せる、ロードが手動制御可能)

### C.3 作業
1. **SettingsPanel への追加**:
   - クラウド backend のプルダウン項目に **☁ バッジ** を表示(`is_cloud=True` を見る)
   - LOADED 状態の表示は維持
2. **`LayerSettingsDialog` の大幅拡張**(スキーマ拡張方針、knownRisks R-2 参照):
   - **スキーマ側の拡張**: `FieldType` に `"dropdown"` / `"toggle"` / `"button"` / `"label_readonly"` 等を追加、`SettingField` に `options_fn` / `action_fn` / `reactive_to` 属性を追加
   - **ダイアログ側の dispatch 拡張**: 新 `FieldType` ごとに `_add_<type>_row` を追加(既存の `_add_field_row` と並列に多態化)
   - 追加する画面要素:
     - **モデル選択ドロップダウン**(`list_recommended_models()` から)、各モデル名の隣に「目安: RAM 2GB / VRAM 1GB」「✓ 推奨 / ⚠ 重い / ✗ 不可」アイコン
     - **[Load Model] ボタン**(クリックで `AppController.load_model_layer(layer)` 呼び出し)
     - **[Auto-load: ON/OFF] トグル**
     - **直近 5 件処理時間平均 + 目安時間**表示(`AppController.get_recent_durations(layer)` から)
     - 「明らかにダメな容量」を選択時は警告ダイアログ
   - **状態変化への追随**: ダイアログ開閉時に `AppController.add_status_listener` / `remove_status_listener` で購読し、Load ボタンや状態ラベルを動的更新(`widget.after(0, ...)` でメインスレッドにマーシャル)
3. **ステータステキストボックス**(全レイヤのエラー集約 + 進捗表示):
   - MainWindow か ControlPanel の適切な場所に追加
   - 各レイヤの `get_recent_errors()` を定期的に取得して集約表示
   - 「どのレイヤ・どの backend で何が起きたか」を必ず明示
   - **DL 中の進捗表示**(R-3): `DOWNLOADING` 状態のレイヤがあれば「`<layer> (<backend> / <model>): モデルダウンロード中 (~XGB)。しばらくお待ちください`」を表示。`ModelInfo.download_size_gb` を参照、`None` ならサイズ非表示
4. **hw 検出ヘルパ**:
   - `common/hw_info.py` 等で `detect_hw() -> HwInfo(ram_gb, has_gpu, vram_gb)` を提供
   - `psutil` + `torch.cuda` で取得

### C.4 影響範囲
- `src/voice_translator/gui/{settings_panel,layer_settings_dialog,layer_settings_schema,main_window,control_panel}.py`
- `src/voice_translator/common/hw_info.py`(新規)
- `src/voice_translator/common/app_controller.py`(直近処理時間のリングバッファ追加 + UI 連携メソッド)
- `tests/test_*` 各種(新 UI コンポーネントは middle 寄りになる可能性、small で済むものは small で)

### C.5 完了条件
- ユーザが GUI でモデル選択・ロード・auto-load 切替できる
- 詳細ダイアログに目安リソースと直近処理時間が出る
- ステータステキストボックスにエラーが集約表示される
- クラウド backend に ☁ バッジが出る
- `pytest -q` → all pass

### C.6 次セッションへの引き継ぎ
- 進捗にチェック
- Phase D で「認証情報入力フィールドを LayerSettingsDialog にもう一つ追加する」と書く

---

## Phase D: 認証情報・同意 UX — keyring + 平文ファイル + 同意モーダル

### D.1 目的
クラウド backend を安全に追加できる基盤を整える(認証保管 + 同意取得)。
**この Phase 完了時点では、実クラウド backend はまだ無い**(Phase F で追加)。

### D.2 前提
- Phase A, B, C 完了
- `requires_credentials=True` を返す capability の仕組みが Phase A で動いている
- LayerSettingsDialog が Phase C で拡張済み

### D.3 作業
1. **資格情報保管モジュール** `common/credentials.py`(新規):
   - `get_credential(backend_name, key_name) -> str | None`
   - `set_credential(backend_name, key_name, value) -> None`
   - `delete_credential(backend_name, key_name) -> None`
   - 内部実装: 起動時に keyring 生存確認 → 可なら keyring、不可なら平文ファイル(prePlan 論点 1 / R-5)
   - **平文ファイルの名前を確定**(候補: `local.secrets` / `app.secrets` / `secrets.local`)
   - ファイルは `.gitignore` に追加
   - **3 層運用**(R-5):
     - テスト: `keyring.set_keyring(InMemoryKeyring())` / `FailKeyring()` で test double 注入
     - 開発者ローカル(実 API 検証用): 平文ファイル直接書き込みで OK(keyring 経由しない)
     - エンドユーザ: keyring(第一)→ 平文ファイル(fallback)
   - `tests/_fixtures.py` に `fake_keyring` / `failing_keyring` の fixture を追加
2. **LayerSettingsDialog に認証入力フィールド**:
   - backend が `requires_credentials=True` のとき、key 入力フィールドを動的表示
   - 入力値を `credentials.set_credential()` で保管
   - 既に保管されている場合は `••••••••` でマスク表示、変更時のみ書き換え
3. **同意ダイアログ** `gui/consent_dialog.py`(新規):
   - prePlan 論点 2 のひな形通り(送信先 / 送信データ / 利用規約 / 「今後表示しない」チェックボックス)
   - SettingsPanel の backend プルダウンで `is_cloud=True` の項目が選ばれたとき、`set_setting` 呼ぶ前に発火
   - 「同意して使用」→ `consents.<backend>: true` を永続化して進行
   - 「キャンセル」→ プルダウン表示値を元に戻し、`set_setting` は呼ばない
   - 「今後表示しない」ON → `consents.suppress_dialogs: true` を永続化
4. **`MISSING_CREDENTIALS` 状態の発火**:
   - 該当 backend のロード時に key 取得 → 無ければ `MISSING_CREDENTIALS` 状態を立てる
   - UI 側で開始ボタンを gate(Phase B の例外処理)

### D.4 影響範囲
- `src/voice_translator/common/credentials.py`(新規)
- `src/voice_translator/gui/{layer_settings_dialog,settings_panel,consent_dialog}.py`
- `src/voice_translator/common/app_controller.py`(MISSING_CREDENTIALS の発火と start gate)
- `pyproject.toml`(`keyring` 依存追加)
- `.gitignore`(平文ファイル名追加)
- `tests/test_credentials.py` 新規 + 既存テストへの影響確認

### D.5 完了条件
- 平文ファイル名が確定し `.gitignore` 済み
- `credentials.get/set/delete` が keyring と平文ファイル両方で動く
- LayerSettingsDialog で key 入力できる
- クラウド backend を選ぶと同意モーダルが出る
- 「キャンセル」でプルダウンが元に戻る
- 「今後表示しない」が永続化される
- `pytest -q` → all pass

### D.6 次セッションへの引き継ぎ
- 進捗にチェック
- Phase E は「リトライ/停止挙動の実装」と書く

---

## Phase E: 失敗時リトライ・停止挙動

### E.1 目的
backend エラーへの統一的な挙動(リトライ・停止・ログ集約)を実装する。

### E.2 前提
- Phase A〜D 完了(`is_retryable_on_error` capability、エラー保持機構、ステータステキストボックスが揃っている)

### E.3 作業
1. **PipelineCoordinator の各ループでリトライ機構**:
   - backend 呼び出しで例外が出たら、`is_retryable_on_error=True` なら最大 3 回まで指数バックオフ(0.5s / 1.0s / 2.0s 等)で再試行
   - 全失敗(or `is_retryable_on_error=False`)なら復帰不能として扱う
2. **復帰不能エラー時のパイプライン停止**:
   - `stop_event` をセットして全スレッド終了
   - 起きたエラーは backend の `record_error()` に積む(C で実装したリングバッファ)
   - `on_fatal` コールバックでステータステキストボックスへも反映
3. **ステータステキストボックスへのエラー反映**:
   - レイヤ状態(`ModelStatus`)変化時、または backend に新エラーが追加された時に再フェッチして表示更新
   - 「どのレイヤの何が起きた」を 1 行ずつ表示

### E.4 影響範囲
- `src/voice_translator/common/pipeline.py`(各ループのリトライ実装)
- `src/voice_translator/common/error_handler.py`(`is_retryable_on_error` を参照する分岐)
- `src/voice_translator/gui/main_window.py` または ControlPanel(ステータステキストボックスの更新ロジック)
- `tests/test_pipeline*.py`(リトライ動作・停止動作の検証)

### E.5 完了条件
- リトライ可能 backend が `RECOVERABLE` 系例外を吐いてもパイプラインが回り続ける(3 回までリトライ)
- 復帰不能エラーでパイプライン全停止 + ステータス領域にエラーが出る
- ローカル backend は即停止
- `pytest -q` → all pass

### E.6 次セッションへの引き継ぎ
- 進捗にチェック
- Phase F は **オプション**。実クラウド backend を 1 つ追加して動作検証する場合に着手

---

## Phase F: 実クラウド backend 追加で動作検証(オプション)

### F.1 目的
Phase A〜E で整備した構造が実環境で動くことを確認する。

### F.2 前提
- Phase A〜E 完了
- 追加する backend を 1 つ決める(候補: OpenAI Whisper API ASR / DeepL Translator / OpenAI TTS 等)

### F.3 作業
1. 新 backend クラス追加(例: `src/voice_translator/asr/openai_api_backend.py`)
   - `is_cloud=True`、`requires_credentials=True`、`is_retryable_on_error=True` を申告
   - `service_name`、`terms_url`、`list_recommended_models()` を実装
2. `backend_setup.py` で `BackendRegistry` に登録
3. `docs/forRunner/` に検証手順を追加(認証付きで `runner_*` を回す例)
4. middle テスト 1 件追加(`@pytest.mark.middle` + 認証 key 必要、CI からは除外)
5. 実機で動作確認: 認証 → モデル選択 → 同意 → ロード → パイプライン起動 → 翻訳 → エラー注入時のリトライ動作

### F.4 影響範囲
- 新 backend ファイル
- `src/voice_translator/common/backend_setup.py`
- `docs/forRunner/testscript.txt`(検証コマンド追加)
- `tests/test_<backend>.py` 新規

### F.5 完了条件
- 新 backend で実翻訳/書き起こしが通る
- 認証フロー、同意モーダル、ロードボタン、ステータス表示が一通り動く

### F.6 次セッションへの引き継ぎ
- ブランチを master にマージ → `docs/design/feature-backend-mgmt/` を `docs/design/done/feature-backend-mgmt/` に移動

---

## 5. 設計上の重要メモ(忘れがちな項目)

### 5.1 既存テストへの非破壊性
本ブランチは既存パイプラインに大きく手を入れる。各 Phase で `pytest -q` を**毎回必ず**通すこと。特に:
- Phase B: `test_app_controller.py` の load_models 関連
- Phase E: `test_pipeline.py` のエラーパス

### 5.2 配布方針(CLAUDE.md)との整合
- CPU を floor、GPU は bonus。新 backend(クラウドも含む)が増えても **コードパスは 1 本**
- `--extra cpu` / `--extra cuda` の区別は維持
- 新規依存(`keyring` 等)は CPU/CUDA 両方で動くこと

### 5.3 commit 規約(CLAUDE.md)
- 個別ファイル名・テスト結果・行数を書かない
- ふるまい/抽象レベルでの変更内容と「なぜ」を中心に書く
- Phase ごとに 1〜数コミット程度に収めるのが目安

### 5.4 prePlan との対応
本 Plan の各 Phase が prePlan のどの論点を実装しているかの早見表:

| Phase | 対応 prePlan 論点 |
|---|---|
| A | 論点 7(capability 拡張)、論点 9(MISSING_CREDENTIALS) |
| B | 論点 5+新(ロード方式)の構造部分 |
| C | 論点 4+5+新(UI 詳細)、論点 1(ステータステキストボックス) |
| D | 論点 1(認証保管)、論点 2(同意 UX) |
| E | 論点 3(リトライ・停止・観測) |
| F | 動作検証(実 backend 追加) |

### 5.5 マージ運用
- 各 Phase 完了で**ユーザレビュー後にマージ**(CLAUDE.md「マージはユーザの依頼があってから」)
- `--no-ff` 必須
- リモートには触らない

---

## 6. testPlan

各 Phase のテスト項目は本 Plan 内に書いたが、より詳細なケース列挙が必要になったら [testPlan.md](testPlan.md) に分離する(各 Phase 着手時に必要に応じて作成)。
