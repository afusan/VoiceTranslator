# refactor/roles-rebalance — 計画(調査ベース)

作成: 2026-06-09。`AppController`(1,415 行)/ `SettingsPanel`(1,018 行)/ `ControlPanel`(787 行)を
読み出した上での整理。**まだ branch は切っていない**。AppController 分割の方向性は
`docs/design/append/AppControllerResponsibilities.html`(2026-06-05)に既存案あり、本計画は
**その案を採用しつつ、GUI 側の責務肥大化も同時に解消する**全体計画。

> **⚠ 上位仕様の切替(2026-06-09 追記)**: ユーザ判断により、本計画(MVC ベース)ではなく
> **MVVM での再構築**を行う方向に方針変更。新仕様は
> [`docs/design/append/AppControllerResponsibilities(MVVM).html`](../append/AppControllerResponsibilities(MVVM).html)
> を参照。本ファイル(MVC 版の計画)は **当面参考扱い** とし、実装着手は MVVM 版仕様のもとで新しい
> Plan.md(別フォルダ予定)を起こしてから行う。本ファイルの §2(現状の責務マップ)は MVVM 版でも
> そのまま読める価値があるので残置。

---

## 1. ゴール

「後付けが多くて役割分担が煩雑」「UI まわりが God オブジェクト化している」状態を
**役割ベースの分割 + 調整役(Mediator)を許容**することで整理する。

ターゲット:

| 現状クラス | 問題 |
|---|---|
| `AppController` (1,415 行) | 9 責務(UI ブリッジ / 設定 / ステータス / 認証 / Backend メタ問合せ / Backend ロード / Pipeline ライフサイクル / 発話完了処理 / 観測装置組立)を抱える |
| `SettingsPanel` (1,018 行) | バックエンド / デバイス / 言語の選択 UI + ASR/Translator/TTS の言語連動 + Capture kind 別 UI + 動作中 restart 連動 を 1 クラスに |
| `ControlPanel` (787 行) | Start/Stop + ロード/出力テストボタン + レイテンシ集約 + ステータステキストボックス + 履歴 + フォーカス状態管理 |
| `MainWindow` の widget 注入 | `MainWindow → SettingsPanel`、`MainWindow → ControlPanel`、さらに `SettingsPanel ↔ ControlPanel` の逆参照を後注入 |

ゴール(目に見える形):

- どのクラスも責務 = 1 行で言える(「設定の編集 UI」「動作開始の入口」)
- クラス間の依存は **一方向 + Observer 系の購読**で表現される(直接 widget 参照を後注入しない)
- 新機能(例: 複合バックエンド対応の UI 表記)を入れるとき、置く場所が責務テーブルから一意に決まる

---

## 2. 現状の責務マップ

### 2-1. AppController(再掲、HTML 既存案を要約)

`AppControllerResponsibilities.html` に詳細あり。9 責務:

| ラベル | 内容 |
|---|---|
| A. UI ブリッジ | `set_callbacks` / `add_status_listener` / `_emit_status` |
| B. 設定ファサード | ConfigStore ラッパ + `output_mode` 派生 + `_active_layers()` |
| C. ステータス集約 | `get_model_status` / `get_status_summary` / `get_recent_durations` |
| D. Credentials 連携 | `verify_and_save_credentials` / `is_backend_verified` |
| E. Backend メタ問合せ | `get_capture_kind` / `get_supported_input_languages` 等 |
| F. Backend ロード / eviction | `load_models` / `evict_model_layer` / `_subscribe_backend` |
| G. Pipeline ライフサイクル | `start_pipeline_async` / `stop_pipeline` / `restart_pipeline_async` |
| H. 発話完了処理 | `_handle_text_ready` / `_handle_utterance_done` / `_push_recent_durations` |
| I. 観測装置組立 | `_build_stage_dump` / Logger 群の生成 |

既存案の分割: **5 コラボレータ + Facade**(`StatusBroadcaster` / `CredentialsCoordinator` /
`BackendLoaderService` / `SettingsFacade` / `PipelineRunner`)。

### 2-2. SettingsPanel

責務がコメントで宣言されている範囲を抜き出す:

- a) バックエンド選択 UI(6 レイヤ分のプルダウン + 設定ボタン + ステータス)
- b) CAPTURE backend の kind に応じたソース UI 切替(プルダウン ↔ プロセス選択ボタン)
- c) 入力言語 / 出力言語プルダウンの構築・連動
- d) ASR / Translator / TTS backend 切替時の言語自動 fallback + 通知バナー
- e) クラウド backend 選択時の同意ダイアログ(`ConsentDialog.maybe_show`)
- f) TTS=(なし) のとき Output 行をグレーアウトする視覚連動
- g) 動作中デバイス変更時の自動 restart(`AppController.restart_pipeline_async`)
- h) ログ出力先 / 保存・再読込ボタン
- i) 3 セクション(バックエンド/デバイス/翻訳)の独立折り畳み永続化
- j) **ControlPanel への逆参照**(`set_control_panel` で後注入)を持って PROCESS 選択完了時に `refresh_ready_state()` を直叩き

→ a〜f は「バックエンドと言語の設定 UI」一塊、g 〜 j は「セッション動作中の連動」が混じっている。

### 2-3. ControlPanel

責務:

- a) Start/Stop トグル(状態機械: idle / loading / running / stopping / starting)
- b) ↻ ロードボタン / 🔊 出力テストボタン
- c) レイヤ別ステータスの集約観測 + ready state 再計算
- d) ステータステキストボックス(`get_status_summary` 表示 + 操作イベント履歴 `_gui_event_log`)
- e) 翻訳履歴の表示(`_apply_text_ready`)
- f) 平均レイテンシ表示
- g) アクセラレータ集約表示(GPU/CPU)
- h) NotificationBanner 連携(起動失敗時のエラー表示)
- i) PROCESS kind での「プロセス未選択」disable 連動

→ a, b, c は「動作の入口」、d, e, f, g は「観測・表示」、h は通知。

### 2-4. クラス間の現状の依存

```
MainWindow
  ├── NotificationBanner
  ├── SettingsPanel ── controller(AppController)
  │                ── banner
  │                ── control_panel(後注入 ↑)
  └── ControlPanel ── controller(AppController)
                   ── settings_panel(初期化時注入 ↓)
                   ── banner

SettingsPanel と ControlPanel が双方向に widget 参照を持つ ← 強結合の温床
```

---

## 3. 設計の方針

### 3-1. AppController: HTML 既存案を採用(5 コラボレータ + Facade)

`AppControllerResponsibilities.html` の段階分割を踏襲:

1. `StatusBroadcaster`(A + C)
2. `CredentialsCoordinator`(D)
3. `BackendLoaderService`(F + E)
4. `SettingsFacade`(B)
5. `PipelineRunner`(G + H + I)

→ AppController は Facade として残し、上記 5 つを保有 + 横断フローのみ書く(認証成功 → backend evict → 状態通知 の連携 等)。

**追加方針**: Pipeline 改造(`refactor/pipeline-composite-backend`)が入ると plan 派生のコードが
入るため、F と G の境界に「PipelinePlanner」をもう 1 つ挟む可能性がある。順序的にはパイプライン
改造を先にやるか、本ブランチが先かで決める(後述 §5)。

### 3-2. GUI: 「画面構築」と「状態反映ロジック」を分離する

#### Panel と PanelLogic に分ける

各 Panel を以下の 2 クラスに分割する(MVVM の ViewModel 相当を Python で素直に書く形):

| 分割後 | 役割 | テスト容易性 |
|---|---|---|
| `XxxPanelView` (CTkFrame サブクラス) | widget の組み立て + StringVar/イベントハンドラの結線のみ | GUI なしでは試せない |
| `XxxPanelLogic` (素の Python クラス) | ステータス計算 / fallback 判定 / 動作中 restart の発火条件 等 | 単体テスト容易(View モック不要) |

例: `SettingsPanelLogic`

- 「ASR backend 切替時の入力言語 fallback」「TTS の対応言語警告」「PROCESS kind 切替時の UI モード判定」を持つ。
- 受け取るのは `AppController`(Facade)の interface だけ。
- 返すのは「次の表示値」「通知を出すべきか」というデータ。
- View は Logic から返ってきた値を `var.set(...)` / `banner.show_warning(...)` で反映するだけ。

→ `MvcVsMvvm.html` で確認したとおり customtkinter には公式バインディングが無いので、
「Observable プロパティ + 自動同期」までは行かず、**MVC + Observer 混在** が現実解。
ただし「View に居なくていいロジック」を Logic 側に逃がすだけでも、God オブジェクト化は止まる。

#### Mediator を入れて Panel 同士の直接依存を断つ

現状の「SettingsPanel が PID 選択完了で ControlPanel.refresh_ready_state() を呼ぶ」のような
直接 widget 参照を、`PanelMediator`(あるいは AppController の薄いシグナル層)経由に置き換える:

```python
# 旧: SettingsPanel が ControlPanel を直接知っている
self._control_panel.refresh_ready_state()

# 新: Mediator にシグナルを通知。ControlPanel は購読しているだけ。
self._mediator.notify("capture_source_changed")
```

`StatusBroadcaster`(AppController 分割の 1 つ目)が UI 向け listener を持つので、それの
**汎用シグナル版**として `UiEventBus` を 1 本作るのが素直。tkinter には `blinker` 軽量シグナル
ライブラリを足すか自前実装する選択。新規依存を避けるなら自前で十分(Python 30 行)。

### 3-3. ControlPanel から「ステータス表示」を切り出す

ControlPanel の中で「動作の入口(ボタン)」「ステータステキスト(レイヤ別状態 + エラー集約)」
「履歴ボックス」「平均レイテンシ」「アクセラレータ表示」が混在している。

提案: **`StatusPanel` を新規に切り出す**(CollapsibleSection の中身)。

- ControlPanel → ボタン + 履歴 + レイテンシ表示
- StatusPanel  → 全レイヤ状態 + 直近エラー + アクセラレータ表示 + GUI 操作イベントログ

`StatusPanel` は `StatusBroadcaster`(AppController 分割の 1 つ目)の listener として動く。
ControlPanel が `_refresh_status_text` で 3 秒周期 poll している現状は、Broadcaster の push に置き換え。

### 3-4. 命名の改修(影響範囲は小さい)

- `set_callbacks` の単一 callback 互換層は段階的に廃止。`add_status_listener` だけにする(R2-6 の延長)。
- `SettingsPanel._show_message` は `print` に落ちている temporary code。NotificationBanner 必須化で消す。
- `_layer_statuses` / `_layer_rows` などの dict キーを `LayerKind` で統一(既に概ねそうだが残骸あり)。

---

## 4. やる順番(Phase 案)

「テストが回り続ける単位で 1 段ずつ」を厳守。各 Phase で `py -m uv run pytest` が pass する状態を維持。

### Phase R-1: AppController から StatusBroadcaster 抽出(難度小)

- `StatusBroadcaster` クラスを `common/status_broadcaster.py` に作成
- A(UI ブリッジの listener 管理 + emit)と C(ステータス集約・閲覧)の責務を移管
- AppController の対応メソッドはそのまま残し、内部で broadcaster に委譲する形(API 互換)
- 単体テスト: GUI 不要で全部書ける

### Phase R-2: CredentialsCoordinator 抽出(難度小)

- `common/credentials_coordinator.py` に D 責務を移管
- CredentialsStore のラッパ + verify フロー + verified フラグの管理

### Phase R-3: BackendLoaderService 抽出(難度中)

- F + E を `common/backend_loader_service.py` に移管
- ロード時の状態通知は StatusBroadcaster 経由

### Phase R-4: SettingsFacade 抽出(難度中)

- B を `common/settings_facade.py` に移管
- backend 切替時の evict 自動発火は BackendLoaderService への Observer で繋ぐ

### Phase R-5: PipelineRunner 抽出(難度中〜大)

- G + H + I を `common/pipeline_runner.py` に移管
- `_start_coord` の Coordinator + Logger 群の組み立てが集中する場所

**ここまでで AppController は Facade(200 行程度)に縮小**。GUI からの API は維持。

### Phase R-6: UiEventBus 導入 + Panel 間直接参照の廃止(難度小〜中)

- `gui/event_bus.py` を作成(自前 30 行程度の publish/subscribe)
- `SettingsPanel.set_control_panel` を撤去、`UiEventBus.notify("capture_source_changed")` に置き換え
- ControlPanel が `event_bus.subscribe("capture_source_changed", self.refresh_ready_state)` で受ける
- MainWindow が EventBus を生成して両 Panel に渡す

### Phase R-7: StatusPanel 切り出し(難度中)

- `gui/status_panel.py` を新規作成(CollapsibleSection + Textbox + accel label)
- ControlPanel から該当 widget 群を移動
- StatusBroadcaster の listener として動かし、3 秒周期 poll を撤去(push 駆動に)

### Phase R-8: Panel ロジック分離(難度中〜大)

- `SettingsPanelLogic` を新規作成し、言語 fallback / TTS 互換チェック / CAPTURE kind UI モード判定 を移動
- View はロジックの返り値で widget を更新
- 単体テストを新規追加(GUI 不要で全部書ける)
- ControlPanel についても同様に `ControlPanelLogic` 切り出し(状態機械を抽出)

---

## 5. 進める順番の判断(本ブランチ vs パイプライン改造)

両方の計画は独立して進められるが、**先行きの依存関係** を整理する:

| シナリオ | 影響 |
|---|---|
| パイプライン改造を先 | UI 側は「ロール」「複合バックエンド」を表示する必要があり、SettingsPanel に分岐が増える。AppController 分割前に SettingsPanel を触ると後の Phase R-8 で書き直しが増える |
| 役割分離(本ブランチ)を先 | AppController が 5 コラボレータに整い、`BackendLoaderService` が plan 派生をきれいに飲み込める。SettingsPanel の Logic 分離も済んでいるので「(吸収済み)」表記の追加は Logic 側だけで済む |

→ **本ブランチ(役割分離)を先にやる方が、後のパイプライン改造の影響を吸収しやすい**。
詳細な判定は report3.md §3 にも書く。

---

## 6. リスクと対策

| リスク | 対策 |
|---|---|
| AppController の API 互換を崩すと GUI / テストが大量に壊れる | Phase R-1 〜 R-5 では AppController を Facade として残し、既存 API を 100% 維持 |
| Phase が長くなりすぎてマージタイミングが遅れる | 各 Phase = 1 ブランチ = 1 マージ。「ここまでで一旦止まれる」状態を毎回作る |
| UiEventBus を入れるとデバッグが追いづらくなる(誰が emit したか分からない) | Bus に logger 注入。`event_bus.notify(key, source=...)` で発火元を必須化 |
| Panel ロジック分離で「ロジック側からも widget を触りたい」誘惑が出る | Logic は **「次の表示値を返す純関数」+「副作用は AppController/EventBus のみ」** を厳守 |
| StatusBroadcaster の listener が GUI スレッド以外から呼ばれる | 各 listener は `widget.after(0, ...)` で marshalling する規約を維持(現状と同じ) |

---

## 7. スコープ外

- **MVVM / Qt 系への全面移行**: `MvcVsMvvm.html` の結論どおり、UI 全書き換えのコストに見合わない。
- **`blinker` などの新規依存追加**: UiEventBus は自前 30 行で十分。新依存は避ける。
- **CLAUDE.md「役割の表明」運用** の刷新: 1〜2 行の docstring 規約は維持。各 Phase で docstring を更新するだけ。

---

## 8. 着手前に詰めたい点(report3.md に詳細)

- 既存テスト(`tests/test_app_controller_*.py` 系)の fixture 移行コスト
- `StatusBroadcaster` を切り出した時の 旧 single callback(`on_status_change`)互換層をいつ削るか
- `UiEventBus` のイベント名は文字列 / Enum どちらか
- `set_callbacks` の `on_text_ready` / `on_utterance_done` を PipelineRunner 移管時にどう露出するか
