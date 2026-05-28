# testPlan: feature/translation-quality-step1

凡例: ☐ 未着手 / ◐ 着手中 / ☑ 完了

---

## 1. small テスト(単体)

| 状態 | 項目 | 期待 |
|---|---|---|
| ☐ | `SileroVadBackend` の既定パラメータが新しい値で渡る | `VADIterator` 呼び出しに `min_silence_duration_ms=800`, `speech_pad_ms=250` が含まれる(モックで検証) |
| ☐ | `FasterWhisperAsrBackend` の `beam_size` 既定値が 5 | `WhisperModel.transcribe` 呼び出しの kwargs を検証 |
| ☐ | `FasterWhisperAsrBackend` が `condition_on_previous_text=True` を渡す | 同上 |
| ☐ | `PipelineCoordinator._put_with_drop` でドロップ時に WARN ログが出る | `caplog` で WARN レベルログ取得、ステージ名と件数が含まれる |
| ☐ | overflow 累計が増えていく | 連続でドロップ発生させ、累計値が増えることを確認 |

---

## 2. middle / large(目視中心)

| 状態 | 項目 | 期待 |
|---|---|---|
| ☐ | 実機: 同じ素材を Step 0(変更前) と Step 1(本変更) で翻訳して比較 | 文末の途切れが減る / 訳文が自然になる(主観) |
| ☐ | 実機: レイテンシ計測 | +200〜400ms 程度の増加に収まる |
| ☐ | 連続発話素材で overflow ログが出るかどうか観測 | 案C(完全並列化)の必要性判断材料に |

---

## 3. 実行方法

```bash
py -m uv run pytest                # 全体
py -m uv run python -m voice_translator  # 実機GUI起動
```
