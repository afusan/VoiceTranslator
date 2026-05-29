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

## 4. 進捗ステータス

このセクションは Phase 完了のたびに更新する。

- [ ] **Phase A**: 基盤拡張(BackendCapabilities / ModelStatus / Backend エラー保持)
- [ ] **Phase B**: ロード方式の変更(起動時 auto-load OFF、開始ボタンの動作変更)
- [ ] **Phase C**: UI 拡張(SettingsPanel バッジ + LayerSettingsDialog 詳細 + ステータステキストボックス)
- [ ] **Phase D**: 認証情報・同意 UX(keyring + 平文ファイル、同意モーダル)
- [ ] **Phase E**: 失敗時リトライ・停止挙動の実装
- [ ] **Phase F**: 実クラウド backend 追加で動作検証(オプション、別ブランチでも可)

---

## Phase A: 基盤拡張 — BackendCapabilities / ModelStatus / エラー保持

### A.1 目的
Phase B 以降が必要とする backend 側 API を全て揃える。実 backend の追加はまだしない。

### A.2 前提
- 本ブランチをチェックアウト済み
- 全テスト pass の状態
- [prePlan 論点 7](prePlan.md) の `BackendCapabilities` 拡張内容を把握

### A.3 作業
1. **`BackendCapabilities` を拡張**(prePlan 論点 7 の表通り):
   - `is_cloud: bool`(既定 False)
   - `requires_credentials: bool`(既定 False)
   - `service_name: str | None`
   - `terms_url: str | None`
   - `is_retryable_on_error: bool`(既定 False)
   - 各モデルの推奨/リソース情報を返す仕組み(後述)
2. **モデル申告 API を backend に追加**(モデル選択肢を持つ backend 用):
   - `list_recommended_models() -> list[ModelInfo]` のようなメソッド
   - `ModelInfo` は `{name, display_name, ram_gb, vram_gb_if_gpu, target_proc_ms_per_sec_audio}` 程度の dataclass
   - モデルを持たない backend(SAPI、Silero 等)は空リスト返却
3. **`ModelStatus.MISSING_CREDENTIALS` を追加**(`types.py` の enum)
4. **backend ベースクラスにエラー保持機構を追加**:
   - `record_error(exc, *, context=None)` と `get_recent_errors() -> list[ErrorRecord]`
   - 内部はリングバッファ(暫定 5 件)
   - 既存 backend を破壊しないよう、ベースクラスにのみ追加(各 backend で個別実装は不要)
5. **既存 backend (`FasterWhisperAsrBackend`、`Nllb200TranslatorBackend`、`SapiTtsBackend`、`SileroVadBackend`、その他) を新 capability で申告し直す**:
   - 主にローカル backend なので `is_cloud=False` / `requires_credentials=False` 等の既定値で十分
   - faster-whisper / NLLB は `list_recommended_models()` を実装(暫定値で OK)

### A.4 影響範囲
- `src/voice_translator/common/types.py`(BackendCapabilities / ModelStatus / 新 dataclass)
- `src/voice_translator/{asr,vad,translator,tts,capture,output}/backend.py`(ベース I/F 拡張)
- 各 backend 実装ファイル(申告内容の追加)
- `tests/test_*.py`(新フィールドが取れることの確認)

### A.5 完了条件
- 全 backend が新 capability を返せる
- `ModelStatus.MISSING_CREDENTIALS` が enum に存在
- backend ベースが `record_error` / `get_recent_errors` を提供
- `pytest -q` → all pass(既存テストは無変更で通ること)
- 新規追加テスト 5〜10 件

### A.6 次セッションへの引き継ぎ
- 進捗ステータスの Phase A にチェック
- 「Phase B から開始可能」と書く
- 追加した capability の例(faster-whisper の `list_recommended_models()` 等)を Plan.md にメモしておくと参照しやすい

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
2. **`LayerSettingsDialog` の大幅拡張**:
   - **モデル選択ドロップダウン**(`list_recommended_models()` から)
     - 各モデル名の隣に「目安: RAM 2GB / VRAM 1GB」「✓ 推奨 / ⚠ 重い / ✗ 不可」アイコン
   - **[Load Model] ボタン**(クリックで該当 backend だけロード実行)
   - **[Auto-load: ON/OFF] トグル**
   - **直近 5 件処理時間平均 + 目安時間**表示(該当レイヤが動作したことがあれば)
   - 「明らかにダメな容量」を選択時は警告ダイアログ
3. **ステータステキストボックス**(全レイヤのエラー集約):
   - MainWindow か ControlPanel の適切な場所に追加
   - 各レイヤの `get_recent_errors()` を定期的に取得して集約表示
   - 「どのレイヤ・どの backend で何が起きたか」を必ず明示
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
   - 内部実装: 第一 = `keyring` ライブラリ経由、第二 = 平文ファイル
   - **平文ファイルの名前を確定**(候補: `local.secrets` / `app.secrets` / `secrets.local`)
   - ファイルは `.gitignore` に追加
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
