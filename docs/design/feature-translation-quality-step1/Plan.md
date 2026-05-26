# Plan: feature/translation-quality-step1

[report6.html](../../../tmp/report6.html) のロードマップ **Step 1: 翻訳精度改善(コード変更のみ、無料)** の作業計画。

---

## 目的

翻訳結果の品質を底上げする。実機で「精度がよくない」という所見への第一手として、
モデル交換などの重い変更に入る前にコストゼロでできる調整を入れる。

---

## スコープ

### IN
- **VAD 区切りの調整**: 文末まで待ち、語尾の子音を逃さないようにする
- **Whisper の ASR 精度向上**: ビーム探索を有効化
- **キュー overflow ログ追加**: 案C(完全並列化)が必要になった時の判断材料を収集
- 既存テストへの影響回避(モック側でデフォルト変更があってもパスすること)

### OUT(別フェーズ)
- Whisper モデルサイズ変更(small→medium) … Step 2
- NLLB モデルサイズ変更 … Step 3
- LLM 翻訳 / DeepL API … Step 4
- パラメータの GUI 化 … pendList(案件は記録済)
- 完全並列化(案C) … pendList(案件は記録済)

---

## 実装ステップ

### ステップ 1: VAD パラメータ既定値変更
`src/voice_translator/vad/silero_backend.py` の `__init__` 既定値:
- `min_silence_ms`: 500 → **800**(文末まで待つ)
- `speech_pad_ms`: 100 → **250**(語尾の子音を逃さない)
- `threshold`: 0.5(維持)

### ステップ 2: Whisper ASR チューニング
`src/voice_translator/asr/faster_whisper_backend.py`:
- `beam_size` 既定値: 1 → **5**(誤り低減)
- `transcribe()` で `condition_on_previous_text=True` を明示(既定だが意図を明確化)

### ステップ 3: キュー overflow ログ
`src/voice_translator/common/pipeline.py`:
- `PipelineCoordinator` にロガー注入(既定 `logging.getLogger("voice_translator")`)
- `_put_with_drop` を instance method に変えて、ドロップ発生時に WARN ログ
- ステージ名(q1=Input→Process / q2=Process→Output)+ ドロップ件数(累計)を出力

---

## 完了条件 (Definition of Done)

- [ ] パラメータ変更後の値が `silero_backend.py` / `faster_whisper_backend.py` の既定値に反映されている
- [ ] `condition_on_previous_text=True` が明示的に指定されている
- [ ] `_put_with_drop` が overflow 時に WARN ログを出す
- [ ] 既存テスト(139件) + 新規 overflow ログテスト 全パス
- [ ] 実機で翻訳結果が改善する(目視確認、ユーザレビュー)

---

## 留意事項

- VAD 調整によるレイテンシは +200〜400ms 増えるが、文単位の翻訳精度向上で体感は良くなる想定
- Whisper `beam_size=5` は速度1.5倍程度遅くなる(精度とのトレードオフ)
- これらの値が「現環境(faster-whisper small / NLLB-200 600M / CPU)」での暫定最適値であり、モデル変更時には再評価が必要

---

## 関連ドキュメント
- 改善ロードマップ全体: `tmp/report6.html`
- 全体タスク: [TaskList.md](../TaskList.md)
- アーキテクチャ: [Architecture.html](../Architecture.html)
- テスト項目: [testPlan.md](testPlan.md)
