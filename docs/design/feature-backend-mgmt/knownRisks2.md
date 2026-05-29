# knownRisks2: 一次解消後に浮上したリスク(第二段階)

[knownRisks.md](knownRisks.md) の R-1〜R-9 を解消する過程で出てきた**派生リスク**を整理する。
最初のリスト作成時には見えていなかったが、設計を 1 段詰めたことで具体化した。

軽微なもの(命名規則のブレ、ログ表示の更新頻度 等)はここに含めない。

優先度は **高 = Phase A 着手前に方針を固めたい / 中 = Phase 進行中に注意で対処 / 低 = なし(軽微は除外済み)**。

---

## 高優先度

### R2-1. `ModelStatus.DOWNLOADING → LOADING` 遷移の責任分担が曖昧
- **問題**: R-3 解消方針で `DOWNLOADING` を導入したが、誰がいつこの状態をマークするのかが未定義
  - 既存の backend ロード処理(`WhisperModel(...)`、`AutoModelForSeq2SeqLM.from_pretrained(...)`)は **DL とロードを暗黙に 1 操作で行う**
  - 外部から「今 DL 中」「DL 完了、ロード開始」を判別できない(tqdm 出力のスニファでもしない限り)
- **影響**: ステータステキストボックスに「ダウンロード中...」を出した直後に LOADED へ飛ぶ、逆に DL が終わってもステータスが DOWNLOADING のままになる、等の表示崩れ
- **検討案**:
  - (a) **キャッシュ事前判定**: AppController がロード前に `cache_check` で「未 DL = DOWNLOADING、DL 済 = LOADING に直行」を決める。最も単純
  - (b) **backend に prepare/load 2 段階 API を強要**: `backend.prepare(progress_cb)` で DL のみ、`backend.load()` でメモリ展開、と分離させる。既存 backend の改修が必要
  - (c) **huggingface_hub のフックを利用**: snapshot_download callback で進捗を取得。実装が backend ごとに違うので汎用化困難
- **私の推奨**: (a) 事前判定方式。Phase A の AppController 拡張に含める
- **対処タイミング**: Phase A 着手時に確定

### R2-2. `AppController` の責任肥大化(God Object 化)
- **問題**: 既に大きい `AppController` に Phase A で以下が**追加される予定**:
  - layer 別 `load_model_layer(layer)` API
  - status 変化の multi-listener 機構(`add_status_listener` / `remove_status_listener`)
  - layer 別 直近処理時間リングバッファ(`deque(maxlen=5)` × 6 layer)
  - 認証情報の有無判定(D で使う)
  - DL 状態の事前判定(R2-1 解消方針 (a) 採用なら)
- **影響**: AppController の責務が「GUI と内部の仲介」を超えて、状態管理 + 観測 + ライフサイクル制御まで丸抱え。テスト/保守コストが線形に増える
- **検討案**:
  - (a) **そのまま AppController に集約**: 短期的にはシンプル、長期的に肥大化
  - (b) **責務単位で別クラスに分離**: 例えば `ModelLoaderService`(ロード) / `StatusBroadcaster`(listener 管理) / `LatencyBuffer`(処理時間) を切り出し、AppController は composition で持つ
  - (c) **Phase A は (a)、後で (b) にリファクタ**: 動かしてから整理
- **私の推奨**: (c) 段階的。AppController に直接足して動かす → 動作確認後、責務単位の分離を別ブランチで
- **対処タイミング**: Phase A は (a) で進める、Phase B/C 完了後に (b) を検討

### R2-3. Phase A の作業量が肥大化(分割の検討)
- **問題**: 当初の Phase A は「型・enum 追加程度」だったが、議論を経て以下が積み上がった:
  - `BackendCapabilities` 拡張(5 フィールド)
  - `ModelInfo` dataclass 新規(7 フィールド程度)
  - `ModelStatus` 拡張(2 値追加)
  - backend ベースの `record_error` / `get_recent_errors`
  - 全 backend の capability 再申告
  - `AppController.load_model_layer`
  - status の multi-listener 機構
  - layer 別 リングバッファ
  - DL 状態の事前判定(R2-1)
- **影響**: 1 セッションで Phase A 完了が現実的でない可能性。途中で context clear が必要になり Plan 通りの「単一セッションで完結」が崩れる
- **検討案**:
  - **A1. 型と backend 側拡張**:
    - `BackendCapabilities` 拡張 / `ModelInfo` / `ModelStatus` の値追加 / 既存 backend の再申告 / `record_error` 機構
    - 既存 UI/AppController の挙動は無変更
  - **A2. AppController 側拡張**:
    - `load_model_layer` / multi-listener / リングバッファ / DL 状態の事前判定
- **私の推奨**: A1 / A2 に分割。A1 → 動作確認 → A2 の流れ
- **対処タイミング**: Phase A 着手前(= Plan 修正)

---

## 中優先度

### R2-4. スキーマ拡張で declarative / imperative が混在する
- **問題**: R-2 解消方針で `SettingField` に `options_fn`(callable) / `action_fn`(callable) / `reactive_to`(状態購読対象) を追加する。元々宣言的だったスキーマに callback がぶら下がる
- **影響**: 「スキーマだけ読めば UI の全貌が分かる」性質が弱まる。callback の lifecycle(いつ呼ばれて何を読み書きするか)を別途規約化しないと読みにくい
- **対処**: Phase C 着手時に「callback は backend 経由の取得・更新のみ、UI 内部状態は触らない」のような薄い規約を `layer_settings_schema.py` の docstring に書く。それで十分

### R2-5. 既存 backend のエラー包装の質が R-6 方針を効かせる前提に届かない
- **問題**: R-6 解消方針は「backend が HTTP コード等を見て適切な `AppError` サブクラス(`FatalError` / `RecoverableError` / `SkipError` / `WarnError`)に包んで raise する」を前提とする。だが既存 backend は `except Exception as e: raise FatalError(...) from e` のような雑な実装が多い可能性
- **影響**: クラウド backend を新規実装する人がこの規約に気付かないと、すべて FATAL に倒れてリトライが効かない
- **対処**:
  - 既存 backend の except 節を Phase A の作業として grep + 必要なら整理
  - `AppError` のドキュメント(CLAUDE.md / Class.md / `errors.py` の docstring)に「backend 実装者は HTTP コード等を見て適切な severity に分けて包むこと」を明記
  - 新 backend テンプレートを `docs/forRunner/` に置く?

### R2-6. multi-listener の lifecycle 管理(リーク / 死んだ widget 参照)
- **問題**: LayerSettingsDialog が複数同時に開かれる、開閉が頻繁に起きる場合、listener の add/remove を確実にやらないと
  - メモリリーク(閉じたダイアログへの参照が残る)
  - 死んだ widget へのコールバックで例外(`tkinter.TclError: invalid command name`)
- **影響**: 散発的な GUI 不安定化、デバッグが困難
- **対処**: Phase A / C 実装時に context manager パターン or `weakref` を採用
  - 案: `controller.subscribe_status(layer, callback) -> Subscription` を返し、`Subscription.unsubscribe()` を確実に呼ぶ(`__del__` でも自動解除されるよう weakref を併用)
  - ダイアログの `_dismiss` で必ず unsubscribe

### R2-7. credentials 3 層運用の検証カバレッジが分断する
- **問題**: R-5 解消方針で「テスト = fake keyring / 開発者ローカル = 平文ファイル / エンドユーザ = keyring(fallback 平文)」と分けた。だが
  - 開発者が「平文ファイル経路」だけで実 API 検証 → エンドユーザの「keyring 経路」がバグっていても気付かない
  - 逆も然り
- **影響**: 「ローカルでは動いた、配布版で動かない」事故
- **対処**:
  - `local.secrets`(仮)に切り替えるためのフラグを明示的に config に持つ(`credentials.use_local_file: true`)。開発者は ON、配布版は OFF が既定
  - 起動時 app.log に「credentials = keyring / local-file」のどちらを使ったかを記録(問題切り分け時に助かる)
  - Phase F の検証チェックリストに「keyring 経路でも動作確認したか」を入れる

### R2-8. 長時間 DL 中の UI 操作可能性が未定義
- **問題**: DL に数分〜十数分かかる場合、その間ユーザが何をしていいかが未定義
  - 「開始」ボタンを押せる(R-3 方針)が、DL 中の layer があるとパイプライン起動できない → 押して何が起きる?
  - 他 layer の設定変更は許可?
  - 別 backend に切り替えたら DL は中断? それとも続行?
  - DL のキャンセル機能は無いが、間違って medium 押して 5 分待つはずが large-v3 押した、を救済できない
- **影響**: ユーザが詰まる、サポート負担
- **対処**: Phase C 着手時に最小限の挙動を定義
  - 案: DL 中は同一 layer の設定/モデル変更を無効化(grayed out)、他 layer は OK
  - 案: DL キャンセルボタンを `LayerSettingsDialog` に置く(R-3 では保留したが、最低限あった方が良い)
  - 「開始」ボタンを押したとき DL 中の layer があれば、ステータスに「DL 中の layer があるためまだ起動できません」を出して何もしない

---

## 横断的な注意

- **R2-1 と R2-3 は Phase A 開始前に Plan を修正**して反映するのが筋
- **R2-2 / R2-5 / R2-6 は Phase 実装時の規約**として CLAUDE.md / Class.md / `layer_settings_schema.py` の docstring に書き起こす必要あり
- 完了時は項目末尾に `[解消 YYYY-MM-DD コミット-id]` を追記(knownRisks.md と同じ運用)
