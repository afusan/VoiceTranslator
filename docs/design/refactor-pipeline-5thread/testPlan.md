# testPlan: refactor/pipeline-5thread

凡例: ☐ 未着手 / ◐ 着手中 / ☑ 完了

---

## R-1: 型と中央管理

### messages.py
| 状態 | 項目 | 期待 |
|---|---|---|
| ☐ | `PipelineMessage` のシリアライズ可否 / フィールド | seq_id, payload を持つ |
| ☐ | 各 payload 型のフィールド | 必要最小フィールドのみ |

### UtteranceLedger
| 状態 | 項目 | 期待 |
|---|---|---|
| ☐ | init(seq_id) で空レコード生成 | |
| ☐ | mark_time(seq_id, stage) で timeline 追加 | |
| ☐ | record(seq_id, **fields) で任意フィールド追加(merge) | |
| ☐ | pop(seq_id) で全情報取得 + ledger からは削除 | メモリリーク防止 |
| ☐ | 並行アクセス(複数スレッド)でデータ破損なし | Lock 検証(threading で並行 mark) |
| ☐ | 未登録 seq_id への mark は安全(自動初期化) | |
| ☐ | 未登録 seq_id への pop は空dict | KeyError しない |

### SequenceGenerator
| 状態 | 項目 | 期待 |
|---|---|---|
| ☐ | next() が単調増加 | 1, 2, 3... |
| ☐ | 並行 next() でも重複なし | スレッドセーフ |

---

## R-2: バックエンドI/Fプリミティブ化

各バックエンド単体テストを新I/Fで書き直し。

### AsrBackend
| 状態 | 項目 | 期待 |
|---|---|---|
| ☐ | transcribe(pcm, hint) が (text, lang) を返す | 戻り値型 |
| ☐ | FasterWhisper 実装の引数受け渡し | 既存テストの新I/F版 |
| ☐ | 空pcm入力で SkipError | |

### TranslatorBackend
| 状態 | 項目 | 期待 |
|---|---|---|
| ☐ | translate(src_text, src_lang, tgt_lang) が str を返す | |
| ☐ | NLLB 実装の言語マッピング | 既存テストの新I/F版 |
| ☐ | 空テキストで passthrough or SkipError | 仕様確定要 |

### TtsBackend
| 状態 | 項目 | 期待 |
|---|---|---|
| ☐ | synthesize(text, lang) が (pcm, samplerate) を返す | |
| ☐ | SAPI 実装 + flush_delay 動作維持 | |

### AudioOutputBackend
| 状態 | 項目 | 期待 |
|---|---|---|
| ☐ | play(pcm, samplerate) が呼ばれる | |
| ☐ | Soundcard 実装の動作維持 | |

---

## R-3: PipelineCoordinator 5スレッド版

### ライフサイクル
| 状態 | 項目 | 期待 |
|---|---|---|
| ☐ | start() で5スレッド起動、全キュー drain、ledger クリア | |
| ☐ | stop() で stop_event セット → 各スレッドが順次終了 | センチネル順序 |
| ☐ | 再 start() で前回の残骸を引きずらない | |
| ☐ | is_running が全スレッド状態を反映 | |

### データフロー
| 状態 | 項目 | 期待 |
|---|---|---|
| ☐ | 1発話を流して各段が呼ばれる(モック) | seq_id 一貫 |
| ☐ | ledger に各段のタイムスタンプが記録される | |
| ☐ | 最終段で ledger.pop → jsonl に書かれる | |
| ☐ | TextLogger.write_src は ASR 段で呼ばれる | |
| ☐ | TextLogger.write_tgt は Translator 段で呼ばれる | |

### キューあふれ
| 状態 | 項目 | 期待 |
|---|---|---|
| ☐ | 各キューの drop で on_dropped(seq_id付き)が呼ばれる | |
| ☐ | drop 累計が stage 別に取れる | get_drop_counts |
| ☐ | drop 時にも text-log は残る(現状機能の継承) | |

### エラー処理
| 状態 | 項目 | 期待 |
|---|---|---|
| ☐ | FATAL なら全スレッド停止 | stop_event |
| ☐ | SKIP は当該発話破棄、他は継続 | |
| ☐ | コールバック失敗で停止しない | |

---

## R-4: ドキュメント

| 状態 | 項目 | 期待 |
|---|---|---|
| ☐ | Class.md が5スレッド + ledger 構成を反映 | |
| ☐ | manual.md の動作説明が更新 | |
| ☐ | shortcutList A-1 が「解消済」マーク | |
| ☐ | pendList の「案C」が解消マーク | |

---

## 実機(全Phase完了後)

| 状態 | 項目 | 期待 |
|---|---|---|
| ☐ | 英語YouTube → 日本語TTS 縦通し動作 | 既存と同等 |
| ☐ | ログに seq_id が出て対応が取れる | grep で確認 |
| ☐ | 連続発話でスループット改善を体感 | overflow ログの頻度↓ |
| ☐ | 停止 → 再開 が clean | 古い発話の再生なし |

---

## 実行方法

```bash
py -m uv run pytest               # 全体
py -m uv run pytest tests/test_ledger.py -v  # R-1 のみ等
```
