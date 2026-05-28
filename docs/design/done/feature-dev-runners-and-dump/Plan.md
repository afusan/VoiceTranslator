# feature/dev-runners-and-dump — レイヤ単体検証環境とパイプラインダンプ

> ブランチ: `feature/dev-runners-and-dump`
> 起票日: 2026-05-28
> 関連: [Architecture.html](../Architecture.html) / [Class.md](../Class.md) / [pendList.md](../pendList.md)

## 1. ゴール

各レイヤ(Capture / VAD / ASR / Translator / TTS / Output)を **CUI から単体で実行できる検証環境** を整備し、モデル/パラメータ/入力音声を切り替えながら結果を確認できるようにする。最終的には任意の **部分パイプライン**(例: VAD→ASR→Translator)も CUI から流せる状態を目指す。

加えて、**本体パイプラインに「ステージ間データのダンプ機能」を追加** し、yaml で ON/OFF できるようにする。実機動作で発生した問題のあるデータを後からランナーで再現できるようにする。

検証ループを「実機 → 問題発見 → ダンプを単体ランナーで突き合わせ → パラメータ調整 → 再計測」という形に閉じることが本ブランチの主目的。

## 2. スコープ

### やる
- 各レイヤの **CUI 単体ランナー**(`python -m voice_translator.dev.runner_<layer>`)
- 部分パイプラインランナー(同 `runner_pipeline`)
- 本体パイプラインの **ステージ間ダンプ機能**(yaml で ON/OFF、出力先指定)
- ランナーとダンプで使う **共通データ形式の規約**(WAV / JSON)
- `ConfigStore` の追加キー(`pipeline.dump.*`)
- 上記に伴う docs(Class.md / Architecture.html / pendList.md)の更新
- small テスト(モック)/ middle テスト(実 WAV + 実バックエンド)の追加

### やらない(本ブランチでは)
- パラメータ調整自体(本ブランチで作る環境を**使って**別ブランチで実施)
- 新規バックエンドの追加(faster-whisper の medium/large を含む)
- GUI からのダンプ ON/OFF(yaml で十分。GUI 化は要望が出てから)
- リモート CI(配布方針通りローカル完結)

## 3. 背景・動機

直近の `refactor/asr-gpu-compute-type` で GPU 環境の ASR 速度が改善し、`small → medium` 等のモデル昇格や、各レイヤパラメータの本格的なチューニングが視野に入った([pendList.md](../pendList.md) 2026-05-28「Whisper モデルサイズ引き上げ検討」、同「翻訳バックエンドの生成パラメータ」)。

しかし現状の検証手段は **GUI から本体パイプラインを縦通しで回す** しかなく、

- 同じ入力で 1 レイヤだけパラメータを変えて比較したい / 翻訳の degenerate を再現するためにあの 920文字英文を直接 NLLB に投げたい / ASR の認識ミスを別モデルで突き合わせたい — といった**部分検証が現実的でない**(毎回マイクから録り直し、毎回全レイヤをロード)。
- 問題発生時のデータ(ASR が崩れた発話、TTS が暴走した翻訳テキスト)を**後から再現できない**(textログには出るが PCM が残らない)。

本ブランチでこの 2 つを同時に解消する。

## 4. 設計の前提

- 既存バックエンドの **抽象 I/F**(`AsrBackend.transcribe(pcm, hint) -> (text, lang)` 等、[Class.md](../Class.md) §1)を **そのまま使う**。ランナーは I/F の上に被せる薄い CLI ラッパに留める。
- **配布方針**([CLAUDE.md](../../../CLAUDE.md) / [pendList.md](../pendList.md))に従い、CPU で動作可能。device は既存の `device="auto"` 引数で吸収。GPU/CPU 別実装は作らない。
- **コードパスは 1 本**を維持。ランナーは ConfigStore + CLI 引数を組み合わせて既存バックエンドを生成するだけで、新しい処理ロジックは持ち込まない。
- ダンプ機能は **既存パイプラインに対して非侵襲**(無効時はオーバーヘッドゼロ、有効時のみフックが発火)とする。

## 5. 全体構成

```
                        ┌────────────────────┐
   実機(GUI/本体)     │ PipelineCoordinator │ ──┐
                        └────────────────────┘   │
                              │ (各ステージで)   │  pipeline.dump.enabled=true
                              ▼                   │
                        ┌────────────────────┐   │
                        │ StageDumpWriter    │ ──┘
                        └────────┬───────────┘
                                 ▼
              <dump_dir>/<run_id>/seq_NNNN_<stage>.{wav,json}
                                 │
                                 │ (これを入力にできる)
                                 ▼
   検証                 ┌────────────────────────┐
   (CUI)               │ voice_translator.dev   │
                        │   runner_capture       │ ── mic → wav
                        │   runner_vad           │ ── wav → wavs + json
                        │   runner_asr           │ ── wav → json
                        │   runner_translator    │ ── json/text → json
                        │   runner_tts           │ ── text/json → wav
                        │   runner_output        │ ── wav → 再生
                        │   runner_pipeline      │ ── 任意の連結
                        └────────────────────────┘
```

## 6. 共通: データ形式の規約

ダンプとランナーが**同じ形式**を読み書きするのが要点(=ダンプをそのままランナーに食わせられる)。

### 6.1 PCM = WAV
- フォーマット: **16kHz / mono / float32 PCM**(プロジェクト内部標準と一致、CLAUDE.md「使用技術」参照)。
- ただし TTS 出力など実サンプルレートが 16kHz でないケースもあるため、**WAV ヘッダに乗っているサンプルレートを正**とし、必要に応じてランナー側で扱う。
- ファイル拡張子: `.wav`。

### 6.2 テキスト/メタ = JSON
- 1 ステージ 1 ファイル、UTF-8、改行 LF、`indent=2`。
- スキーマ(共通フィールド + ステージ別フィールド):

```jsonc
// seq_NNNN_asr.json
{
  "seq_id": 42,
  "stage": "asr",
  "produced_at": "2026-05-28T15:49:27.100",
  "src_lang": "en",
  "text": "David Muir, ABC's World News Tonight ..."
}

// seq_NNNN_translate.json
{
  "seq_id": 42,
  "stage": "translate",
  "produced_at": "2026-05-28T15:49:27.560",
  "src_lang": "en",
  "tgt_lang": "ja",
  "src_text": "...",
  "tgt_text": "..."
}
```

### 6.3 run 単位のメタ = `run.json`
- ダンプ走行 1 回ごとに `<dump_dir>/<run_id>/run.json` を生成。
- 内容: 起動時刻、各バックエンド名、各バックエンド設定(`backends_config` のスナップショット)、device 解決結果、git rev(取れれば)。
- ランナー側はこれを読まなくても動くが、人間が「どの設定で取れたダンプか」を後から特定する用。

### 6.4 ファイル名規約
- ダンプ: `seq_<4桁>_<stage>.<ext>`。例: `seq_0042_vad.wav`、`seq_0042_asr.json`、`seq_0042_tts.wav`。
- `<stage>` ∈ { `vad`, `asr`, `translate`, `tts` }(Capture は VAD 入力前の生 PCM = 通常はダンプ対象外。必要時のみ別フラグで)。
- `<run_id>` は `YYYYMMDD-HHMMSS`(秒精度)+ 必要なら衝突回避 suffix。

## 7. 変更1: PipelineCoordinator のダンプフック

### 7.1 設計の原則
- **既存ロジックに分岐を撒かない**:各ステージの末尾で `self._dump.on_<stage>(seq_id, ...)` を 1 行呼ぶだけ。
- **無効時はオーバーヘッドゼロ**:`self._dump` は無効時 `_NullDumpWriter`(全メソッドが no-op)を使う Null Object パターン。条件分岐ではなく多態で吸収。
- **書き込みはバックグラウンド**:I/O ブロックがパイプラインに波及しないよう、内部に小さなワーカスレッドを持つ。失敗してもパイプラインは止めない(WARN ログのみ)。

### 7.2 新規クラス
| クラス | 役割 |
|---|---|
| `StageDumpWriter`(`src/voice_translator/common/stage_dump.py`) | ステージごとのデータを `<dump_dir>/<run_id>/` に書き出す。`on_vad(seq_id, pcm, samplerate)` / `on_asr(seq_id, text, src_lang)` / `on_translate(seq_id, src_text, src_lang, tgt_text, tgt_lang)` / `on_tts(seq_id, pcm, samplerate)` / `start_run(meta)` / `stop_run()`。書き込みは内部の単一ワーカスレッド経由。 |
| `NullStageDumpWriter` | 同 I/F の no-op 実装。`pipeline.dump.enabled=false` のとき注入。 |

### 7.3 PipelineCoordinator への注入
- `__init__` に `dump: StageDumpWriter | None = None` を追加(None なら NullStageDumpWriter を内部生成)。
- `start()` で `dump.start_run(meta)`、`stop()` で `dump.stop_run()`。
- 各ループの該当箇所(例: `_asr_loop` の `recognized_queue` への put 前)で `self._dump.on_asr(...)` を呼ぶ。

### 7.4 AppController からの結線
- `ConfigStore` の `pipeline.dump.enabled` を見て `StageDumpWriter` か `NullStageDumpWriter` を生成し、Coordinator に渡す。
- 出力先は `pipeline.dump.directory`(既定 `./logs/dumps`)。`log.directory` とは独立(ダンプはサイズが大きく、別管理にしたい)。

## 8. 変更2: 各レイヤの単体 CLI ランナー

### 8.1 配置
新規モジュール `src/voice_translator/dev/`:

```
src/voice_translator/dev/
├── __init__.py
├── runner_capture.py     # マイク or デバイス → wav
├── runner_vad.py         # wav → 切り出し wav群 + json
├── runner_asr.py         # wav → json
├── runner_translator.py  # json/text → json
├── runner_tts.py         # text/json → wav
├── runner_output.py      # wav → 再生
├── runner_pipeline.py    # 部分パイプライン
├── _common.py            # CLI 引数共通・WAV/JSON IO・config override
└── _ledger_dummy.py      # ダミー seq_id / ledger 互換ヘルパ
```

### 8.2 共通仕様
- すべて `python -m voice_translator.dev.runner_<layer> [args]` で起動。
- 共通オプション:
  - `--config <path>`: 既存 `config.yaml` をベースに読む(省略時は `DEFAULT_CONFIG`)
  - `--input <path>`: 入力ファイル
  - `--output <path>`: 出力ファイル(省略時は stdout / 自動命名)
  - `--verbose` / `-v`: ログレベルを DEBUG に
- レイヤ固有オプションは ConfigStore の `backends_config.<backend>.<key>` を CLI から上書きする形(例: ASR なら `--model small/medium/large-v3 --device auto --compute-type int8_float16 --beam-size 1`)。
- バックエンド選択も CLI で(例: `--backend faster_whisper`)。MVP では各レイヤ 1 実装なので実質固定だが、将来の差し替えに備えた口を空けておく。

### 8.3 ランナー別仕様(要点のみ)

| ランナー | 入力 | 出力 | 主な CLI オプション |
|---|---|---|---|
| `runner_capture` | (デバイス) | wav | `--device-id`, `--duration`, `--samplerate` |
| `runner_vad` | wav | wav 群 + `index.json` | `--threshold`, `--min-silence-ms`, `--max-speech-sec`, `--speech-pad-ms` |
| `runner_asr` | wav | json | `--model`, `--device`, `--compute-type`, `--beam-size`, `--lang-hint` |
| `runner_translator` | json or `--text` | json | `--device`, `--src-lang`, `--tgt-lang`, `--num-beams`, `--no-repeat-ngram-size`, `--repetition-penalty` |
| `runner_tts` | text or json | wav | `--rate`(SAPI), `--tgt-lang` |
| `runner_output` | wav | (再生) | `--device-id` |

- `runner_translator` の `--num-beams` 等は pendList の「翻訳バックエンドの生成パラメータ」と直結する(=このランナーが届いた時点で degenerate 再現テストが楽になる)。

### 8.4 ペイロード付随情報(seq_id 等)の扱い
- ランナーは ledger を持たない。`seq_id` は CLI で指定 or 自動採番(既定 1)。
- 出力 JSON にはダミー値で OK(ユーザ依頼通り)。読む側のランナーも seq_id は素通しでよい。

## 9. 変更3: 部分パイプラインランナー

`runner_pipeline.py` は、任意の連続レイヤを **メモリ内で繋いで** 一度に流す。

```
python -m voice_translator.dev.runner_pipeline \
    --from vad --to translate \
    --input sample.wav \
    --output ./out/ \
    --config ./config.yaml
```

- `--from` / `--to` ∈ { `vad`, `asr`, `translate`, `tts` } のサブセット(`capture` / `output` はデバイス依存なのでこのランナーでは対象外)。
- 出力は **各ステージのダンプと同じ形式** で `--output` 配下に保存。これにより `runner_pipeline` の出力をさらに別の単体ランナーで再加工できる。
- 内部実装は、本体パイプラインの `_*_loop` を **スレッドなし・キューなしの順次呼び出し** に削ぎ落とした形。Coordinator は使わない(=GUI / 5 スレッドのテストとは別系統で軽くする)。

## 10. ConfigStore の追加キー

`DEFAULT_CONFIG` に下記を追加:

```python
"pipeline": {
    ...既存...,
    "dump": {
        "enabled": False,                   # ステージ間ダンプの ON/OFF
        "directory": "./logs/dumps",        # 出力先。run_id 配下に書く
        "stages": ["vad", "asr", "translate", "tts"],  # 書き出すステージの選択
        "max_runs": 20,                     # 古い run_id ディレクトリの自動掃除上限(0 で無効)
    },
},
```

- `stages` を絞れるのは、PCM 系(`vad`/`tts`)はサイズが大きいため、テキスト系(`asr`/`translate`)だけ取りたいケースに対応するため。
- `max_runs` は連続走行で dump ディレクトリが肥大化するのを防ぐリングバッファ。

## 11. 実装フェーズ

各 Phase 完了時に小さくコミット(CLAUDE.md「コミット・ブランチ」)。`--no-ff` マージはユーザ依頼があった時点で。

| Phase | 内容 | 完了条件 |
|---|---|---|
| **A. データ規約 + ダンプ機能 ✅完了 2026-05-28** | `StageDumpWriter` / `NullStageDumpWriter` の追加(`common/stage_dump.py`)、PipelineCoordinator への結線(各ループに 1 行ずつ on_* 呼び出し)、ConfigStore キー追加(`pipeline.dump.*`)、AppController での生成・ライフサイクル管理。small テスト 19 件追加で計 363 件パス。`config.yaml` で `pipeline.dump.enabled: true` にすると `./logs/dumps/<run_id>/seq_NNNN_*.wav,json` が書かれる。 | (達成済)Coordinator が dump フックを vad→asr→translate→tts の順で同一 seq_id で呼ぶことを `test_pipeline_dump.py` で確認。`test_stage_dump.py` で WAV/JSON 規約・古い run の自動削除・stages フィルタを確認。 |
| **B. 共通基盤 + ASR/Translator ランナー ✅完了 2026-05-28** | `dev/_common.py`(WAV/JSON IO、テキスト入力解決、共通 argparse)、`dev/runner_asr.py`、`dev/runner_translator.py`。tests/test_dev_common.py + test_runner_asr.py + test_runner_translator.py(計 20 件)。`runner_asr --model medium --device cuda --compute-type int8_float16` 等が動く。`runner_translator --num-beams 4 --no-repeat-ngram-size 3 --repetition-penalty 1.1` で degenerate 回避設定の効果検証が可能(pendList 直結)。 | (達成済)CLI 引数 → backend ctor の伝播、出力 JSON のスキーマ一致、stdout 出力、stdin 入力、JSON/.txt 入力のフォールバックを確認。 |
| **C. VAD/TTS ランナー ✅完了 2026-05-28** | `dev/runner_vad.py`(出力は切り出し WAV 群 + index.json)、`dev/runner_tts.py`。tests/test_runner_vad.py + test_runner_tts.py(計 6 件)。 | (達成済)VAD パラメータ(threshold / min_silence_ms / max_speech_sec)が backend に届くこと、index.json と分割 WAV が 1:1 対応すること、TTS が JSON 入力から tgt_lang を継承することを確認。 |
| **D. Capture/Output ランナー** | `runner_capture.py`、`runner_output.py`(デバイス依存)。**本ブランチでは保留** — 実機マイク/スピーカが必須で middle テストでも検証が困難。Phase A〜C/E で得たダンプデータと runner_pipeline で「実機マイク経由」の代替は既にできているため、優先度を下げる。要望時に別ブランチで対応。 | (TBD) |
| **E. 部分パイプラインランナー ✅完了 2026-05-28** | `dev/runner_pipeline.py`(`--from`/`--to`)。tests/test_runner_pipeline.py(4 件)。バックエンドは一度だけロードして複数発話をバッチ処理するので、ランナーを個別に呼ぶより速い。 | (達成済)`vad→translate`、`asr→translate`(VAD スキップ)、`translate→tts`(.json 入力)が動く。`--from > --to` を弾く。 |

A は他の前提なので最初。B はチューニング価値が最も高く優先。C/D/E は順不同で詰める。

## 12. テスト方針

[Class.md](../Class.md) のレイヤ責務に従い、各ランナーを **既存バックエンドのモックで** テストすればよい(small で完結)。

- **small**(モック):
  - CLI 引数のパースと `ConfigStore` への反映(`runner_<layer> --device cuda --beam-size 5` で `backends_config.faster_whisper.*` が上書きされる)
  - WAV/JSON IO の往復(書いて読んで等価)
  - `StageDumpWriter` がフック呼出順に従ってファイルを生成すること
  - `NullStageDumpWriter` がオーバーヘッドゼロ(関数呼び出しのみ)
- **middle**(`@pytest.mark.middle`):
  - 実 `SileroVadBackend` に 8 秒の録音 WAV を食わせる
  - 実 `FasterWhisperAsrBackend("small", device="cpu")` に短い英語 WAV を流して text が返る
  - `runner_pipeline --from vad --to asr` の縦通し
- **large**(`@pytest.mark.large`、手動のみ):
  - 実 GPU(あれば)で `runner_asr --model medium` まで通す
  - `runner_translator` で過去に degenerate を起こした入力 → 既定 vs `--num-beams 4` の比較

詳細項目は `testPlan.md` に別建てで作成(本ブランチ Phase A の終わりに着手)。

## 13. 既存ドキュメントの更新

- **[Class.md](../Class.md)**:
  - 「§4 横断機能(共通)」に `StageDumpWriter` / `NullStageDumpWriter` を追記。
  - 「§6 拡張時の追加例」に「ダンプを使った検証ループ」の項を追加(任意)。
- **[Architecture.html](../Architecture.html)**:
  - レイヤ図に「Dump Hook」をオプション要素として併記(必要なら点線で)。
  - dev ランナー群を**本体パイプラインとは別レーン**として描く。
- **[pendList.md](../pendList.md)**:
  - 「Whisper model_size 引き上げ検討」「翻訳バックエンドの生成パラメータ」の各エントリに、**本ブランチ完了後を再検討トリガに追記**(=このランナーが揃った時点で再着手)。
- **[TaskList.md](../TaskList.md)**:
  - Phase A〜E をタスクとして反映。

## 14. 配布方針との整合

- ランナー群は **追加バイナリ無し / 既存依存のみ** で動く(`argparse` は stdlib、WAV IO は `numpy` + `soundfile` か stdlib `wave`)。
- 既存配布物に乗せても起動時間は変わらない(`dev.*` を import するのは CLI 実行時のみ)。
- `pipeline.dump.enabled` の既定は **False**(配布デフォルトでは無効、開発者が yaml で ON にする想定)。

## 15. 関連 pendList

このプランは以下のエントリを **直接アンブロック** する:
- [Whisper モデルサイズ(model_size)の引き上げ検討](../pendList.md) — `runner_asr` で実測がしやすくなる
- [翻訳/LLM バックエンドの生成パラメータを設定可能にする](../pendList.md) — `runner_translator` の `--num-beams` 等で degenerate 再現が容易
- [パイプラインステージのパラメータ GUI 化](../pendList.md) — 単体ランナーでの実験結果を踏まえてプリセット設計に進める
