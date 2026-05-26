# testPlan: feature/individual-text-logs

凡例: ☐ 未着手 / ◐ 着手中 / ☑ 完了

---

## 1. small テスト(単体)

### 1-1. TextLogger
| 状態 | 項目 | 期待 |
|---|---|---|
| ☐ | 既定 OFF: ファイルが生成されない | `src_enabled=False, tgt_enabled=False` で write しても両ファイル不存在 |
| ☐ | src のみ ON | `soundsrc.txt` に追記、`translated.txt` 不存在 |
| ☐ | tgt のみ ON | `translated.txt` に追記、`soundsrc.txt` 不存在 |
| ☐ | 両方 ON | 両ファイルに追記 |
| ☐ | 行フォーマット | `[YYYY-MM-DD HH:MM:SS] [lang] text\n` |
| ☐ | append モード | 複数 write で追記される(上書きされない) |
| ☐ | 空 src_text スキップ | `src_text=""` でも src ファイルに行が追加されない |
| ☐ | 空 tgt_text スキップ | 同上 |
| ☐ | UTF-8 で日本語が書ける | 文字化けしないこと |
| ☐ | LF 改行で書かれる | Windows 上でも `\n` のまま(自動 CRLF にならない) |

### 1-2. ConfigStore 既定値
| 状態 | 項目 | 期待 |
|---|---|---|
| ☐ | `log.src_text_enabled` の既定が False | |
| ☐ | `log.tgt_text_enabled` の既定が False | |

### 1-3. AppController 連携
| 状態 | 項目 | 期待 |
|---|---|---|
| ☐ | start_pipeline 後に TextLogger が作られる | `AppController._text_logger` 非None |
| ☐ | `_handle_utterance_done` で TextLogger.write が呼ばれる | カウントなりログなりで検証 |
| ☐ | TextLogger 書き込み失敗でも UI コールバックは呼ばれる | 例外を握って on_utterance_done に到達 |

---

## 2. middle / large(目視)

| 状態 | 項目 | 期待 |
|---|---|---|
| ☐ | 実機: `config.yaml` で両 ON → 開始 → 数発話 → ファイル内容確認 | 期待行が両ファイルに書かれる |
| ☐ | 設定切替(片方だけ ON など)で挙動が変わる | |

---

## 3. 実行方法

```bash
py -m uv run pytest                # 全体
py -m uv run pytest tests/test_logger.py -v   # ロガー周りだけ
```
