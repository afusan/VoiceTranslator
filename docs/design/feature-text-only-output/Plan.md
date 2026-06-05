# feature/text-only-output — 計画

メタ計画 [feature-runtime-flex-and-input](../feature-runtime-flex-and-input/Plan.md) の **Phase 3**。
TTS / Output を動かさず、翻訳テキストの確定で完了扱いとなる「**text_only モード**」を追加する。

---

## 1. 目的

- TTS 不要な利用シーン(字幕として出すだけで十分、視聴中の音声を邪魔したくない、TTS の品質不満)に対応。
- クラウド TTS の API 課金を回避したいユーザにも有効。
- 「出力 backend がなくても動くアプリ」として配布対象を広げる。

---

## 2. スコープ

### in
- ConfigStore に `pipeline.output_mode: "audio" | "text_only"`(既定 `audio`)を追加。
- `PipelineCoordinator` に `output_mode` パラメータ。`text_only` のとき:
  - Input / ASR / Translator の 3 スレッドのみ起動(TTS / Output は起動しない)。
  - Translator 完了で `on_text_ready` を発火、レジャを `pop` してバッファ即解放。
  - `translated_queue` / `synthesized_queue` には何も流れない。
  - `on_utterance_done` は呼ばない(Output 概念なし)。
- `AppController`:
  - `output_mode` プロパティ。
  - `_active_layers()` で `text_only` のとき TTS / Output を除外。
  - `load_models` / `load_auto_load_layers_async` / `_check_missing_credentials_gate` で同除外。
  - `_start_coord` で `output_mode` を Coordinator に渡し、`text_only` 時は TTS/Output 引数を None にする。
  - `_handle_text_ready` で text_only のとき jsonl / processtime / `_push_recent_durations` を兼ねる。
- `SettingsPanel`:
  - 「バックエンド」セクションに「出力モード」プルダウン。`text_only` 選択で TTS / Output 行を disable/グレーアウト。
- `ControlPanel`:
  - 「全 LOADED」「演算」表示で TTS / Output を除外。
- 既存テスト群が変わらず通る(audio モードの回帰確認)。
- 新規 small / middle テスト(バッファ処理回り重点)。

### out
- 出力モード切替の `auto-reload`(audio → text_only に切り替えた瞬間に Coordinator を作り直す等)は対象外。設定変更後の再 Start 時に新モードが反映されればよい。
- TTS / Output backend のレイヤ自体の撤廃は対象外(将来 audio に戻したいユーザのため)。

---

## 3. 変更ファイル

| ファイル | 変更内容 |
|---|---|
| `src/voice_translator/common/config_store.py` | `DEFAULT_CONFIG["pipeline"]["output_mode"] = "audio"` を追加 |
| `src/voice_translator/common/pipeline.py` | docstring 更新 / `output_mode` パラメータ追加 / `tts`/`output` を Optional 化 / `start` / `stop` / `_translator_loop` に分岐 |
| `src/voice_translator/common/app_controller.py` | `output_mode` プロパティ / `_active_layers` / `load_models` 等の対象絞り込み / `_start_coord` で TTS/Output を null 注入 / `_handle_text_ready` で text_only 時にログ書き出しを兼ねる |
| `src/voice_translator/gui/settings_panel.py` | 「出力モード」プルダウン追加。`_apply_output_mode_to_rows` で TTS/Output 行を disable/グレーアウト |
| `src/voice_translator/gui/control_panel.py` | `_active_layer_statuses` ヘルパ追加 / `_sync_ready_state` と `_refresh_accel_label` で TTS/Output を除外 |
| `docs/design/Class.md` | PipelineCoordinator / AppController の表に text_only モードを記述 |
| `tests/test_text_only_output.py` | 新規。下記の観点を網羅 |

---

## 4. 設計上のポイント

### 4-1. 「モード切替で Coordinator は作り直す」前提

`PipelineCoordinator` は `__init__` で `output_mode` を受け取り、以降は不変。
動作中にモード切替したい場合は **stop → start で新 Coordinator を作る** のが基本。
ConfigStore に書いた値は **次回 Start** で反映される。

これは「audio 動作中に text_only に切替」が低頻度なことを前提にした選択。動作中切替が必要に
なったら別 issue で「mode_swap」を実装する余地を残しておく(複雑度が跳ねるので今回は避ける)。

### 4-2. text_only でのバッファ解放

メモリリーク防止のため、Translator 完了の最後に **必ず** `ledger.pop(seq_id)` する。
- `_translator_loop` の終端で text_only モードなら `record = ledger.pop(seq_id)` を取り、`on_text_ready(record)` に渡す。
- 例外: コールバック中の例外は本体スレッドに伝播させず、ログだけ残す(他発話の処理が止まらないように)。
- 空翻訳(`tgt_text` が空)もすでに `ledger.pop` で解放されるので二重処理にならない。

### 4-3. 既存の audio モードとの互換

- `output_mode="audio"`(既定)では `__init__` で `tts` / `output` が必須(`None` だと `ValueError`)。
- `audio` モードの分岐ロジックは触らない(回帰リスク最小化)。
- `_handle_text_ready` の振る舞いは「`output_mode == "text_only"` なら最終扱い、それ以外は UI 通知のみ」で分岐する。

### 4-4. UI 上の見せ方

- 「出力モード」プルダウン:「音声で出力(既定)」「テキストのみ(TTS/Output なし)」。
- `text_only` で TTS / Output 行は dropdown / 「設定」ボタンを disable、ラベルは灰色化。
  状態自体は維持(audio に戻したらそのまま使える)。
- 「↻ ロード」ボタンの活性化判定からは TTS / Output 状態が除外される。

### 4-5. テスト戦略(バッファ重点)

| 観点 | 種別 | 内容 |
|---|---|---|
| スレッド構造 | small | text_only 起動時に tts_thread / output_thread が None / Input/ASR/Translator は alive |
| on_text_ready 発火 | small | text_only で発話を流すと on_text_ready 受信、on_utterance_done は受信しない |
| TTS/Output 非実行 | small | text_only に TTS/Output spy backend を渡しても synthesize/play 0 回 |
| ledger 解放 | small | ready した seq_id を `ledger.peek` すると空 dict(残骸ゼロ) |
| キュー未使用 | small | `_translated_queue.qsize() == 0` / `_synthesized_queue.qsize() == 0` |
| audio→text_only restart | small | audio で 1 周 → 新 Coordinator(text_only)で 1 周 → ready 後にキュー/レジャ空 |
| 同一 Coordinator stop→start | small | text_only で 2 周 → 2 周目開始時の drain が効いている |
| audio モード回帰 | small | audio で従来通り output.play まで到達、`t_playback` がタイムラインに乗る |
| `_active_layers` | small | audio で 6 レイヤ / text_only で 4 レイヤ(TTS/Output 除外) |
| `_handle_text_ready` のログ書き出し | small | text_only で `translation_logger.write_record` / `process_time_logger.write_record` 呼出 / audio では呼ばない |
| ログ書き出し失敗時 | small | jsonl / processtime が例外でも UI 通知は届く |
| ConfigStore | small | デフォルト値 `"audio"` / 既存 yaml 読み込みで保持される |

middle 階層(WAV 流し込みの縦通し)は test_pipeline_e2e に乗せない(text_only は 3 スレッドで小さく検証可能なので small で十分)。

---

## 5. 確認手順(手動 / 開発者向け)

1. `py -m uv run pytest tests/test_text_only_output.py tests/test_pipeline.py tests/test_pipeline_e2e.py tests/test_app_controller.py` で関連が緑。
2. `py -m uv run pytest -q --ignore=tests/test_google_cloud_tts_large.py` で small 全体が緑(本作業ブランチで 901 件パス確認済み)。
3. 実機:
   - 設定パネル「バックエンド」セクションを開き「出力モード」を「テキストのみ」に切替 → 設定保存 → 再起動(or `↻ ロード` → 開始)。
   - 翻訳結果が履歴に出るが音は鳴らない / TTS / Output の行は灰色 / ロードボタンも TTS/Output を無視する。
   - 「音声で出力」に戻すと TTS / Output 行が活性化、ロード後に通常動作に復帰する。
