# UI 肥大化対策「3 Move」— 全体ロードマップ

作成: 2026-06-10。本ファイルは **refactor-ui-3move/ 配下の全 Phase を俯瞰するインデックス兼ロードマップ**。
分析と提案の経緯は `tmp/report4.html`(git 管理外)に基づき、採用された内容を本フォルダに正式化した。

> **旧計画との関係**: MVC 5 コラボレータ案(`pend/refactor-roles-rebalance`、参考扱い)と
> MVVM 全面再構築案(`refactor-mvvm/`、2026-06-10 停止 → `tmp/stopped-mvvm-plan/` に退避)の
> 後継。両案の分析・思想は引き継ぎ、**自前フレームワーク建設と AppController 削除はしない**。
> 引き継ぎ対応の詳細は §6。

---

## 1. 総ゴール / End-State

P3 完了時点で以下が成立している:

- **UI 判断ロジック**(ボタン状態計算・言語候補計算・表示文字列整形・集約判定)が
  `gui/logic/` の**状態を持たない純関数**に集約され、GUI なしの small テストで全分岐を検証できる
- Panel は「widget 組立 + イベント → logic 呼び出し → 戻り値を塗る」だけ(役割を 1 行で言える)
- **通知経路が Subscription 1 本**: `set_callbacks` / Panel 間逆参照 / 3 秒 poll は存在しない
- AppController は「設定を読み、backend をロードし、パイプラインを起動・停止する**ランタイム**」
  (約 700 行)。メタ問合せは `BackendCatalog`、認証は `CredentialsService` に分離
- 防御的 `except Exception`(現状 UI+Controller で 115 箇所)が契約テストへの置き換えで半減
- 新機能の UI 対応は「logic 関数 1 つ + View の塗り 1 行」で済む(税金構造の解消)

**やらないこと(明示)**:
- Observable / Command / TkBinder / DebouncedSaver / AppContainer 等の基盤自作(MVVM 案の中核 → 撤回)
- AppController の削除・Facade 化(ランタイム役として残す)
- StatusBroadcaster / SettingsFacade / PipelineRunner の切り出し(残留部とロック・キャッシュを
  共有しており、切ると配線が純増する。PipelineRunner のみ複合バックエンド改造着手時に再判断)
- 「設定を保存」ボタンの auto-persist 化(UX 変更であり肥大化と独立 → pendList 起票済み 2026-06-10)
- 新規依存の追加

---

## 2. Phase 全体俯瞰

| # | Phase | ブランチ | 主な成果物 | 依存 | ふるまい変更 | 状態 |
|---|---|---|---|---|---|---|
| P1 | **logic-extract** | `refactor/ui-phase1-logic-extract` | `gui/logic/`(ready_state / language_choices / backend_display / status_summary / accel_summary / palette)+ Panel の塗り直し + shim テストの純 small 化 | — | **ゼロ**(表示文字列まで同一) | ✅ 実装完了 2026-06-10(マージは全 Phase 完了後にまとめて、のユーザ方針で保留) |
| P2 | **event-unify** | `refactor/ui-phase2-event-unify` | `set_callbacks` 廃止 → Subscription 統一 / Panel 間逆参照・転送の撤去 / 動作中デバイス変更 restart の AppController 移管 / poll は 30 秒に縮小(判断点の決定: エラー履歴の遅延表示専用として存続) | P1 | **restart の発火条件のみ**(契約 §3.11 を ❌ 書き換え済み) | ✅ 実装完了 2026-06-10(P1 からのスタックブランチ) |
| P3 | **controller-slim** | `refactor/ui-phase3-controller-slim` | `common/backend_catalog.py`(メタ問合せ)+ `common/credentials_service.py`(認証)分離。AppController の既存メソッドは 1 行委譲の互換窓として残置 | P1 | ゼロ | ✅ 実装完了 2026-06-10(P2 からのスタックブランチ) |
| P4 | **(任意)** | 着手時に命名 | ① StatusPanel / HistoryPanel の物理分割 ② GUI 参照の catalog / credentials 直付け替え + 互換窓削除 ③ backend エラーのイベント化 + 30 秒 poll 完全廃止 ④ PipelineRunner 切り出し(複合バックエンド着手時) | P1〜P3 | ゼロ | 未着手(痛み / 必要が出たら) |

**合計 3〜4 PR**。各 Phase は単独でマージ可能・単独で価値が残ることを成立条件とする
(P1 だけで止めても、テスト簡素化と「税金の着地点」が残る)。

### 依存グラフ

```
  P1 (logic-extract)
   ├── P2 (event-unify)
   └── P3 (controller-slim)   ← P2 と独立、並行可
          ↓
        P4 (任意)
```

---

## 3. 横断テーマ(各 Phase に同梱、専用 Phase は設けない)

- **防御 except の削減**: `# noqa: BLE001 - モック対策` 系は、テストが shim 不要になった範囲から
  削除する。BackendBase 契約確立後の「仕様逸脱 backend への保険」は契約テスト(small)で置き換える。
  目安: 115 → 50 以下。**触ったファイルで都度削る**。
- **Class.md の更新**: 各 Phase で新クラス/移動メソッドの所属を反映(最低限でよい)。
- **behavioral-contract.md の手チェック**: 各 Phase 完了時に該当章を実機で 1 回踏み、
  ✅(自動テスト化)/ ❌(意図的変更)/ 🧪(手動確認)を記録する。
  契約は [behavioral-contract.md](behavioral-contract.md)(停止 MVVM 計画から復帰・本計画用に再調整)。

| Phase | 重点章(behavioral-contract.md) |
|---|---|
| P1 | §1.6〜1.11(言語連動)/ §3.1〜3.6(ボタン状態)/ §6(アクセラレータ)/ §7(ステータス集約)/ §9(出力テスト) |
| P2 | §2.8(multi-listener)/ §3.11(restart — **意図的変更 ❌**)/ §11.5(プロセス選択 → ready 遷移)/ §13(置き換え実施) |
| P3 | §2(ステータス)/ §8(認証フロー)全章 |

---

## 4. 判断点(Phase 間で立ち止まる場所)

| タイミング | 判断 | アクション |
|---|---|---|
| P1 完了後 | logic 関数が「状態を持たない」規約を守れているか | 状態・副作用が混入していたらマージ前に戻す。規約: **次の表示値を返す純関数 / 副作用は View と Controller のみ** |
| P2 着手前 | 3 秒 poll を即廃止するか、30 秒間隔の保険として 1 Phase 残すか | 「全状態変化が Subscription を通る」small テストが揃えば即廃止。不安が残れば保険残し → P3 で削除 |
| P2 着手前 | restart 発火の意味拡張(デバイス再列挙の fallback 書き込みでも restart)を受け入れるか | 受け入れ推奨(実デバイスが変わったのに旧デバイスで動き続ける方が事故)。契約 §3.11 を ❌ + 書き換え |
| P3 完了後 | **複合バックエンド改造(`refactor-pipeline-composite-backend/Plan.md`)に着手するか** | UI 整理完了後が最適タイミング(「(◯◯ に統合)」表記が logic 関数 1 つで済む)。PipelineRunner 切り出しの要否もここで判断 |
| P4 | ControlPanel がまだ読みにくいか | 痛みが残る場合のみ StatusPanel / HistoryPanel を物理分割(logic 抽出後は機械的作業) |

---

## 5. 実装委譲の運用(安価モデルへの委譲ガイド)

各 Phase の Plan.md は**処方箋形式**(新規ファイル・関数シグネチャ・移行元の行範囲・禁止事項を明記)で
書き、設計判断を実装時に発生させない。委譲の適否:

| Phase | 委譲適性 | 理由 |
|---|---|---|
| P1 | **◎ 委譲向き** | 純関数抽出 + テスト書き換え。シグネチャと移行マップを Plan.md で確定済み。スレッド境界に触れない |
| P2 | **△ 非推奨** | `after(0)` marshalling・listener スレッド・restart 競合などスレッド境界の変更が本体。委譲するなら完了後に上位モデルでレビュー必須 |
| P3 | **◎ 委譲向き** | 状態を持たない proxy 群の移動。機械的 |

**委譲時のガードレール(Plan.md に同梱、実装者への必須指示)**:
1. **テストを通すためにテストを弱めない**(assert の削除・緩和は禁止。CLAUDE.md「テスト変更時の方針」順守)
2. **新しい `try/except Exception` を追加しない**(P1 の目的の一つが防御コードの削減)
3. Plan.md に無い public API の追加・変更をしない(必要が生じたら実装を止めて報告)
4. 完了条件: `py -m uv run pytest` 全 pass + Plan.md のチェックリスト全項目 + 表示文字列の不変
   (golden テストで担保)
5. マージ判断はレビュー後(実装者はマージしない)

---

## 6. 旧計画からの引き継ぎ対応

| 旧計画の要素 | 本計画での扱い |
|---|---|
| MVC 案 §2 責務マップ | 引き継ぐ(分析として有効。`tmp/stopped-mvvm-plan/refactor-roles-rebalance/Plan.md`) |
| MVC 案 R8: PanelLogic 分離 | **P1 そのもの**(順序を最初に繰り上げ) |
| MVC 案 R6: UiEventBus 新設 | 形を変えて P2(新クラスは作らず既存 Subscription 機構の適用拡大) |
| MVC 案 R1〜R5: 5 コラボレータ分割 | 縮小して P3(状態を共有しない Catalog / Credentials の 2 切片のみ) |
| MVC 案 R7: StatusPanel 分離 | P4(任意) |
| MVVM: 基盤自作(Observable 他 5 点) | **撤回**(tkinter 標準 + 既存 Subscription で足りる) |
| MVVM: AppController 削除 | **撤回**(ランタイム役として残す) |
| MVVM: auto-persist(保存ボタン撤去) | pendList へ分離(2026-06-10 起票) |
| MVVM: behavioral-contract.md | **復帰**(本フォルダ。§10.1 / §13 を本計画用に再調整) |

---

## 7. ディレクトリ構成(refactor-ui-3move/ 配下)

```
docs/design/refactor-ui-3move/
├── Roadmap.md                    … 本ファイル
├── behavioral-contract.md        … ふるまい契約(全 Phase 共通、停止 MVVM 計画から復帰)
├── phase1-logic-extract/
│   ├── Plan.md                   … P1 処方箋(委譲可能な粒度)
│   └── testPlan.md
├── phase2-event-unify/           … 着手時に新設
└── phase3-controller-slim/       … 着手時に新設
```

完了時の取り扱い: 各 Phase の `<phase>/` フォルダは master マージ後に
`docs/design/done/refactor-ui-3move/<phase>/` へ移動する(CLAUDE.md 運用)。
Roadmap.md と behavioral-contract.md は全 Phase 完了まで本フォルダに残す。

---

## 8. 関連ドキュメント

- ふるまい契約: [behavioral-contract.md](behavioral-contract.md)
- P1 計画: [phase1-logic-extract/Plan.md](phase1-logic-extract/Plan.md) / [testPlan.md](phase1-logic-extract/testPlan.md)
- パイプライン改造(別系統・P3 後に合流判断): [../refactor-pipeline-composite-backend/Plan.md](../refactor-pipeline-composite-backend/Plan.md)
- 旧計画(参考、git 管理外): `tmp/stopped-mvvm-plan/`(MVVM Roadmap / 責務マップ)
- 分析レポート(git 管理外): `tmp/report4.html`
- 全体構成: [../Architecture.html](../Architecture.html) / [../Class.md](../Class.md)
