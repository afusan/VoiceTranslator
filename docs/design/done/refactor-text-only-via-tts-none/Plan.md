# refactor/text-only-via-tts-none — 計画

P3 (`feature/text-only-output`) の改修。出力モードを **独立 ConfigStore キー** で管理する形から、
**TTS backend に「(なし)」を追加して派生判定する形** に統合する。

---

## 1. 動機

P3 で導入した「出力モード」プルダウンは概念が UI 上に増える点で冗長。ユーザの「TTS の選択値で
判断するのが自然」という指摘を受け、設定の表現方法を 1 つにまとめる:

- TTS プルダウンに「(なし)」を追加 → 選択すれば自動的に text_only モード。
- ConfigStore の `pipeline.output_mode` キーは廃止。

---

## 2. スコープ

### in
- `ConfigStore`: `pipeline.output_mode` キーを撤去(後方互換は気にしない)。
- `AppController`:
  - `TTS_NONE = "none"` 定数(BackendRegistry にこの名前は登録しない前提)。
  - `output_mode` プロパティを `backends.tts` 派生に変更(`"none"` / 空 / 未設定 → `text_only`)。
  - `set_setting("backends","tts","none")` で TTS / Output レイヤを **再ロード発火しない** ように分岐(BackendRegistry に "none" backend は無いため評価失敗を避ける)。
- `SettingsPanel`:
  - 「出力モード」プルダウンを撤去。
  - TTS プルダウンの選択肢に `(なし)` を末尾追加(`_tts_display_to_internal` / `_tts_internal_to_display` で内部値 `"none"` と相互変換)。
  - TTS=(なし) のとき Output 行(label/dropdown/設定ボタン)をグレーアウト/disable。TTS 行も label と設定ボタンをグレーアウトする(プルダウンは復帰用に enable)。
  - `(なし)` 選択時はクラウド同意ダイアログ(`_gate_cloud_consent`)を呼ばない。
  - TTS=(なし) のとき `_check_tts_output_lang_compatibility` も warn しない。
- テストの調整: `pipeline.output_mode` を参照していた test_text_only_output 内のクラスを `backends.tts` ベースに書き換え。新規 `tests/test_settings_panel_tts_none.py` で UI 連動を担保。

### out
- BackendRegistry に "none" を実 backend として登録することはしない(UI 側で扱う)。
- Output backend を単体で「(なし)」にする選択肢は提供しない(TTS=(なし) で連動)。
- 動作中の動的切替(audio → text_only の即時反映)は対象外。P4 の「動作中デバイス変更=停止→再開」の枠組みで将来対応。

---

## 3. 変更ファイル

| ファイル | 変更内容 |
|---|---|
| `src/voice_translator/common/config_store.py` | `pipeline.output_mode` の既定値を撤去(コメントで派生方針を残す) |
| `src/voice_translator/common/app_controller.py` | `TTS_NONE` / `output_mode` の判定変更 / `set_setting` の TTS/Output リロード skip |
| `src/voice_translator/gui/settings_panel.py` | 「出力モード」プルダウン撤去 / TTS プルダウンに `(なし)` 追加 / `_apply_tts_none_visual` で Output 行 disable / 表示↔内部値変換ヘルパ |
| `docs/design/Class.md` | 出力モードの記述を派生方式に書き換え(`PipelineCoordinator` 表 / 出力モード節 / `AppController.output_mode`)|
| `tests/test_text_only_output.py` | `TestAppControllerOutputMode` / `TestAppControllerHandleTextReady` / `TestConfigStoreDefault` を派生方式向けに書き換え |
| `tests/test_settings_panel_tts_none.py` | 新規。表示↔内部値変換 / プルダウンに `(なし)` が並ぶ / 内部 "none" 時の初期表示 / `_on_backend_change` で内部値保存 / クラウド gate skip / Output 行 disable |

---

## 4. 設計上のポイント

### 4-1. なぜ「TTS だけ特殊扱い」で済むか

- 出力モードを変えたい意図 = 「TTS を動かしたくない」とほぼ等価。
- TTS が停止すれば Output も意味を失うので、Output 側で独立した選択肢を持たせる必要はない。
- Output backend のキャッシュ(`_backends[OUTPUT]`)は保持してよい(audio に戻したときに再利用できる)。

### 4-2. `"none"` を内部値に選んだ理由

- BackendRegistry の TTS backend 名(`sapi` / `piper` / `elevenlabs` / `openai_tts` / `google_cloud_tts`)と衝突しない短い文字列。
- ConfigStore 保存時に YAML 上で読みやすい(`backends.tts: none`)。
- `None`(Python の None / YAML の null)を使うと `get_setting` のデフォルトと区別しづらいので採用しない。

### 4-3. UI 上の disable 状態

- TTS=(なし) のとき:
  - **Output 行**: label の text_color を灰色化、dropdown と「設定」ボタンを `state=disabled`。
  - **TTS 行**: label と「設定」ボタンをグレーアウト。dropdown は enable(復帰用に「(なし)」以外を選び直せる必要があるため)。
- TTS=実 backend に戻すと全要素 enable に復帰。

### 4-4. テストの追加範囲

| 観点 | 件数 |
|---|---|
| 表示↔内部値ヘルパ | 4 |
| TTS プルダウンの選択肢 | 1 |
| 起動時の StringVar 初期値 | 2 |
| `_on_backend_change` の挙動(none / クラウド gate / 実 backend) | 3 |
| Output 行の disable / enable / 切替時 | 3 |
| **合計** | **13** |

加えて、既存の `test_text_only_output.py` の `TestAppControllerOutputMode` / `TestAppControllerHandleTextReady` / `TestConfigStoreDefault` を `backends.tts` ベースに書き換えて、出力モード派生の挙動を担保。

---

## 5. 確認手順

1. `py -m uv run pytest tests/test_text_only_output.py tests/test_settings_panel_tts_none.py tests/test_settings_panel_lang.py tests/test_settings_panel_sections.py tests/test_app_controller.py` で関連が緑。
2. `py -m uv run pytest -q --ignore=tests/test_google_cloud_tts_large.py` で small 全体が緑(本作業ブランチで 916 件 pass 確認済み)。
3. 実機:
   - 「バックエンド」セクションを開く → TTS プルダウン末尾に「(なし)」が並ぶ。
   - 「(なし)」選択 → Output 行が灰色化(label/dropdown/設定ボタンが操作不可)。
   - 設定保存 → 再起動 → 縦通しでテキストのみ出る(音は鳴らない)。
   - TTS を SAPI 等に戻す → Output 行が活性化、ロード後に通常動作。
