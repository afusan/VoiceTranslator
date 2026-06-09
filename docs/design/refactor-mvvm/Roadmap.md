# MVVM 再構築 — 全体ロードマップ

作成: 2026-06-09。本ファイルは **refactor-mvvm/ 配下の全 Phase を俯瞰するインデックス兼ロードマップ**。
各 Phase の詳細は `refactor-mvvm/<phase-folder>/Plan.md`、Phase 横断のふるまい契約は
`refactor-mvvm/phase1-infra/behavioral-contract.md`(全 Phase で参照)を見る。

上位仕様: [`docs/design/append/AppControllerResponsibilities(MVVM).html`](../append/AppControllerResponsibilities(MVVM).html)

---

## 1. 総ゴール / End-State

MVVM 4 層への移行を完了した時点で、以下が成立している:

- **`app_controller.py` は削除されている**(Facade も含めて消える)
- `src/voice_translator/gui/` は **View 専用**(widget 組み立て + バインドのみ。ロジックを持たない)
- `src/voice_translator/viewmodels/` に各 ViewModel(GUI なしで単体テスト可能)
- `src/voice_translator/common/` は **Model + Infrastructure**(`Observable[T]` / `Command` / 各 Coordinator)
- 状態同期は **手書きの "Controller が View を更新する" コードが消え、Observable 購読に統一**
- 「設定を保存」ボタンは存在せず、設定変更は **DebouncedSaver による auto-persist** で吸収される
- 起動エントリは `__main__.py` → `AppContainer` → `MainViewModel` + `MainWindow`
- `behavioral-contract.md` の全項目が ✅ / ❌ / 🧪 のいずれかになっている

---

## 2. Phase 全体俯瞰

| # | Phase | 主な成果物 | 依存 | 完了時に見える状態 | 想定 PR 数 |
|---|---|---|---|---|---|
| 1 | **infra**(extraction) | `Observable[T]` / `Subscription`(BackendBase から extraction)+ `Command` / `AsyncCommand` / `TkBinder` / `UiDispatcher` / `DebouncedSaver` / `AppContainer`(skeleton) | — | 基盤コードと既存 BackendBase が新形に揃う。**UI / ふるまいは変わらない** | 1(本ブランチ) |
| 2 | **status-vm** | `StatusVM` + `StatusView` 最小ペア。AppController._emit_status を Observable へ橋渡し | P1 | ControlPanel のステータステキスト部分が VM 駆動に置き換わる。3 秒 poll が消える | 1 |
| 3 | **backend-selection-vm** | `BackendSelectionVM` + `BackendSettingsView` + Model 層 `BackendLoader` 抽出 | P1, P2 | SettingsPanel のバックエンドセクションが VM 駆動に。AppController の Backend 関連メソッドが激減 | 1〜2 |
| 4 | **language-selection-vm** | `LanguageSelectionVM` + `LanguageSettingsView` + `NotificationVM` | P3 | SettingsPanel の言語連動ロジックが VM の Reaction に。手書きの fallback コードが消える | 1 |
| 5 | **device-selection-vm** | `DeviceSelectionVM` + `DeviceSettingsView`(CAPTURE kind 別 UI も VM 側で吸収) | P3 | SettingsPanel の「ControlPanel への逆参照」が不要に。Panel 間直接結合が消える | 1 |
| 6 | **pipeline-runner-vm** | `PipelineRunner`(Model)+ `ControlVM` / `HistoryVM` / `Control/HistoryView` + DebouncedSaver 接続。「設定を保存」ボタン撤去 | P2, P5 | **AppController が解体される**。ふるまい契約 §13(MVC 由来の挙動)が新形に置換 | 2〜3 |
| 7 | **dialog-vm** | LayerSettings / Credential / Consent / ProcessSelect の各 Dialog VM 化 | P6 | AppController の最後の残骸が撤去 → `__main__.py` から `AppContainer` + `MainViewModel` 起動に | 1〜2 |
| 8 | **docs-end-state** | Architecture.html を MVVM 4 層構成で書き直し、Class.md の AppController 章を削除 + MVVM 章追加 | P7 | ドキュメントが実装と一致。新規開発者の参照点が揃う | 1 |

**合計**: 9〜12 PR、各 Phase = 1 ブランチを基本(P3 / P6 は規模次第で 2 ブランチ分割可)。

---

## 3. 依存グラフ

```
  P1 (infra)
   ├── P2 (status-vm)
   ├── P3 (backend-selection) ── P4 (language-selection)
   │                          └── P5 (device-selection)
   ↓
  P2 + P5  →  P6 (pipeline-runner + AppController 解体)
                ↓
              P7 (dialog-vm)
                ↓
              P8 (docs end-state)
```

- **P2 / P3 / P5 は P1 さえ終われば並行可能**(着手者が分かれる場合に有用、現状は単独着手なので順次)
- **P3 → P4 は順序固定**(LanguageSelectionVM が BackendSelectionVM の Observable を購読するため)
- **P6 が最大の山場**。AppController を解体しながら PipelineRunner を Model 層に切り出すため、`behavioral-contract.md` 全章の手チェックを必須

---

## 4. 判断点(Phase 間で立ち止まる場所)

| タイミング | 判断 | アクション |
|---|---|---|
| P1 完了後 | `Observable[T]` の自前実装に予想外の難所が出ていないか | 出ていれば: `Observable` 内部実装だけ traitlets に差し替え(公開 API 維持)。出ていなければ次へ |
| P3 完了後 | `BackendLoader` の Model 化が想定範囲か | 範囲超過なら BackendSelectionVM を含む P3 を 2 PR に分割 |
| P5 完了後 | **パイプライン改造に切り替えるか / P6 を続けるか** | AppController が薄くなった時点で立ち止まり、複合バックエンド需要との優先順位を再評価。`refactor-pipeline-composite-backend/Plan.md` 着手判定 |
| P6 着手前 | DebouncedSaver による auto-persist のふるまい(`behavioral-contract §13.1`)を本当に変えるか | 撤回するなら ControlVM の Save Command を残す案にスライド |
| P6 完了後 | AppController が完全に消えたか / 残骸があるか | 残骸があれば P7 のスコープに繰り入れる |
| P8 完了後 | Architecture.html と実装の整合 | 不整合があれば再修正の小さな PR を当てる |

---

## 5. behavioral-contract.md との連動

各 Phase で **対応する章を手チェック**する。Phase 完了 PR の説明文に「契約のどの章を見たか」を記載する規約。

| Phase | 重点的に見る章(behavioral-contract.md) |
|---|---|
| P1 | **全章で「ふるまいが変わっていないこと」を確認**(Phase 1 はふるまい変更ゼロが完了条件) |
| P2 | §2(モデルステータス表示)/ §7(ステータス集約テキスト)/ §6(アクセラレータ表示) |
| P3 | §1(バックエンド選択)/ §2(モデルステータス表示)/ §8(認証フロー)|
| P4 | §1.6〜1.10(言語連動 + TTS 互換警告) |
| P5 | §11(プロセス選択)/ §1.11(CAPTURE kind 別 UI)/ §3.11(動作中 device 切替 restart) |
| P6 | §3(Start / Stop / 動作中)/ §4(翻訳結果)/ §5(レイテンシ)/ §10(設定永続化)/ §12(エラーハンドリング)/ §13(削除予定 = ここで実際に置き換える) |
| P7 | §8(認証 dialog)/ §9(出力テスト)/ §11(プロセス選択 dialog) |
| P8 | 全章の最終チェック(✅ / ❌ / 🧪 の最終確認) |

---

## 6. パイプライン改造との合流

別系統である [`refactor-pipeline-composite-backend/Plan.md`](../refactor-pipeline-composite-backend/Plan.md)
(複合バックエンド対応)は MVVM 化と **独立して進められる**が、いくつか合流点がある:

- **着手タイミング**: §4 の通り、P5 完了後の判断点が自然な切替候補
- **PipelineRunner の置き場**: パイプライン改造の `build_pipeline_plan` 純関数は Model 層
  (`pipeline_runner.py` / `pipeline.py`)に閉じる。MVVM 側は ControlVM / StatusVM が Observable で受けるだけ
- **UI 表記の連動**: 複合バックエンドが入ると `BackendSelectionVM` に **「(◯◯ に統合)」フラグ**(`absorbed_by`)が必要。
  これは MVVM 化が済んでいれば「VM に Observable 追加 + View に小さなラベル」だけで済む
- **逆順だった場合**: パイプライン改造を先にすると、Coordinator 周りの責務がさらに重くなる(MVVM 移行で PipelineRunner に切り出す前提が崩れる)。現状の MVVM 先行は合理的

---

## 7. 中断時の規約(各 Phase が独立にマージ可能であること)

各 Phase は **以下を満たす状態でマージできる**ことを規約とする:

1. `py -m uv run pytest` が完全 pass(small カテゴリ)
2. 当該 Phase が触る範囲のふるまい契約を **手で 1 回確認**(完了 PR に記録)
3. **既存 AppController と新 VM が共存していて壊れていない**
   - 例: P3 完了時点で BackendSelectionVM が AppController を介して動く(AppController がまだ仲介役を兼ねる)
   - P6 で初めて AppController が解体され、共存状態が解消する
4. ドキュメントは最低限(Class.md の関連メソッドの所属移動を反映)更新

中断したい場合は、その Phase の PR をマージするか、ブランチを破棄するか、いずれかで終端する。**中間状態を長期間のブランチで放置しない**。

---

## 8. ディレクトリ構成(refactor-mvvm/ 配下)

```
docs/design/refactor-mvvm/
├── Roadmap.md                  … 本ファイル
├── phase1-infra/
│   ├── Plan.md
│   └── behavioral-contract.md  … 全 Phase 共通(P1 で起票、各 Phase で参照)
├── phase2-status-vm/           … 着手時に新設
│   └── Plan.md
├── phase3-backend-selection-vm/
├── phase4-language-selection-vm/
├── phase5-device-selection-vm/
├── phase6-pipeline-runner-vm/
├── phase7-dialog-vm/
└── phase8-docs-end-state/
```

完了時の取り扱い: 各 Phase の `<phase>/` フォルダは master マージ後に **`docs/design/done/refactor-mvvm/<phase>/`** に移動する
(CLAUDE.md の「マージ完了時に done/ へ」運用に合わせる)。

---

## 9. 関連ドキュメント

- 上位仕様: [`AppControllerResponsibilities(MVVM).html`](../append/AppControllerResponsibilities(MVVM).html)
- 採用判断の根拠: [`MvcVsMvvm.html`](../append/MvcVsMvvm.html)
- 旧 MVC 案(参考扱い): [`pend/refactor-roles-rebalance/Plan.md`](../pend/refactor-roles-rebalance/Plan.md)
- パイプライン改造(別系統): [`refactor-pipeline-composite-backend/Plan.md`](../refactor-pipeline-composite-backend/Plan.md)
- ふるまい契約: [`phase1-infra/behavioral-contract.md`](phase1-infra/behavioral-contract.md)
- 全体構成: [`Architecture.html`](../Architecture.html) / [`Class.md`](../Class.md)(P8 で MVVM 版に書き直し予定)
