# 使い方 (runner manual) — 各レイヤの単体 CLI ランナー

本体アプリ(GUI)とは別に、**各レイヤを CUI から単体で動かして検証する**ためのランナー群の使い方。
パラメータ調整・モデル比較・問題発話の再現に使う。本体パイプライン(`PipelineCoordinator`)
は経由せず、各バックエンドを直接呼び出すだけのシンプルな構成。

詳細設計は [docs/design/feature-dev-runners-and-dump/Plan.md](design/feature-dev-runners-and-dump/Plan.md) を参照。

---

## 1. ランナー一覧

| モジュール | 役割 | 入力 | 出力 |
|---|---|---|---|
| `voice_translator.dev.runner_vad` | 発話区切り検出(silero-vad) | WAV | 分割 WAV 群 + `index.json` |
| `voice_translator.dev.runner_asr` | 書き起こし(faster-whisper) | WAV | JSON `{seq_id, stage, src_lang, text, runner}` |
| `voice_translator.dev.runner_translator` | 翻訳(NLLB-200) | text / .json / .txt / stdin | JSON `{..., src_text, tgt_text, runner}` |
| `voice_translator.dev.runner_tts` | 音声合成(SAPI) | text / .json / .txt / stdin | WAV(mono int16) |
| `voice_translator.dev.runner_pipeline` | 任意の連続レイヤを連結 | レイヤに応じて WAV or text | レイヤごとに WAV/JSON + `index.json` |
| `voice_translator.dev.runner_output` | 音声再生(soundcard) | tone / WAV / text(TTS 経由) | 指定デバイスへの再生(ファイル出力なし) |

実機マイク必須の Capture ランナーは未提供(別ブランチ)。Output は実機スピーカへ
実際に音を出すため、本体アプリで「翻訳まで出ているのに音が鳴らない」ような切り分けに使う。

---

## 2. 全ランナー共通の規約

### 2.1 起動方法
```powershell
py -m voice_translator.dev.runner_<layer> [args]
```

### 2.2 共通オプション
| オプション | 説明 |
|---|---|
| `-v` / `--verbose` | DEBUG ログを stderr に出す(stdout は結果出力で占有) |
| `--help` | 引数一覧を出す。最初に必ず確認推奨 |

### 2.3 データ形式
- **WAV**: 16kHz / mono / int16(プロジェクト内部は float32 だが WAV は int16 で保存)
- **JSON**: UTF-8、`indent=2`、非 ASCII はそのまま(`ensure_ascii=False`)
- ファイル名規約: `seq_<4桁>_<stage>.{wav,json}`(例: `seq_0042_vad.wav`)

### 2.4 ペイロード付随情報(`seq_id` 等)
ランナーは Ledger を持たないので `seq_id` は CLI 引数(`--seq-id`)か自動採番(既定 1)。
**ダミー値で OK**。後段ランナーへの繋ぎ込みでは値は素通しされる。

### 2.5 GPU / CPU の切り替え(`--extra` と `--device` は別レイヤ)

CUDA を使うかどうかは **2 段階** に分かれている:

| 層 | フラグ | 役割 |
|---|---|---|
| env(uv 層) | `--extra cuda` / `--extra cpu` | CUDA 版 torch / ctranslate2 バイナリを venv に入れる(GPU を使う**前提条件**) |
| app(ランナー層) | `--device` | この実行で実際に何を使うか(既定 `auto`)。`auto` は `torch.cuda.is_available()` を見て自動選択 |

実用パターンは下記の 3 つだけ:

```powershell
# (A) GPU 環境(NVIDIA + CUDA 12 ドライバ)で動かす — 普段はこれ
py -m uv run --extra cuda python -m voice_translator.dev.runner_asr `
    --input <wav> -o out.json
# → 内部で auto → cuda + int8_float16 に解決される(明示不要)

# (B) CPU 環境(GPU 非搭載 / オフィス PC 等)で動かす
py -m uv run --extra cpu python -m voice_translator.dev.runner_asr `
    --input <wav> -o out.json
# → 内部で auto → cpu + int8 に解決される

# (C) GPU 環境で意図的に CPU 走行させて比較計測したい
py -m uv run --extra cuda python -m voice_translator.dev.runner_asr `
    --input <wav> -o out.json --device cpu --compute-type int8
```

注意:
- `uv run` は `--extra` を指定しないと既定で CPU 版に倒れる(uv の conflicts 解決仕様)。**起動コマンドにも sync と同じ `--extra` を必ず付ける**(`README.md` 参照)。
- `--extra cpu` の env で `--device cuda` を指定すると CUDA DLL(`cublas64_12.dll`)が見つからず `FatalError` で落ちる。これは想定通りの失敗で、`--extra cuda` への切替が必要。
- 以降の例では原則 `--extra cuda` を付ける(GPU 想定)。CPU 環境のユーザは `--extra cpu` に読み替え。

---

## 3. 本体アプリでサンプルダンプを生成する

ランナー検証の入力データは本体アプリの **ステージ間ダンプ機能** で取得できる。

`config.yaml`(GUI の保存先、未編集なら `./config.yaml`)で以下を有効化:

```yaml
pipeline:
  dump:
    enabled: true                      # ダンプ ON
    directory: ./logs/dumps            # 出力ルート
    stages: [vad, asr, translate, tts] # 書き出す対象(絞る場合 [asr, translate] 等)
    max_runs: 20                       # 古い run の自動掃除上限(0 で無効)
```

GUI を起動 → 開始 → 適当に発話 → 停止。
`./logs/dumps/<run_id>/`(run_id は `YYYYMMDD-HHMMSS-NNNN`)に以下が並ぶ:

```
run.json                       … 使用バックエンド/device/言語のスナップショット
seq_0001_vad.wav               … VAD で切り出した発話 PCM
seq_0001_asr.json              … {seq_id, stage, src_lang, text}
seq_0001_translate.json        … {seq_id, stage, src_lang, tgt_lang, src_text, tgt_text}
seq_0001_tts.wav               … 合成音声(SAPI のレート、通常 22050Hz)
seq_0002_*.{wav,json}          … 発話 2 件目以降
...
```

各ランナーはこのファイル群を**そのまま入力にできる**(下記参照)。

---

## 4. ランナー別: 詳細

### 4.1 `runner_asr` — 書き起こし

```powershell
# 最小: 既定モデル(small)で書き起こし → stdout に JSON
# (GPU env なら自動で cuda、CPU env なら自動で cpu を選択)
py -m uv run --extra cuda python -m voice_translator.dev.runner_asr `
    --input sample.wav

# medium モデルで書き起こし、ファイルへ
py -m uv run --extra cuda python -m voice_translator.dev.runner_asr `
    --input sample.wav --model medium --output result.json

# ダンプの VAD セグメントを別モデルで再書き起こし(検証ループの典型)
py -m uv run --extra cuda python -m voice_translator.dev.runner_asr `
    --input logs/dumps/20260528-160000-0001/seq_0042_vad.wav `
    --model medium --beam-size 5 --lang-hint en
```

| オプション | 既定 | 説明 |
|---|---|---|
| `--input` / `-i` | (必須) | 入力 WAV |
| `--output` / `-o` | stdout | 出力 JSON パス |
| `--model` / `-m` | `small` | Whisper モデル(`tiny`/`base`/`small`/`medium`/`large-v2`/`large-v3` 等) |
| `--device` / `-d` | `auto` | `auto` / `cuda` / `cpu` |
| `--compute-type` / `-c` | `auto` | `auto` / `int8` / `float16` / `int8_float16` 等 |
| `--beam-size` / `-b` | `1` | ビーム幅(>1 で精度↑/遅延↑) |
| `--lang-hint` / `-l` | `auto` | 言語ヒント(ISO 639-1)。`auto` で自動検出 |
| `--seq-id` | `1` | 出力 JSON に載せる seq_id(ダミー値で OK) |

出力 JSON は `seq_NNNN_asr.json` と同スキーマ + `runner` 配下に実行メタ(モデル名・解決後 device・所要時間 等)。

---

### 4.2 `runner_translator` — 翻訳

```powershell
# 最小: 直接テキスト指定
py -m uv run --extra cuda python -m voice_translator.dev.runner_translator --text "Hello, world."

# ダンプの ASR 結果を翻訳(src_lang は JSON の値を継承)
py -m uv run --extra cuda python -m voice_translator.dev.runner_translator `
    --input logs/dumps/20260528-160000-0001/seq_0042_asr.json --tgt-lang ja

# degenerate(同じ n-gram 反復)再現:既定 vs 抑止パラメータの比較
py -m uv run --extra cuda python -m voice_translator.dev.runner_translator `
    --input long.txt `
    --num-beams 1 --no-repeat-ngram-size 0 --repetition-penalty 1.0 `
    --output before.json
py -m uv run --extra cuda python -m voice_translator.dev.runner_translator `
    --input long.txt `
    --num-beams 4 --no-repeat-ngram-size 3 --repetition-penalty 1.1 `
    --output after.json

# stdin から
"good morning" | py -m uv run --extra cuda python -m voice_translator.dev.runner_translator --src-lang en --tgt-lang ja
```

| オプション | 既定 | 説明 |
|---|---|---|
| `--text` / `-t` | — | 翻訳対象テキストを直接指定(`--input` と排他) |
| `--input` / `-i` | — | `.json`(`text`/`src_text`/`tgt_text` を採用)または素テキスト |
| `--output` / `-o` | stdout | 出力 JSON パス |
| `--src-lang` | `en` | 翻訳元(`.json` 入力に `src_lang` があればそちらを優先) |
| `--tgt-lang` | `ja` | 翻訳先 |
| `--device` / `-d` | `auto` | `auto` / `cuda` / `mps` / `cpu` |
| `--model-name` | `facebook/nllb-200-distilled-600M` | HF モデル名 |
| `--num-beams` | `4` | ビームサーチ幅 |
| `--no-repeat-ngram-size` | `3` | この長さの n-gram を出力中で繰り返さない |
| `--repetition-penalty` | `1.1` | 同じトークンへのペナルティ(>1 で抑制) |
| `--max-length` | `512` | 生成長の上限 |
| `--no-early-stopping` | OFF | 早期停止を無効化 |
| `--seq-id` | `1` | 出力 JSON に載せる seq_id |

入力が `.json` でなおかつ stdin/`.txt` でないときは `src_lang` を JSON から継承する。CLI の `--src-lang` は **使われない**(ダンプ再生で間違って渡しても安全)。

---

### 4.3 `runner_vad` — 発話区切り検出

```powershell
# 既定パラメータで long.wav を切り出し
py -m uv run --extra cuda python -m voice_translator.dev.runner_vad `
    --input long.wav --out-dir vad_out/

# 短めの無音で区切る + 1発話最大 5 秒
py -m uv run --extra cuda python -m voice_translator.dev.runner_vad `
    --input long.wav --out-dir vad_out/ `
    --threshold 0.3 --min-silence-ms 300 --max-speech-sec 5.0
```

| オプション | 既定 | 説明 |
|---|---|---|
| `--input` / `-i` | (必須) | 入力 WAV |
| `--out-dir` / `-O` | (必須) | 出力ディレクトリ |
| `--threshold` | `0.5` | speech probability の判定しきい値 |
| `--min-silence-ms` | `500` | 発話終了とみなす無音期間 |
| `--speech-pad-ms` | `100` | 発話前後の余白 |
| `--max-speech-sec` | `8.0` | 1 発話の最大長(0 で無効化) |
| `--chunk-samples` | `2048` | 入力を分割して投入するサイズ(VAD 内部で 512 単位に再分割) |

`out_dir/seq_NNNN_vad.wav`(切り出された発話)と `out_dir/index.json`(各セグメントの長さ・タイムスタンプ・使用パラメータ)が生成される。

---

### 4.4 `runner_tts` — 音声合成

```powershell
# 直接テキストを合成
py -m uv run --extra cuda python -m voice_translator.dev.runner_tts `
    --text "こんにちは" --output hello.wav

# ダンプの翻訳結果から(tgt_lang は JSON から継承)
py -m uv run --extra cuda python -m voice_translator.dev.runner_tts `
    --input logs/dumps/20260528-160000-0001/seq_0042_translate.json `
    --output out.wav

# 早口 + flush 待機を短く
py -m uv run --extra cuda python -m voice_translator.dev.runner_tts `
    --text "テスト" -o t.wav --rate 240 --flush-delay-sec 0.05
```

| オプション | 既定 | 説明 |
|---|---|---|
| `--text` / `-t` | — | 合成対象テキスト(`--input` と排他) |
| `--input` / `-i` | — | `.json`(`tgt_text`/`text` を採用)または素テキスト |
| `--output` / `-o` | (必須) | 出力 WAV パス |
| `--tgt-lang` | `ja` | SAPI ボイス選択ヒント(`.json` 入力に `tgt_lang` があればそちらを優先) |
| `--rate` | `180` | 読み上げ速度(WPM 相当) |
| `--flush-delay-sec` | `0.1` | runAndWait 後の待機(SAPI flush 不整合の暫定対処) |

再生はしない(出力 WAV を別途プレイヤーで再生 / `runner_output --wav <path>` で再生可)。

---

### 4.5 `runner_pipeline` — 部分パイプライン

任意の連続ステージをメモリ内で連結して一気に流す。バックエンドは 1 度だけロードされるので、複数発話を含む長尺 WAV の **バッチ処理が速い**。

```powershell
# 長尺 WAV を VAD で区切って ASR まで一気に
py -m uv run --extra cuda python -m voice_translator.dev.runner_pipeline `
    --from vad --to asr `
    --input long.wav --out-dir out/ --model small

# ダンプ済み発話を ASR から TTS まで(VAD はスキップ)
py -m uv run --extra cuda python -m voice_translator.dev.runner_pipeline `
    --from asr --to tts `
    --input logs/dumps/20260528-160000-0001/seq_0042_vad.wav `
    --out-dir out/ --model medium --rate 200

# ASR ダンプ JSON を翻訳→合成(degenerate 再現)
py -m uv run --extra cuda python -m voice_translator.dev.runner_pipeline `
    --from translate --to tts `
    --input logs/dumps/20260528-160000-0001/seq_0042_asr.json `
    --out-dir out/ `
    --num-beams 4 --no-repeat-ngram-size 3
```

| オプション | 既定 | 説明 |
|---|---|---|
| `--from` | (必須) | 開始ステージ(`vad`/`asr`/`translate`/`tts`) |
| `--to` | (必須) | 終了ステージ(`--from` 以後) |
| `--input` / `-i` | (必須) | `vad`/`asr` 開始は WAV、`translate`/`tts` 開始は `.txt` または `.json` |
| `--out-dir` / `-O` | (必須) | 出力ディレクトリ |
| `--src-lang` / `--tgt-lang` | `en` / `ja` | 翻訳の言語ペア(JSON 入力時は JSON 値を優先) |
| VAD 系 | `--vad-threshold`, `--vad-min-silence-ms`, `--vad-speech-pad-ms`, `--vad-max-speech-sec`, `--vad-chunk-samples` |
| ASR 系 | `--model`, `--device`, `--compute-type`, `--beam-size` |
| Translator 系 | `--model-name`, `--num-beams`, `--no-repeat-ngram-size`, `--repetition-penalty`, `--max-length`, `--no-early-stopping`(device は ASR と共通) |
| TTS 系 | `--rate`, `--flush-delay-sec` |

`out_dir/seq_NNNN_<stage>.{wav,json}` と `out_dir/index.json`(処理時刻・パラメータ・全 unit のサマリ)を生成。

---

### 4.6 `runner_output` — 音声再生(切り分け用)

本体で「翻訳までは出ているのに音が鳴らない」ような症状が出たとき、Output レイヤ
単体が動くかを確認するためのランナー。`AppController` / `PipelineCoordinator` を
介さず `AudioOutputBackend` を直接呼ぶので、原因を Output / デバイス / soundcard
側に絞れる。

```powershell
# 1) 現在の環境で見える出力デバイス一覧(* がデフォルト想定)
py -m uv run --extra cpu python -m voice_translator.dev.runner_output --list-devices

# 2) デフォルト出力デバイスに 440Hz サイン波 1 秒(最も単純な疎通)
py -m uv run --extra cpu python -m voice_translator.dev.runner_output --tone

# 3) 任意デバイスに WAV を再生(device-id は手順 1 の左カラムから)
py -m uv run --extra cpu python -m voice_translator.dev.runner_output `
    --device-id "{0.0.0.00000000}.{...}" --wav some.wav

# 4) TTS backend で合成 → 再生(SAPI→soundcard の本番経路に近い確認)
py -m uv run --extra cpu python -m voice_translator.dev.runner_output --text "テスト音声です"
```

| オプション | 既定 | 説明 |
|---|---|---|
| `--backend` | `soundcard` | 使用する Output backend 名(BackendRegistry に登録された名前) |
| `--list-devices` | OFF | デバイス一覧を表示して終了(他オプションは無視) |
| `--device-id` | (先頭) | 再生先デバイス。省略時は backend が返す先頭(soundcard ならデフォルトスピーカ) |
| `--tone` | (既定) | サイン波を再生(`--wav` / `--text` のいずれも未指定なら自動でこれ) |
| `--tone-hz` | `440.0` | サイン波の周波数 [Hz] |
| `--tone-sec` | `1.0` | サイン波の長さ [秒] |
| `--tone-sr` | `44100` | サイン波のサンプルレート [Hz] |
| `--wav` | — | WAV を再生(サンプルレートは WAV のものを使う) |
| `--text` | — | TTS backend で合成して再生(`--tts` / `--tgt-lang` と併用) |
| `--tts` | `sapi` | `--text` 指定時に使う TTS backend 名 |
| `--tgt-lang` | `ja` | `--text` 指定時に TTS に渡す言語ヒント |

切り分け手順の例:

1. `--list-devices` で本体 GUI の SettingsPanel に出ているデバイスと一致するか確認。
   一致しなければ ConfigStore の `devices.output` が古い ID を指している可能性。
2. `--tone` で何も音が鳴らなければ、Output backend / 選んだデバイス / soundcard 側で
   詰まっている。本体だけの問題ではない。
3. `--tone` は鳴るが `--text` で鳴らなければ、TTS 経路(SAPI 合成 → PCM)に問題が
   あるか、TTS が空 PCM を返している(本体側では `SkipError` で個別発話だけ捨てている
   可能性)。
4. `--wav` で本体ダンプの `seq_NNNN_tts.wav` を再生して鳴れば、TTS 合成自体は
   問題なく、最後の再生 hand-off だけが詰まっている。

ファイル出力はしない(再生のみ)。`backend.start` / `backend.play` / `backend.stop`
の順に呼ばれ、`play` が同期再生なのでコマンドが返ってきたら再生終了。

---

## 5. よく使う検証ワークフロー

### 5.1 「ASR モデルを上げると精度がどう変わるか」
```powershell
# 本体で sample を取って (config.yaml の pipeline.dump.enabled: true)
# small と medium を同じ WAV で比較
py -m uv run --extra cuda python -m voice_translator.dev.runner_asr `
    -i logs/dumps/<run>/seq_0001_vad.wav -m small  -o small.json
py -m uv run --extra cuda python -m voice_translator.dev.runner_asr `
    -i logs/dumps/<run>/seq_0001_vad.wav -m medium -o medium.json
# 差分は jq 等で確認
```

### 5.2 「翻訳の degenerate を抑える設定の効果を測る」
```powershell
# 問題が出た発話の ASR 結果(.json)を流して、現行設定 vs より強い抑止
py -m uv run --extra cuda python -m voice_translator.dev.runner_translator `
    -i logs/dumps/<run>/seq_0184_asr.json `
    --num-beams 1 --no-repeat-ngram-size 0 -o before.json
py -m uv run --extra cuda python -m voice_translator.dev.runner_translator `
    -i logs/dumps/<run>/seq_0184_asr.json `
    --num-beams 8 --no-repeat-ngram-size 4 --repetition-penalty 1.3 -o after.json
```

### 5.3 「長尺 WAV を VAD パラメータ別に切ってまとめて ASR」
```powershell
py -m uv run --extra cuda python -m voice_translator.dev.runner_pipeline `
    --from vad --to asr -i lecture.wav -O out_default/

py -m uv run --extra cuda python -m voice_translator.dev.runner_pipeline `
    --from vad --to asr -i lecture.wav -O out_short/ --vad-min-silence-ms 200
```

### 5.4 「翻訳テキストの TTS 出力をいくつかの rate で聴き比べ」
```powershell
$text = (Get-Content logs/dumps/<run>/seq_0042_translate.json | ConvertFrom-Json).tgt_text
py -m uv run --extra cuda python -m voice_translator.dev.runner_tts -t $text -o slow.wav --rate 140
py -m uv run --extra cuda python -m voice_translator.dev.runner_tts -t $text -o fast.wav --rate 240
```

### 5.5 「同じ env のままで CPU vs GPU の速度差を測る」
```powershell
# GPU(既定。明示不要)
py -m uv run --extra cuda python -m voice_translator.dev.runner_asr `
    -i logs/dumps/<run>/seq_0001_vad.wav -o asr_gpu.json
# 同 env で CPU 走行を強制(--device cpu + --compute-type int8 を両方明示)
py -m uv run --extra cuda python -m voice_translator.dev.runner_asr `
    -i logs/dumps/<run>/seq_0001_vad.wav -o asr_cpu.json `
    --device cpu --compute-type int8
# elapsed_ms フィールドを比較
```

---

## 6. 出力 JSON スキーマ(リファレンス)

すべて `StageDumpWriter` の規約と一致。後段ランナーが入力としてそのまま読める。

### 6.1 `seq_NNNN_asr.json`
```jsonc
{
  "seq_id": 42,
  "stage": "asr",
  "src_lang": "en",
  "text": "...",
  // 単体ランナー出力時のみ
  "runner": {
    "name": "runner_asr",
    "model": "small",
    "device_requested": "auto", "device_resolved": "cuda",
    "compute_type_requested": "auto", "compute_type_resolved": "int8_float16",
    "beam_size": 1,
    "input": "...", "input_samplerate": 16000, "input_samples": 128000,
    "elapsed_ms": 754.0
  }
}
```

### 6.2 `seq_NNNN_translate.json`
```jsonc
{
  "seq_id": 42,
  "stage": "translate",
  "src_lang": "en", "tgt_lang": "ja",
  "src_text": "...", "tgt_text": "...",
  "runner": { /* runner_translator 実行時のみ */ }
}
```

### 6.3 `seq_NNNN_vad.wav` / `seq_NNNN_tts.wav`
- mono / int16 PCM
- samplerate は WAV ヘッダから読む(VAD 出力は 16kHz、TTS は SAPI 依存で通常 22050Hz)

### 6.4 `run.json`(本体パイプラインダンプのみ)
本体実行時のスナップショット。ランナー側ではこのファイルは生成しない(代わりに各出力 JSON の `runner` フィールドに実行コンテキストを残す)。

---

## 7. トラブルシュート

| 症状 | 対処 |
|---|---|
| `faster-whisper のロードに失敗` / `transformers のロードに失敗` | env の torch が入っていない。`py -m uv sync --extra cpu`(または `--extra cuda`)を実行 |
| `Library cublas64_12.dll is not found or cannot be loaded` | CPU env(`--extra cpu`)で `--device cuda` を指定した場合に出る。`py -m uv run --extra cuda ...` で起動するか、`--device` を既定(`auto`)に戻す |
| GPU 環境のはずなのに `device_resolved: cpu` になる | `py -m uv run` を **`--extra cuda` 無し**で起動した可能性。uv の conflicts 解決が CPU 側に倒れて torch が CPU 版に差し替わる(`uv run` 既知挙動。README.md 参照) |
| `--input` 指定したのに `入力が空です` | `.json` のスキーマが `text`/`tgt_text`/`src_text` のいずれも持っていない可能性 |
| ASR が CPU(int8)より GPU(float16)で遅い | `--compute-type int8_float16`(`auto` 既定で自動選択される)。`float16` 単独より small モデル+短入力で安定して速い |
| `runner_vad` のセグメント数が 0 | 入力 WAV が無音 / `--threshold` が高すぎ / サンプルレートが 16kHz でない(警告が出る) |
| `runner_pipeline` で `--from > --to` エラー | ステージ順は `vad < asr < translate < tts` で固定 |
| `runner_output` で `指定 device_id が見つかりません` | `--list-devices` で出る ID と完全一致が必要(soundcard は中括弧つき GUID)。コピペで貼り付け |
| `runner_output --tone` で音が出ない | デフォルトデバイスが期待と違う可能性。`--list-devices` で 1 行目を確認、別デバイスを `--device-id` で明示 |

---

## 関連ドキュメント
- [docs/manual.md](manual.md) — 本体アプリ(GUI)の使い方
- [docs/design/Architecture.html](design/Architecture.html) — アーキテクチャ(レイヤ図・I/F)
- [docs/design/Class.md](design/Class.md) — クラス/モジュール詳細
- [docs/design/feature-dev-runners-and-dump/Plan.md](design/feature-dev-runners-and-dump/Plan.md) — 本機能の設計
- [docs/design/pendList.md](design/pendList.md) — パラメータ調整関連の未対応項目
