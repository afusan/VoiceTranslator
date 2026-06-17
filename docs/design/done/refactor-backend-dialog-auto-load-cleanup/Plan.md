# Plan: refactor-backend-dialog-auto-load-cleanup

## 目標

バックエンドの「設定」ダイアログに残っている「起動時に自動ロード(auto_load)」の
スイッチ/設定キー/ロジックを、コードベースから完全に削除する。

`branch-lazy-load-auth-gate` で確定した「変更即ロード廃止 / Start / ↻ ロード 2 経路」
方針のもとでは `auto_load=True` で起動時に先行ロードする 3 つ目の経路は不要と
判断されたが、UI 上のスイッチ・ConfigStore のキー・AppController のメソッド・
i18n カタログ・テストが残骸として存在している状態を解消する。

## 非目標

- lazy ロード(Start 押下時にロード)という現行方針の変更
- ダイアログのその他項目のリファクタ・整理
- Architecture の構造変更(スレッドモデルそのものは変えない)
- master へのマージ(マージは利用者の明示指示があってから)

## 削除対象一覧

### A. `src/voice_translator/gui/layer_settings_schema.py`

| 対象 | 行番号 | 内容 |
|------|--------|------|
| 関数 `_auto_load_toggle` | 183〜192 | SettingField を生成するヘルパ関数ごと削除 |
| docstring のコメント | 17行目 | `"toggle"` の説明に `auto_load 等で利用` とある部分を修正 |
| `_auto_load_toggle("soundcard")` 呼び出し | 306行 | CAPTURE セクション |
| `_auto_load_toggle("silero")` 呼び出し | 320行 | VAD セクション |
| `_auto_load_toggle("webrtcvad")` 呼び出し | 340行 | VAD セクション |
| `_auto_load_toggle("pyannote")` 呼び出し | 357行 | VAD セクション |
| `_auto_load_toggle("pvcobra")` 呼び出し | 366行 | VAD セクション |
| `_auto_load_toggle("faster_whisper")` 呼び出し | 385行 | ASR セクション |
| `_auto_load_toggle("openai_whisper")` 呼び出し | 397行 | ASR セクション |
| `_auto_load_toggle("openai_whisper_api")` 呼び出し | 407行 | ASR セクション |
| `_auto_load_toggle("google_stt")` 呼び出し | 418行 | ASR セクション |
| `_auto_load_toggle("deepgram")` 呼び出し | 428行 | ASR セクション |
| `_auto_load_toggle("nllb200")` 呼び出し | 447行 | TRANSLATOR セクション |
| `_auto_load_toggle("deepl")` 呼び出し | 449行 | TRANSLATOR セクション |
| `_auto_load_toggle("openai_gpt")` 呼び出し | 459行 | TRANSLATOR セクション |
| `_auto_load_toggle("anthropic_claude")` 呼び出し | 469行 | TRANSLATOR セクション |
| `_auto_load_toggle("sapi")` 呼び出し | 480行 | TTS セクション |
| `_auto_load_toggle("piper")` 呼び出し | 491行 | TTS セクション |
| `_auto_load_toggle("elevenlabs")` 呼び出し | 510行 | TTS セクション |
| `_auto_load_toggle("openai_tts")` 呼び出し | 530行 | TTS セクション |
| `_auto_load_toggle("google_tts")` 呼び出し | 548行 | TTS セクション |
| `_auto_load_toggle("soundcard")` 呼び出し(OUTPUT) | 558行 | OUTPUT セクション |
| `layer_settings_dialog.py` 内の evict 除外ガード | 534行 | `keys[2] != "auto_load"` の分岐 |

### B. `src/voice_translator/gui/layer_settings_dialog.py`

| 対象 | 行番号 | 内容 |
|------|--------|------|
| 保存時 evict 除外ガード | 484行と534行 | コメント「`auto_load` は load 時パラメータでないので除外」と `and keys[2] != "auto_load"` の行 |

### C. `src/voice_translator/gui/i18n.py`

| ロケール | 行番号 | 内容 |
|----------|--------|------|
| ja | 81〜85行 | `"layer_settings.auto_load.label"` と `"layer_settings.auto_load.help"` キー(複数行値含む) |
| en | 426〜430行 | 同上(en) |
| zh | 754〜758行 | 同上(zh) |
| es | 1070〜1074行 | 同上(es) |

4 言語合計 8 キー(label + help × 4 ロケール)を削除。
カタログ整合テスト(`test_i18n.py::test_catalog_key_parity`)が全ロケール同時削除を検証するため、4 言語必ず同時削除する。

### D. `src/voice_translator/common/config_store.py`

| 対象 | 行番号 | 内容 |
|------|--------|------|
| コメント説明 | 91〜93行 | `# 全 backend 共通の新キー(Phase B): auto_load: bool ...` コメントブロック |
| `soundcard` の `"auto_load": False` | 96行 | キー削除(soundcard ブロックが空になるなら `{}` 残置または他キーと同居) |
| `proctap` の `"auto_load": False` | 99行 | キー削除(他キー `resample_quality` と同居) |
| `sapi` の `"auto_load": False` | 106行 | キー削除(他キー `rate` と同居) |
| `silero` の `"auto_load": False` | 110行 | キー削除(他キー `threshold` 等と同居) |
| `faster_whisper` の `"auto_load": False` | 119行 | キー削除(他キー `device` 等と同居) |
| `nllb200` の `"auto_load": False` | 131行 | キー削除(他キー `device` と同居) |

`soundcard` は `auto_load` しか持っていないため、削除後はブロック自体を `{}` にする
(他の backend と辞書キーの有無で扱いが変わることを防ぐ。将来のキー追加先は残す)。

### E. `src/voice_translator/common/app_controller.py`

| 対象 | 行番号 | 内容 |
|------|--------|------|
| メソッド `get_auto_load_layers` | 1010〜1030行 | メソッドごと削除 |
| メソッド `load_auto_load_layers_async` | 1032〜1064行 | メソッドごと削除 |
| コメント(docstring) | 113行 | `auto_load のタイミング` 言及を削除 |
| コメント | 462行 | `/ auto_load に寄せる方針` の言及を `/ ↻ ロードに寄せる方針` に修正 |
| コメント | 543行 | `/ 起動時 auto_load の 3 経路` を `の 2 経路` に修正 |
| コメント | 747行 | `load_auto_load_layers_async` 言及を削除 |

### F. `src/voice_translator/gui/main_window.py`

| 対象 | 行番号 | 内容 |
|------|--------|------|
| docstring の記述 | 3〜6行 | `auto_load=True` が指定されている backend のレイヤだけを先行ロードする ... の説明文を削除・書き換え |
| `load_auto_load_layers_async()` 呼び出し | 71〜73行 | コメント含む auto_load 起動処理を削除 |

### G. テストファイル

| ファイル | 対象 | 内容 |
|----------|------|------|
| `tests/test_app_controller.py` | クラス `TestPhaseBAutoLoad`(1050〜1107行) | クラスごと削除 |
| `tests/test_app_controller.py` | クラス `TestPhaseBConfigDefaults::test_auto_load_defaults_false_for_all_backends`(1170〜1179行) | メソッドごと削除。クラス内に別のテストが残るのでクラスは残す |
| `tests/test_app_controller.py` | コメント(606行) | `auto_load に寄せる` の言及を修正 |
| `tests/test_layer_settings_schema.py` | `test_backend_filter_excludes_other_backends` メソッド内のコメントとアサーション(246〜253行) | auto_load トグルで検証していた部分を「フィルタロジック確認」の意図を保ちつつ書き換え |
| `tests/test_vad_switching.py` | 9行目のコメント | `auto_load` 言及を削除 |

### H. ドキュメント

| ファイル | 対象 | 内容 |
|----------|------|------|
| `docs/design/Class.md` | 122行 `load_auto_load_layers_async` のメソッド行 | 行ごと削除 |
| `docs/design/Class.md` | 136〜137行のライフサイクル図 | `auto_load` 経路の記述を削除 |
| `docs/design/Class.md` | 148行のコメント | `/ auto_load` 言及を削除 |
| `docs/design/Class.md` | 112行のコメント | `/ auto_load の 3 経路` を `の 2 経路` に修正 |
| `docs/design/Architecture.html` | 288〜290行 Loader スレッドの説明 | `auto_load` 言及を削除し現状に合わせる |

## 削除順序(壊れにくい順)

1. **i18n カタログ削除** (C) — 他に依存なし。先に消すと `test_catalog_key_parity` が
   欠落キー検出で赤になるが、schema 同時削除で緑に戻る。4 言語同時削除必須。

2. **schema のヘルパ + 全呼び出し削除** (A) — i18n キーを消した後。
   スキーマから `auto_load` フィールドが消えることでダイアログに項目が出なくなる。

3. **dialog.py の evict 除外ガード削除** (B) — schema から auto_load フィールドが
   消えているため、ガードが実際に評価されることはなくなっているが明示的に削除する。

4. **config_store.py の `auto_load` キー削除** (D) — DEFAULT_CONFIG から消す。
   既存の保存済み YAML に残っていても `config.get()` で値が残るだけで、
   それを読むコードがなくなるため実害なし。後方互換シムは作らない(CLAUDE.md 方針)。

5. **app_controller.py のメソッドとコメント削除** (E) — get/load メソッドを削除。

6. **main_window.py の呼び出し削除** (F) — E を削除してから呼び出し側を削除。

7. **テスト削除・修正** (G) — 実装が固まってから。

8. **ドキュメント更新** (H) — 最後に現状に合わせる。

## テスト戦略

### 既存テストの修正・削除

| テスト | 対処 | 理由 |
|--------|------|------|
| `TestPhaseBAutoLoad` クラス全体 | **削除** | 機能ごと消えるため、守るべき契約がない |
| `test_auto_load_defaults_false_for_all_backends` | **削除** | 上と同様 |
| `test_backend_filter_excludes_other_backends` | **書き換え** | フィルタロジック自体の検証は意味があるが、auto_load トグルへの言及は削除。「他 backend のフィールドが除外される」という構造を `toggle` 型以外で確認する形に修正 |
| `test_catalog_key_parity` | **変更なし** | auto_load キーを 4 ロケール同時削除すれば整合が保たれ、テストは引き続き正常に機能する |
| コメント修正 | 各所の「auto_load」言及コメントを現状に合わせて修正 | 誤解を招く記述を消す |

### 新規 small テストの要否

新規テストは **不要**。削除後に守るべき以下の不変条件は既存テストがカバーする。

- ダイアログが表示するフィールドはスキーマ駆動(スキーマに項目がなければ UI に出ない) → `test_layer_settings_schema.py` の既存 visible_fields テスト
- 保存時に backend_config が変わったら evict → 既存の dialog 保存ラウンドトリップ / evict テスト
- i18n カタログ整合 → `test_catalog_key_parity`

ただし、`test_backend_filter_excludes_other_backends` の書き換え後にアサーションが
意味のある内容になっているかを確認してから commit する。

## 影響範囲とリスク

### 機能的影響

- **ユーザへの影響**: 「設定」ダイアログに「起動時に自動ロード」スイッチが表示されなくなる。
  既定 OFF かつ利用者が ON にしていた場合は、次回起動時の自動ロードが行われなくなる。
  代替: Start ボタン押下時に必要なレイヤを遅延ロードする(現行動作が維持)。
- **既存設定ファイルへの影響**: 保存済み `config.yaml` に `auto_load: true` が残っていても
  読むコードがなくなるため無視される。後方互換シムは作らない(CLAUDE.md 方針)。

### リスク

| リスク | 程度 | 対策 |
|--------|------|------|
| i18n の 4 ロケール削除漏れ → `test_catalog_key_parity` が赤 | 低(一括削除で防げる) | 削除時に Grep で全ロケール確認 |
| `test_backend_filter_excludes_other_backends` の書き換えが「テストを甘くする」方向になる | 中 | 書き換えてアサーションが意味のある内容かレビューする |
| AppController の 2 メソッド削除が他の呼び出し元を見落とす | 低(Grep で確認済み、main_window のみ) | 削除後に `py -m uv run pytest -q` で一気通貫確認 |
| Class.md / Architecture.html の更新漏れ | 低 | 削除後に Grep で残存確認 |

### 後方互換

後方互換シムは **作らない**。`auto_load` キーが既存 YAML に残っていても読むコードがない
ため実害なし。CLAUDE.md「後方互換ハックを書かない」方針に従う。
