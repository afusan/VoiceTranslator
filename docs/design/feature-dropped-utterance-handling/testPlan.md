# testPlan: feature/dropped-utterance-handling

凡例: ☐ 未着手 / ◐ 着手中 / ☑ 完了

---

## 1. small テスト(単体)

### 1-1. PipelineCoordinator: on_dropped
| 状態 | 項目 | 期待 |
|---|---|---|
| ☐ | キューあふれ発生時に `on_dropped(items, stage_name)` が呼ばれる | items は捨てられた Utterance リスト |
| ☐ | あふれが起きない場合は呼ばれない | |
| ☐ | コールバック内例外でもパイプライン継続 | エラーログ記録 |
| ☐ | 複数件まとめてドロップしても items に全件 | rapid put でテスト |

### 1-2. PipelineCoordinator: pcm 解放
| 状態 | 項目 | 期待 |
|---|---|---|
| ☐ | ASR 後に utt.pcm が None | FakeAsr で transcribe 内で pcm を覗き、その後に None になることを検証 |

### 1-3. AppController: ドロップ→TextLogger
| 状態 | 項目 | 期待 |
|---|---|---|
| ☐ | `_handle_dropped` が TextLogger.write を呼ぶ | mock で確認 |
| ☐ | TextLogger 未設定なら何もしない | エラーなく終わる |
| ☐ | TextLogger.write 例外でもパイプライン継続 | |

### 1-4. SAPI rate config 化
| 状態 | 項目 | 期待 |
|---|---|---|
| ☐ | `backends_config.sapi.rate` 既定値が 180 | ConfigStore のデフォルトを検証 |
| ☐ | `register_default_backends(registry, config)` が config を読む | SAPI ファクトリが指定 rate でインスタンス生成 |
| ☐ | config を渡さない場合は既定の rate=180 が使われる | 後方互換 |
| ☐ | config の rate 変更が反映される | factory 経由で SapiTtsBackend に rate が渡る |

---

## 2. middle / large(目視中心)

| 状態 | 項目 | 期待 |
|---|---|---|
| ☐ | 実機: 長尺発話でドロップ発生 → `soundsrc.txt`/`translated.txt` に欠落なく追記される | overflow ログと突き合わせて確認 |
| ☐ | 実機: config で SAPI rate を 220 等に変更 → 再生が早口になる | 体感確認 |
| ☐ | 実機: メモリ使用量が低下 | (任意)Process 中のメモリプロファイル |

---

## 3. 実行方法

```bash
py -m uv run pytest                # 全体
py -m uv run python -m voice_translator  # 実機GUI
```
