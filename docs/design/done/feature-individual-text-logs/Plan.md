# Plan: feature/individual-text-logs

翻訳前後テキストの個別ログ出力(デバッグ用)。pendList [2026-05-26] の対応。

---

## 目的

翻訳精度の評価・トラブルシュートを楽にするため、
- 翻訳前テキスト(ASR出力 = `src_text`)を `soundsrc.txt` に追記
- 翻訳後テキスト(`tgt_text`)を `translated.txt` に追記

既存 jsonl は機械処理向け、テキストファイルは人間が斜め読みする用途。

---

## 決め事(確定済)

| # | 項目 | 値 |
|---|------|---|
| 1 | ファイル名 | `soundsrc.txt` / `translated.txt` |
| 2 | 出力先 | 既存 `log.directory` 配下(jsonl と同居) |
| 3 | 書式 | `[YYYY-MM-DD HH:MM:SS] [lang] text\n` |
| 4 | 言語コード | src は ASR検出 or 設定値、tgt は設定値 |
| 5 | 既定 ON/OFF | **OFF**(デバッグ用) |
| 6 | 設定キー | `log.src_text_enabled` / `log.tgt_text_enabled` |
| 7 | GUI公開 | なし(`config.yaml` 編集で切替) |
| 8 | 書き込みタイミング | `_handle_utterance_done` 内(Output スレッド末尾) |
| 9 | エンコード / 改行 | UTF-8 / LF |
| 10 | 追記モード | append、ローテーションなし |
| 11 | 書き込み失敗時 | ログ記録して継続(パイプライン本体を止めない) |
| 12 | 既存 jsonl との関係 | 併用 |
| 13 | クラス設計 | **B 案**: 新クラス `TextLogger` を別途追加 |
| 14 | 空文字の扱い | src/tgt が空なら書かない |

---

## スコープ

### IN
- `TextLogger` クラスを `src/voice_translator/common/logger.py` に追加
- `ConfigStore.DEFAULT_CONFIG.log.*` に新設定キー2つ追加
- `AppController._loader_body` で `TextLogger` を生成
- `AppController._handle_utterance_done` で `TextLogger.write` を呼ぶ
- 単体/結合テスト追加
- `docs/manual.md` に使い方追記、`Class.md` に `TextLogger` 追加

### OUT
- GUI からの ON/OFF 切替(将来 Phase 2 で SettingsPanel に追加)
- ローテーション
- フィルタ(言語別ファイルなど)

---

## 実装ステップ

1. `common/logger.py` に `TextLogger` クラス追加
2. `common/config_store.py` の `DEFAULT_CONFIG` に新キー追加
3. `common/app_controller.py` の `_loader_body` と `_handle_utterance_done` を更新
4. テスト追加(`tests/test_logger.py` 拡張 + `tests/test_app_controller.py` 拡張)
5. `docs/manual.md` / `docs/design/Class.md` 更新
6. pytest 全パス確認
7. コミット → 実機確認 → マージ

---

## 完了条件 (Definition of Done)

- [ ] `config.yaml` で `log.src_text_enabled: true` にすると `<log_dir>/soundsrc.txt` に追記される
- [ ] `log.tgt_text_enabled: true` で `<log_dir>/translated.txt` に追記される
- [ ] 各 OFF で何も書かれない(ファイルすら作らない)
- [ ] 空 src_text / tgt_text の発話は出力スキップ
- [ ] 書き込み失敗時にパイプラインが止まらない
- [ ] 既存 jsonl 出力も従来通り動く
- [ ] 既存テスト全パス + 新規 TextLogger テスト 全パス

---

## 関連ドキュメント
- アーキテクチャ: [Architecture.html](../Architecture.html)
- クラス詳細: [Class.md](../Class.md)
- 全体タスク: [TaskList.md](../TaskList.md)
- 保留リスト: [pendList.md](../pendList.md)
- テスト項目: [testPlan.md](testPlan.md)
