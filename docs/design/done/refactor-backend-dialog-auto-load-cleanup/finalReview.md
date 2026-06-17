# finalReview: refactor-backend-dialog-auto-load-cleanup

## レビュー 1 巡目

**判定: GO**

---

### 観点 0: 上位整合性

指摘なし。

- request.md の「自動時に自動ロード」削除要求に対し、実装は `auto_load` に関するコード・設定・UI・i18n・テスト・ドキュメントを網羅的に削除しており、要求を正確に満たしている。
- checkedRequest.md のスコープ(UI / ConfigStore キー / ロジック分岐 / i18n / テスト)は全て対応済み。非目標(他の lazy ロード機構の変更、ダイアログの他項目リファクタ)には踏み込んでいない。
- Plan.md のセクション A〜H の削除対象は全て実施されている。designReview.md で追加指摘された `AppControllerResponsibilities.html` の残存も対応済み。
- `pendList.md` 111 行の `{auto_load,resample_quality}` 記述は過去の作業記録として残置。designReviewer が「実装者の判断に委ねる」とした件であり、CLAUDE.md の「作業記録系は経緯を書く」方針に沿っているため問題なし。

---

### 観点 1: 設計整合(コード現実)

指摘なし。

- 削除対象のメソッド(`get_auto_load_layers` / `load_auto_load_layers_async`)、ヘルパ関数(`_auto_load_toggle`)、i18n キー(4 言語 x 2 キー = 8 エントリ)、DEFAULT_CONFIG の `auto_load` キー(6 backend)、evict 除外ガード、MainWindow の呼び出しが全て削除されている。
- `src/` 配下と `tests/` 配下に `auto_load` の残存は grep で 0 件を確認。
- Class.md のメソッド一覧・ライフサイクル図・MainWindow 役割記述、Architecture.html の Loader スレッド説明、AppControllerResponsibilities.html のメソッド列挙が全て現行コードと整合している。
- config_store.py の `soundcard` ブロックは Plan.md の指示通り `{}` に変換されている。

---

### 観点 2: テストの十分性と健全性

指摘なし。

- `TestPhaseBAutoLoad` クラス全体(5 テスト)と `test_auto_load_defaults_false_for_all_backends` が削除されている。機能ごと消えたため守るべき契約がなく、削除は妥当。
- `test_backend_filter_excludes_other_backends` の書き換えは適切。旧テストは `toggle` 型フィールドの有無で `applies_when_backend` フィルタを検証していたが、auto_load toggle 削除後は webrtcvad の `aggressiveness` フィールド(他 backend の具体的な設定項目)で同じフィルタロジックを検証する形に書き換えられている。アサーションは「webrtcvad 選択時に webrtcvad 専用フィールドが出る」「silero 選択時に webrtcvad 専用フィールドが出ない」と、フィルタの正方向・負方向の両方を検証しており、「甘くして通す」方向の改変ではない。
- `TestPhaseBConfigDefaults` のクラス docstring が `"backends_config.<backend>.auto_load と consents.* の既定値"` から `"consents.* の既定値"` に正しく更新されている。残る `test_consents_suppress_dialogs_default_false` は引き続き有効。
- コメント修正箇所(test_app_controller.py 606 行、test_vad_switching.py 9 行)は `auto_load` 言及を削除して現行の 2 経路記述に更新されている。
- 全 small テスト 1388 pass、5 skip(無関係の AWS Transcribe backend 条件)を確認。

---

### 観点 3: 発動条件外(スキップ)

backend 追加・置換・device 関連コード変更を含まない削除作業のため。

---

### 観点 4: セキュリティ

指摘なし。認証情報に関わる変更はない。削除のみの作業。

---

### 観点 5: プロジェクト規約遵守(CLAUDE.md 系)

- **[軽] Phase 名残存(コード内 docstring)**: app_controller.py の 797 行 / 1013 行 / 1087 行 / 1124 行、control_panel.py の 10 行に `Phase B` 表記が残存している。CLAUDE.md は「コード docstring 内の既存の Phase/リスク ID 表記は、触ったファイルで都度削る」と定めている。ただし、今回の diff は auto_load 関連の行のみを対象としており、これらの Phase B 表記は別のセクション(start_pipeline / load_model_layer / 認証 gate 等)に属する。削除タスクのスコープ外で関連性が薄いため、重大度は「軽」とする。次回これらの行を触る機会に削ればよい。
  - worker 回答:

指摘なし(上記の軽微指摘は GO を妨げない):
- 「モック対策」の防御 try/except の混入はない。
- 後方互換ハック(リネーム残骸・無意味な再エクスポート)はない。
- i18n 規約: 4 言語 x 2 キーの同時削除により `test_catalog_key_parity` の整合を維持。直書き CJK の混入なし。
- 購読の解除漏れ: 今回の変更で購読の追加・変更はない。
- コメント方針: auto_load 関連の経緯コメント(Phase B 言及)は適切に削除・修正されている。

---

### 観点 6: 役割表明とドキュメント反映

指摘なし。

- Class.md の `load_auto_load_layers_async` メソッド行が削除されている。
- Class.md のライフサイクル図から auto_load 経路が削除され、`[起動](モデルロードなし)` に更新されている。
- MainWindow の役割記述が「auto_load=True のレイヤだけを先行ロード」から「モデルのロードは Start ボタン押下時に行う(lazy ロード)」に書き換えられている。
- Architecture.html の Loader スレッド名が `vt_loader / vt_auto_loader` から `vt_loader` に、説明から `起動時の auto_load` が削除されている。
- main_window.py の docstring が現行動作を正確に記述している。
- 新規クラスの追加はないため、Class.md への新規登録は不要。

---

### 観点 7: スコープ・複雑性

指摘なし。

- 純粋な削除作業に徹しており、要求外の機能追加・過剰一般化はない。
- Plan.md の非目標(他の lazy ロード機構の変更、ダイアログの他項目リファクタ、Architecture の構造変更)を踏み越えていない。
- `toggle` 型が LAYER_SETTINGS で現在使われていない状態になるが、スキーマシステムの一部として型定義・パーサ・ドキュメントが残っているのは妥当(将来の利用を見越した残置であり、過剰ではない)。
