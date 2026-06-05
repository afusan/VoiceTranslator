# feature/dynamic-languages — 計画

メタ計画 [feature-runtime-flex-and-input](../feature-runtime-flex-and-input/Plan.md) の **Phase 2**。
動作中の SettingsPanel で入力/出力言語を変えたとき、**次の発話から** 新言語が反映される
ようにする。バックエンドの変更は含まない。

---

## 1. 目的

ドッグフーディング中の自然な要望:
- 字幕がついた素材を見ているとき、入力素材が複数言語になることがある(英語 → 中国語に切替)
- 翻訳結果として欲しい言語を途中で変えたい(英語 → 日本語に切替)

毎回パイプラインを停止/再開する UX は重い。**動作を維持したまま** 設定変更が反映されるのが理想。

---

## 2. スコープ

### in
- `PipelineCoordinator.set_languages(*, src=None, tgt=None)` を追加。動作中の `_src_lang` / `_tgt_lang` を atomic に差し替える。
- `AppController.set_setting("languages", "src"|"tgt", value)` に「Coordinator が動作中なら転送」のロジックを追加。
- 既存の SettingsPanel ハンドラ(`_on_src_lang_changed` / `_on_tgt_lang_changed`)はそのまま使う(`set_setting` 経由なので AppController 側で吸収)。

### out
- 「進行中の発話」のキャンセル/再投入(captured_queue に入っている発話を巻き戻して新言語で訳す)は対象外 → pendList の「提案 C」へ。
- Translator backend / TTS backend 自体の差し替え(動作中)は対象外。
- src 言語の自動検出ロジックの変更は対象外(従来通り、auto なら ASR が検出した値を採用)。

---

## 3. 変更ファイル

| ファイル | 変更内容 |
|---|---|
| `src/voice_translator/common/pipeline.py` | `PipelineCoordinator.set_languages` を追加(get_drop_counts の直後)。docstring で「次発話から反映」と「lock 不要(参照型代入の atomicity)」を明記 |
| `src/voice_translator/common/app_controller.py` | `set_setting` の末尾に languages 系の中継処理を追加。`is_running` チェック付き |
| `tests/test_dynamic_languages.py` | 新規。`set_languages` の挙動 / AppController の中継 |
| `tests/test_pipeline_e2e.py` | `test_set_languages_takes_effect_on_next_utterance` を追加。動作中切替の縦通し確認(モック backend) |

---

## 4. 設計上のポイント

### 4-1. lock の要否

`self._src_lang` / `self._tgt_lang` は **str 参照1個** で持つ。Python では str 参照型の単純代入は
GIL によって atomic なので、書き換え/読み出しが他スレッドと競合してもデータレースは起きない。

ただし「複数フィールド(src と tgt)を 1 つの set_languages 呼び出しで同時更新したい」場合、
古い src + 新 tgt の組合せが瞬間的に観測される可能性は理論上残る。これは「次発話」単位での反映で
問題なく、ユーザは中間状態を意識しない(UI 側もそれぞれ別ハンドラから呼ぶ)。

→ Lock は導入しない。

### 4-2. 「進行中発話」の扱い

| 発話の状態 | 反映タイミング |
|---|---|
| まだ capture されていない | 新 src / 新 tgt の両方が反映される |
| capture 済み、ASR/Translator 未到達 | src は古い(`RawPayload.src_lang_hint` は capture 時点で確定済み) / tgt は **Translator 通過時の最新値** |
| Translator 通過済み、TTS 未到達 | src/tgt とも `TranslatedPayload` の値(古い) |
| 全完了済み | 影響なし |

完全な一貫性を持たせるなら「captured_queue を drain + 再投入」が必要だが、過剰実装。
ユーザの主要な使い方は「会話/視聴中に切り替えて、これから来る発話に効く」なので OK。

### 4-3. AppController の中継位置

`set_setting` の `backends` 中継の直後に追加する。理由:
- 同じ「設定変更を即時反映する」パターンで横並びに置ける。
- `is_running` チェックは `self._coord is not None and self._coord.is_running` で AND。
  Coordinator が None(未起動) なら従来通り ConfigStore のみ更新、次回 Start で新値が乗る。

### 4-4. テスト戦略(small / middle)

| 階層 | 対象 |
|---|---|
| small | `PipelineCoordinator.set_languages` の挙動(両更新 / None 維持 / no-args noop / str 強制 / 新 payload 反映) |
| small | `AppController.set_setting` の中継(running → 呼ぶ / 停止中 → 呼ばない / languages 以外 → 呼ばない / 値の str 強制 / 未知キー → 中継しない) |
| middle(test_pipeline_e2e) | WAV を流して動作中に `coord.set_languages(tgt="en")` → 後続発話が新言語で完了する |

---

## 5. 確認手順(手動 / 開発者向け)

1. `py -m uv run pytest tests/test_dynamic_languages.py tests/test_pipeline_e2e.py tests/test_pipeline.py tests/test_app_controller.py` が緑。
2. 実機で `py -m voice_translator` を立ち上げ → 動作中に「翻訳」セクションの出力言語を切り替え → 次の発話以降に新言語の翻訳が表示される(TTS も新言語で読まれる、対応 backend の場合)。
