# designReview: refactor-backend-dialog-auto-load-cleanup

## レビュー 1 巡目

**判定: GO**

全体として、削除対象の洗い出しは網羅的で、削除順序・テスト戦略・リスク分析も
十分に練られている。以下の指摘を反映すれば安心して実装着手できる。

---

### 観点 0: 上位整合性

- **[軽] request.md の表現と Plan.md の解釈**
  request.md は「自動時に自動ロード」、Plan.md は「起動時に自動ロード(auto_load)」と言い換えている。意味は同一であり、実ファイルの `auto_load` キーと一致するため問題なし。
  - 実装者回答:

- 指摘なし(request.md / checkedRequest.md に誤情報・矛盾はない。Plan.md は checkedRequest.md のスコープ・非目標をそのまま踏襲している)。

---

### 観点 1: 役割 / 単一責任

- 指摘なし。削除対象はすべて既存クラスの該当メソッド・フィールド・コメントであり、新規の責務追加がないため問題なし。

---

### 観点 2: 既存資産の再利用

- 指摘なし。今回は純粋な削除作業であり、新規クラス・パターンの追加がない。

---

### 観点 3: 配布方針

- 指摘なし。device 関連・backend 追加を含まない削除作業のため、配布方針への影響はない。

---

### 観点 4: スコープ

- **[中] ドキュメント更新(セクション H)に漏れがある**
  Plan.md のセクション H は `Class.md` と `Architecture.html` のみを対象としているが、
  grep の結果、以下のファイルにも `auto_load` への言及が残存する:
  1. `docs/design/append/AppControllerResponsibilities.html`(202-203 行: `load_auto_load_layers_async()` / `get_auto_load_layers()` の列挙)
  2. `docs/design/pendList.md`(111 行: `backends_config.proctap.{auto_load,resample_quality}` の記述)
  3. `docs/design/Class.md` 267 行目: MainWindow の役割記述中に `load_auto_load_layers_async()` / `auto_load=True` の言及

  checkedRequest.md が「ドキュメント: Class.md / Architecture.html の該当記述があれば現在形に整える」としている以上、恒常ドキュメント(`append/` 配下含む)の残存も削除対象に含めるべき。
  一方、`pendList.md` の 111 行は過去の作業記録であり、経緯として残す判断もあり得る(CLAUDE.md「作業記録系は日付・Phase を書く」方針)。ここは実装者の判断に委ねる。
  - 実装者回答:

---

### 観点 5: テスト容易性

- **[軽] 削除順序 E → F の依存方向**
  Plan.md は「5. app_controller.py のメソッド削除 (E) → 6. main_window.py の呼び出し削除 (F)」の順だが、
  main_window.py が `load_auto_load_layers_async()` を呼んでいるため、E を先に消すと F のコードがコンパイルエラー(AttributeError)になる。
  実際の作業では F(呼び出し元) → E(被呼び出し) の順か、同時削除が安全。
  Plan.md の説明「E を削除してから呼び出し側を削除」は因果が逆になっている。
  テスト途中で壊れた状態を経由しないよう順序を修正、または「E と F は同時に削除する」と明記するのが望ましい。
  - 実装者回答:

---

以上。観点 4 の中程度指摘(ドキュメント漏れ)を Plan.md に反映すれば実装着手可。
観点 5 の軽微指摘(削除順序)は作業時に注意すれば足りるが、Plan に記載があると安心。
