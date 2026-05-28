# Plan: feature/dropped-utterance-handling

[tmp/report7.html](../../../tmp/report7.html) の調査結果に基づく対応。
キューあふれで捨てられた発話の取りこぼし問題を解消する。

---

## 目的

- 翻訳精度評価のため、ドロップされた発話も含めて **テキストログには確実に残す**
- 不要になったメモリを早めに解放してキューに余裕を持たせる
- TTS 再生時間を短縮して詰まりの発生頻度自体を下げる

---

## スコープ

### IN(A + C + pcm解放)
- **A**: PipelineCoordinator に `on_dropped(items, stage)` コールバック追加 → AppController が TextLogger に渡す
- **pcm解放**: Process スレッドの ASR 完了直後に `utt.pcm = None`(shortcutList A-1 の部分対処)
- **C**: SAPI rate を `config.yaml` で変更可能に(`backends_config.sapi.rate`、既定 180)
  - 関連: `register_default_backends(registry, config)` シグネチャを config 受け取り対応に拡張
- 単体テスト追加

### OUT(今回はやらない)
- B: キューサイズの設定化(shortcutList B-2)。今回は据え置き。
- 完全並列化(案C)
- Utterance ステージ別分割(shortcutList A-1 本格対処)

---

## 実装ステップ

### Step 1: A + pcm 解放(まとめて 1コミット)
1. `pipeline.py`:
   - `__init__` に `on_dropped: Callable[[list[Utterance], str], None] | None = None` 追加
   - `_put_with_drop` でドロップ発話を集めて `on_dropped` 呼び出し
   - Process スレッドの ASR 直後に `utt.pcm = None`
2. `app_controller.py`:
   - `_handle_dropped(items, stage_name)` メソッド追加
   - `_loader_body` で `on_dropped=self._handle_dropped` を Coordinator に渡す
3. テスト追加(後述)

### Step 2: C(SAPI rate config化、別コミット)
1. `config_store.py`: `DEFAULT_CONFIG` に `backends_config.sapi.rate = 180` 追加
2. `backend_setup.py`: `register_default_backends(registry, config=None)` に変更
   - config がある場合、SAPI は config の rate を読むファクトリで登録
3. `app_controller.py`: `register_default_backends(registry, config)` を呼ぶ箇所がもしあれば対応
4. `__main__.py`: 同上
5. テスト追加

---

## 完了条件 (Definition of Done)

- [ ] q2 で発話がドロップされた時、TextLogger に src/tgt が書き出される
- [ ] q1 ドロップ(まだ src 未確定)は TextLogger 側で empty スキップされる
- [ ] Process の ASR 後に `utt.pcm` が `None` になる(メモリ削減)
- [ ] `config.yaml` の `backends_config.sapi.rate` 変更が SAPI に反映される
- [ ] 既存テスト + 新規テスト 全パス

---

## 関連
- 調査: [tmp/report7.html](../../../tmp/report7.html)
- MVP端折りリスト: [tmp/shortcutList.md](../../../tmp/shortcutList.md)(D-2, A-1部分対処, B-1関連)
- 全体タスク: [TaskList.md](../TaskList.md)
- テスト項目: [testPlan.md](testPlan.md)
