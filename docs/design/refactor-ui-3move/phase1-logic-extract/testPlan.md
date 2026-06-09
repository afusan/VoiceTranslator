# P1: logic-extract — テスト計画

作成: 2026-06-10。全テストは **small**(マーカーなし、モックのみ、< 1 秒/件)。
middle / large は本 Phase では追加しない(GUI 実機確認は契約の手チェックで代替)。

---

## 1. 新規テストファイル

### tests/test_logic_ready_state.py

`compute_ready_state` / `filter_active_statuses` の全分岐。

| # | ケース | 期待 |
|---|---|---|
| 1 | 全レイヤ LOADED(audio, DEVICE, input/output あり) | toggle「▶ 開始」normal / status「停止中」/ load「ロード済み」disabled / test「🔊 出力テスト」normal |
| 2 | いずれか MISSING_CREDENTIALS | toggle「認証情報未設定」disabled / status に「詳細ダイアログ」文言 |
| 3 | いずれか DOWNLOADING | toggle「モデル DL 中…」disabled |
| 4 | MISSING_CREDENTIALS と DOWNLOADING 併存 | MISSING_CREDENTIALS が優先(現分岐順) |
| 5 | PROCESS kind + has_input_source=False | toggle「プロセス未選択」disabled |
| 6 | PROCESS kind + has_input_source=True | 通常判定に進む |
| 7 | INIT / NOT_DOWNLOADED 残存 | toggle normal / status「停止中(押下時にロードします)」 |
| 8 | LOADING 残存(MISSING/DL なし) | toggle normal / status「停止中(ロード中)」/ load「ロード中…」disabled |
| 9 | text_only: TTS/OUTPUT が MISSING_CREDENTIALS でも除外され Start 可 | toggle normal |
| 10 | text_only: test ボタン | 「🔊 (TTS なし)」disabled |
| 11 | audio + has_output_device=False | test「🔊 出力未選択」disabled |
| 12 | statuses が空 dict | load「↻ ロード」normal(現挙動: 空なら normal) |
| 13 | filter_active_statuses: audio は全 6 レイヤ / text_only は 4 レイヤ | dict キー検証 |

### tests/test_logic_language_choices.py

旧 `test_settings_panel_lang.py` のシナリオを**全件移植**した上で次を網羅:

| # | ケース | 期待 |
|---|---|---|
| 1 | src: supported=["en","ja","fr"], auto 対応, current="en" | codes 先頭 "auto"、残り sorted、selected="en"、fallback_from=None |
| 2 | src: auto 非対応 | codes に "auto" 無し |
| 3 | src: supported 空 | fallback_pool 使用 |
| 4 | src: current 非対応 + auto 対応 | selected="auto"、fallback_from=current |
| 5 | src: current 非対応 + auto 非対応 | selected=codes[0]、fallback_from=current |
| 6 | src: supported に重複 | dedupe + sort |
| 7 | tgt: "auto" が supported に混入 | 除外される |
| 8 | tgt: current 非対応 → ja あり | selected="ja" |
| 9 | tgt: current 非対応 → ja 無し en あり | selected="en" |
| 10 | tgt: ja/en 両方無し | selected=codes[0] |
| 11 | tts_warning_needed: backend=""/"none" | False |
| 12 | tts_warning_needed: supported 空 | False(不明は警告しない) |
| 13 | tts_warning_needed: current_tgt 対応 | False |
| 14 | tts_warning_needed: current_tgt 非対応 | True |
| 15 | format_*_message 3 種 | 現行文言と一字一句一致(固定文字列 assert) |

### tests/test_logic_backend_display.py

| # | ケース | 期待 |
|---|---|---|
| 1 | tts 変換往復("none" ↔ "(なし)"、通常名は素通し) | 一致 |
| 2 | capture_internal_to_display: kind=DEVICE/PROCESS | 「デバイス (x)」/「プロセス (x)」 |
| 3 | capture_internal_to_display: kind=None / "(未登録)" / 空文字 | 素通し |
| 4 | capture_display_to_internal: 「デバイス (soundcard)」 | "soundcard" |
| 5 | capture_display_to_internal: カッコ無し文字列 | 素通し(防衛挙動) |
| 6 | backend_display_to_internal / internal_to_display: layer 別 dispatch | TTS/CAPTURE/その他 |

### tests/test_logic_status_summary.py

| # | ケース | 期待 |
|---|---|---|
| 1 | **golden**: 全レイヤ + エラー 2 件 + 操作イベント 3 件 | **現 `get_status_summary` + `_refresh_status_text` 合成出力と byte 一致**(書き換え前の実出力をリテラルで固定) |
| 2 | DOWNLOADING 行に dl_size_hint 併記 | `[asr] faster_whisper: Downloading (~2.9GB)` 形式 |
| 3 | エラー 0 件 | 「最近のエラー:」セクションが出ない |
| 4 | エラー 6 件 | 新しい順 5 件で打ち切り |
| 5 | 操作イベント 0 件 | 「操作イベント:」セクションが出ない |
| 6 | 操作イベント 7 件 | 新しい順 5 件、`  ` インデント |
| 7 | ErrorRecord.context 有/無 | ` (ctx)` 付与の有無 |

### tests/test_logic_accel_summary.py

| # | ケース | 期待 |
|---|---|---|
| 1 | cuda あり | ("演算: GPU (cuda)", 緑) |
| 2 | mps あり | ("演算: GPU (mps)", 緑) |
| 3 | cuda + mps | "演算: GPU (cuda, mps)"(sorted) |
| 4 | 全 cpu | ("演算: CPU のみ", 琥珀) |
| 5 | 全 None | ("演算: -(モデル準備中)", slate) |
| 6 | text_only で TTS に "cuda" 報告 | 無視され CPU のみ判定 |
| 7 | device 大文字 "CUDA" | lower 正規化で GPU 扱い |

---

## 2. 既存テストの書き換え

| ファイル | 扱い |
|---|---|
| `test_settings_panel_lang.py` | シナリオを test_logic_language_choices.py へ移植。panel 側には「dropdown.configure / set_setting / banner が呼ばれる」配線 smoke を 2〜3 件残す |
| `test_settings_panel_tts_none.py` | 表示変換部分は test_logic_backend_display.py へ。グレーアウト等の widget 操作テストは現状維持 |
| `test_app_controller.py`(get_status_summary 系 6 件) | 「get_status_snapshot のデータ検証」+「format_status_summary の文字列検証」に分割書き換え。**シナリオは温存、削除しない** |
| `test_text_only_output.py` | `_sync_test_button_state` 等を直接叩いている箇所のみ logic 関数呼び出しに追従。パイプライン系シナリオは触らない |
| `test_control_panel_test_output.py` | ボタン状態の期待値はそのまま。参照先メソッド名の変更にのみ追従 |

**禁止**: 通らなくなったテストの削除。書き換え時は「何のふるまいを守っていたか」を docstring に残す。

---

## 3. 手動チェック(契約)

P1 完了時、実機(GUI 起動)で以下の章を 1 回ずつ踏み、behavioral-contract.md に 🧪 + 日付を記録:

- §1.6〜1.11(ASR/Translator/TTS 切替の言語連動・fallback バナー・CAPTURE kind UI 切替)
- §3.1〜3.6(各ボタン状態遷移)
- §6(アクセラレータ表示: GPU 環境なら cuda、`--device cpu` 相当で CPU のみ)
- §7(ステータス集約: エラー誘発 → 表示 → 操作イベントクリア)
- §9(出力テストボタンの 3 disable 条件 + 再生)

---

## 4. 性能ガード

- 新規テスト 1 件 1 秒超は設計を疑って報告(CLAUDE.md 方針)
- logic 関数は I/O 無しのため、テスト全体への追加実行時間は 1 秒未満が目安
