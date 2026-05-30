# knownRisks2: 一次解消後に浮上したリスク(第二段階)

[knownRisks.md](knownRisks.md) の R-1〜R-9 を解消する過程で出てきた**派生リスク**を整理する。
最初のリスト作成時には見えていなかったが、設計を 1 段詰めたことで具体化した。

軽微なもの(命名規則のブレ、ログ表示の更新頻度 等)はここに含めない。

優先度は **高 = Phase A 着手前に方針を固めたい / 中 = Phase 進行中に注意で対処 / 低 = なし(軽微は除外済み)**。

---

## 高優先度

### R2-1. `ModelStatus.DOWNLOADING → LOADING` 遷移の責任分担が曖昧 [解消 2026-05-29]
- **問題**: R-3 解消方針で `DOWNLOADING` を導入したが、誰がいつこの状態をマークするのかが未定義
- **解消方針**: **(a) キャッシュ事前判定 + 分散管理**を組み合わせる
  - **状態の所有を backend 側に移す**: 各 backend が自分の `ModelStatus` を保有(従来 `AppController._model_status` dict にあったもの)
  - **backend がロード処理内で状態を更新**: キャッシュ未 DL なら `DOWNLOADING` → DL 完了で `LOADING` → メモリ展開完了で `LOADED`
  - **判定は backend 内で `cache_check` モジュールを利用**:既存の `huggingface_hub.try_to_load_from_cache` を使う
  - **AppController は購読者**: backend の状態変化を notify で受け取り、UI に re-broadcast するだけ(BackendManager 等の新クラスは作らない)
- **設計上の含意**:
  - 「backend 内部の事情(状態 / エラー / 現モデル名 / device / compute_type)」は backend に閉じる
  - 「発話を跨いだ集約(layer 別処理時間 deque)」は AppController が持つ
  - この分離で R2-2(AppController 肥大化)も部分的に解消(`_model_status` dict が消える)

### R2-2. `AppController` の責任肥大化(God Object 化) [対処方針確定 2026-05-29 / pendList 移送]
- **問題**: 既に大きい `AppController` に Phase A で以下が追加される
  - layer 別 `load_model_layer(layer)` API
  - status 変化の multi-listener 機構
  - layer 別 直近処理時間リングバッファ
- **対処方針**: (c) 段階的 — Phase A は集約方式、Phase B/C 完了後に責務分離をリファクタ検討
  - R2-1 の分散化(状態を backend に移管)で `_model_status` dict が消えるので、肥大化はそこそこ抑制される
  - 残る orchestration 責務の整理は将来のリファクタとして **pendList に記録**: [pendList.md](../../pendList.md) の「AppController の責務分離」エントリ参照

### R2-3. Phase A の作業量が肥大化(分割の検討) [解消 2026-05-29]
- **問題**: 当初 Phase A は「型・enum 追加程度」だったが、議論を経て積み上がった項目で 1 セッション完結が困難に
- **解消方針**: Phase A を **A1 / A2** に分割(Plan.md 側を更新済み)
  - **A1: 型 + backend 基盤**: `ModelStatus` 拡張 / `BackendCapabilities` 拡張 / `ModelInfo` dataclass / backend 基底に「自分の状態・エラー履歴・notify 機構」追加 / 既存 backend の再申告。**既存 UI/AppController の挙動は無変更**
  - **A2: AppController 統合**: backend 状態を購読 → UI に re-broadcast、`load_model_layer(layer)` 追加、layer 別 処理時間リングバッファ
- **想定セッション数**: 各 1 セッション

---

## 中優先度

### R2-4. スキーマ拡張で declarative / imperative が混在する [対処方針確定 2026-05-29]
- **問題**: R-2 解消方針で `SettingField` に callback (`options_fn` / `action_fn` / `reactive_to`) を追加 → 元の宣言的性質が弱まる
- **対処方針**: Phase C 着手時に **`layer_settings_schema.py` の docstring に薄い規約を書く**:
  - 「callback は backend 経由の取得/更新のみを行う」
  - 「UI 内部状態は触らない」
  - 「副作用の起点は backend(状態更新は notify 経由)」
- これで十分(過度な抽象化はしない)

### R2-5. 既存 backend のエラー包装の質が R-6 方針を効かせる前提に届かない [対処方針確定 2026-05-29]
- **問題**: R-6 解消方針は「backend が HTTP コード等を見て適切な `AppError` サブクラスに包んで raise する」前提だが、既存 backend は `except Exception as e: raise FatalError(...) from e` のような雑な包み方の可能性
- **対処方針**:
  - **Phase A1 の作業**: 既存 backend の `except` 節を grep し、雑な FatalError 包みなら整理
  - **`errors.py` の docstring 拡張**: 「backend 実装者は HTTP/ネットワークエラーを HTTP コード等で見て、適切な severity (RECOVERABLE / FATAL / SKIP / WARN) に分けて包むこと」を明記
  - **CLAUDE.md / Class.md にも追記**: 設計の前提として backend 実装者向けに見える形に

### R2-6. multi-listener の lifecycle 管理(リーク / 死んだ widget 参照) [対処方針確定 2026-05-29]
- **問題**: ダイアログの開閉に伴う listener の add/remove が雑だとリーク / 死んだ widget へのコールバックで `tkinter.TclError`
- **対処方針**: **`Subscription` パターン**を採用
  - `controller.subscribe_status(layer, callback) -> Subscription` で購読、`Subscription.unsubscribe()` で解除
  - 内部は `weakref` を併用し、`__del__` で自動解除されるようにフェイルセーフを入れる
  - LayerSettingsDialog の `_dismiss` で**必ず**明示 unsubscribe(`weakref` は最後の安全網)
- **対処タイミング**: Phase A2(listener 機構実装)+ Phase C(ダイアログ側で利用)

### R2-7. credentials 3 層運用の検証カバレッジが分断する [対処方針確定 2026-05-29]
- **問題**: 開発者が平文ファイル経路だけで実 API 検証 → エンドユーザの keyring 経路バグに気付かない(逆も然り)
- **対処方針**:
  - **config フラグ**: `credentials.use_local_file: bool`(既定 false = keyring 経由)を ConfigStore に追加。開発者は明示的に ON にして平文ファイル経路を使う
  - **起動時ログ**: `app.log` に「credentials backend = keyring / local-file」を 1 行記録(問題切り分けの起点になる)
  - **Phase F の検証チェックリスト**: 「keyring 経路でも動作確認したか」を必須項目に

### R2-8. 長時間 DL 中の UI 操作可能性が未定義 [対処方針確定 2026-05-29]
- **問題**: DL 中の他操作(他 layer 設定変更、backend 切替、開始ボタン押下)が未定義
- **対処方針**: Phase C 着手時に最小限の挙動を確定
  - **DL 中の同一 layer**: モデル選択 / Auto-load トグル / backend 切替を**グレーアウト**(操作不可)
  - **DL 中の他 layer**: 操作可(独立)
  - **「開始」ボタン押下時**: DL 中の layer があればステータスに「ダウンロード中のため起動できません」を出して何もしない(押せるが効果なし)
  - **DL キャンセルボタン**: 最低限実装する(`LayerSettingsDialog` に小さくボタンを置く)。R-3 で保留扱いだったが、誤クリックの救済として最小機能はあった方が良い

---

## 横断的な注意

- **R2-1 と R2-3 は Phase A 開始前に Plan を修正**して反映するのが筋
- **R2-2 / R2-5 / R2-6 は Phase 実装時の規約**として CLAUDE.md / Class.md / `layer_settings_schema.py` の docstring に書き起こす必要あり
- 完了時は項目末尾に `[解消 YYYY-MM-DD コミット-id]` を追記(knownRisks.md と同じ運用)
