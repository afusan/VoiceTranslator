# 使い方 (manual)

ローカルで動作する音声翻訳アプリの使い方。

---

## 1. このアプリでできること
- PC内で再生されている音声(YouTube/Twitch/通話など)を取り込んで、
  別言語に翻訳した音声を**別の出力デバイス**から再生する。
- 自分のマイクから話した内容を翻訳して、スピーカ/仮想マイクから再生する。
- **テキスト字幕モード**(TTS=(なし)): 翻訳結果を画面表示のみで完了し、TTS / 出力デバイスは
  使わない構成も選べる。音を出したくないシーンや、TTS の準備が要らないときに有効。

主な処理の流れ(各段は独立したスレッドで動作):
```
[入力デバイス] → VAD → ASR → 翻訳 → TTS → [出力デバイス]
                 Input    ASR  Translator TTS   Output
                       (5スレッド・4キュー構成)

# TTS=(なし) のとき(text_only モード)
[入力デバイス] → VAD → ASR → 翻訳 → (履歴表示で完了)
                 Input    ASR  Translator
                  ※ TTS / Output スレッドは起動しない
```

---

## 2. 動作環境

- **OS**: Windows 11(MVP の動作確認対象。Mac/Linuxは将来対応)
- **Python**: 3.12 以上(`uv` が自動取得します)
- **ディスク**: 5〜6GB 程度(faster-whisper モデル + NLLB-200 + PyTorch等)
- **メモリ**: 8GB 以上推奨(NLLB-200 600M を CPU で動かすため)
- **ネット**: 初回起動時のみ、モデルDLに必要

---

## 3. インストール

`uv`(Python のモダンな環境管理ツール)を使います。

```bash
# uv を入れる(いずれか1つ)
py -m pip install --user uv                       # pip経由
# or: powershell -c "irm https://astral.sh/uv/install.ps1 | iex"  # 公式

# プロジェクトを取得後、依存と Python 3.11 を自動セットアップ
# CPU 専用環境(誰でも動く、推奨初手):
py -m uv sync --extra cpu

# NVIDIA GPU を持っているなら CUDA 版にする(自動で GPU 使用、+3GB):
# py -m uv sync --extra cuda
```

`uv sync` で `.venv/` フォルダに仮想環境が作られ、Python 3.11 と必要なライブラリが入ります。
初回は数百MB〜数GBのダウンロードが入るので、ネット環境に注意してください。

### `--extra cpu` と `--extra cuda` の選び方
| 環境 | 推奨 | 備考 |
|---|---|---|
| Windows / Linux (NVIDIA GPU 無し) | `--extra cpu` | CPU でしか動かない |
| Windows / Linux + NVIDIA GPU(RTX 等) | `--extra cuda` | 翻訳/ASR が 5〜15 倍速 |
| macOS (Apple Silicon: M1/M2/M3) | `--extra cpu` | MPS が自動で使われる |
| macOS (Intel) | `--extra cpu` | CPU のみ |
| AMD GPU | `--extra cpu` | ROCm は本リリース非対応 |

- **CUDA Toolkit のインストールは不要**(`--extra cuda` で取得する wheel に CUDA ランタイムが同梱されています)。NVIDIA ドライバが入っていれば動きます(`nvidia-smi` で確認)。
- 2 つの extras は **排他**(同時に指定しないこと)。
- 後から切り替えたい場合は再度 `uv sync --extra <別の方>` で OK(差分だけインストールされます)。

### **重要**: アプリ起動時にも `--extra` を付ける

`uv run` はデフォルトで「extras 無し」で内部 sync を再実行するため、せっかく `--extra cuda`
で入れた CUDA 版 torch が **CPU 版に上書きされて戻ってしまう** という挙動があります。
これを避けるには、起動コマンドにも同じ `--extra` を付けてください:

```bash
# GPU 版で動かす(毎回 --extra cuda を付ける)
py -m uv run --extra cuda python -m voice_translator

# CPU 版で動かす
py -m uv run --extra cpu python -m voice_translator
```

「sync ではなく今の venv を維持して実行したい」場合は `--no-sync` を付けます:
```bash
py -m uv run --extra cuda --no-sync python -m voice_translator
```

---

## 4. 起動方法

```bash
py -m uv run python -m voice_translator
```

→ GUI ウィンドウが立ち上がります。

> **モデルロードのタイミング**: 各モデル(ASR / 翻訳 / VAD など)はアプリ起動時に1回だけ
> バックグラウンドでロードします(設定 UI の各レイヤ右側のステータスラベルで進捗を確認できる)。
> ロードが終わるまで「▶ 開始」ボタンは「モデル準備中…」として無効化されます。
> 全レイヤが `Loaded` になると有効化され、押すと即座に処理が始まります。
> 一度ロードしたモデルは停止後も常駐するため、**Stop → Start を繰り返してもロード待ちは発生しません**
> (設定でバックエンドを切り替えたときだけ、そのレイヤだけ再ロードが走る)。
>
> **初回起動の注意**: 初回ロード時には **ASRモデル(150MB)** と **翻訳モデル(NLLB-200, 約2.5GB)**
> のダウンロードが走ります。数分〜十数分かかります。気長にお待ちください。2回目以降はキャッシュから読み込まれます。

---

## 5. 初期設定

設定パネルは **3 つのセクション**(「バックエンド」「デバイス」「翻訳」)に分かれており、
それぞれの見出し(▼ / ▶)をクリックして個別に折り畳むことができる。開閉状態は次回起動時にも
保持される。下部の「ログ出力先」「設定を保存 / 再読込 / デバイス再列挙」ボタンは常時表示。

### 5-1. 入力デバイスの選び方
- PC音声を翻訳したい → **「[LB] スピーカ名」(ループバック)** を選ぶ
- 自分の声を翻訳したい → 通常のマイクを選ぶ

「入力デバイス」プルダウンの内容は **「音声取得」プルダウンで選んだ backend** に連動する:
- 既定の `soundcard` backend では、通常マイクとスピーカループバック(`[LB] ...`)が並ぶ。
- 将来 `proctap`(per-process キャプチャ)等が追加されたら、上段で backend を切り替えると
  下段のソース一覧が自動的に更新される(プロセス一覧など)。

### 5-1-1. 動作中のデバイス切替(自動再開)
動作中(▶ 開始 中)に入力 / 出力デバイスを変更すると、**自動的に停止 → 再開** して新デバイスに
切り替わる(1〜2 秒の中断あり)。画面上部に青色のバナー「(入力/出力)デバイスを切り替えました
(再開中…)」が表示され、完了で自動的に消える。失敗時は赤色のエラーバナーに切り替わり、
パイプラインは停止する。

### 5-2. 出力デバイスの選び方 ← 重要
**必ず入力デバイスとは別のデバイスを選んでください。**
- 同じデバイスを選ぶと、翻訳音声をループバック入力で再キャプチャしてしまい、
  無限に翻訳が回るフィードバックループになります。
- アプリ起動時に「入力=出力」を検出するとエラーで弾きます。

おすすめ構成例(英語YouTube → 日本語音声):
- 入力: `[LB] Speakers`(PCの既定スピーカのループバック)
- 出力: `Headphones`(ヘッドホン側)

### 5-3. 翻訳言語の選び方
- **入力言語 (src)**: 翻訳元の言語。`auto` で自動検出も可能。
- **出力言語 (tgt)**: 翻訳先の言語。例: `ja`(日本語), `en`(英語)。
- **動作中の変更**: 動作中(▶ 開始 中)に src / tgt を切り替えると、**次の発話から** 新言語が
  適用される(パイプライン停止は不要)。すでに ASR/翻訳キューに積まれている発話は古い言語の
  まま完走するため、切替直後に旧言語の翻訳が 1〜2 件出ることがあるが仕様。

### 5-4. レイヤ別の実装選択
バックエンドセクションのプルダウンから選択する:
- 音声取得: `デバイス (soundcard)` / 将来 `プロセス (proctap)` 等
  - 「**取得単位**(デバイス / プロセス)」+ 「**backend 名**」を併記した表示形式。
  - 内部値(`config.yaml` の `backends.capture`)は backend 名のまま。
  - 同じ取得単位に複数 backend がある場合は併記された backend 名で識別する。
- VAD: `silero` / `webrtcvad` / `pyannote` / `pvcobra`(後 3 つは `--extra vad-extra` で追加)
- ASR: `faster_whisper` / `openai_whisper` / `openai_whisper_api` / `google_stt` / `deepgram`
- 翻訳: `nllb200` / `deepl` / `openai_gpt` / `anthropic_claude`
- TTS: `sapi` / `piper` / `elevenlabs` / `openai_tts` / `google_cloud_tts` / **`(なし)`**
- 音声出力: `soundcard`

### 5-4-0. TTS=(なし) でテキスト字幕モードにする
TTS プルダウンの末尾に `(なし)` の選択肢がある。これを選ぶと:
- **TTS と Output レイヤを使わない構成**になる(text_only モード)。
- 翻訳結果は **画面の履歴** に表示されるだけで、音は鳴らない。
- 「音声出力」行は自動的にグレーアウトされ、出力デバイスの選択は無効化される(復帰時に
  使えるよう、選択値自体は記憶される)。
- 起動時の TTS / Output レイヤのロードもスキップされる(クラウド TTS の認証情報が
  無くても、テキスト字幕モードならそのまま開始できる)。
- ※ 切替は **次回 Start** で反映される(動作中の即時切替は対象外)。設定を保存して
  Stop → Start すれば新モードに切り替わる。

各実装の**右側にステータスラベル**が表示されます(英語、色付き):
- **`Init`(グレー)** — アプリ起動直後やバックエンド切替直後の初期状態(まだロードを起動していない)
- **`Missing Credentials`(赤)** — クラウド backend で認証情報が未設定
- **`Not Downloaded`(赤)** — ロード失敗(キャッシュ無 + DL 失敗等)
- **`Downloading...`(オレンジ)** — モデル DL 中
- **`Loading...`(オレンジ)** — メモリへロード中
- **`Loaded`(緑)** — メモリにロード済み(開始ボタンが押せる状態)

通常は `Init → Loading... → Loaded` と推移します。

実装をプルダウンから切り替えると、**そのレイヤだけが Init に戻り、即座に再ロード**されます
(他のレイヤは常駐したままなので、開始ボタンの待ち時間は最小限)。
ただし TTS=(なし) を選んだ場合は再ロードは走らず、Init のまま留まります(text_only モードで
TTS / Output レイヤは使われないため)。

### 5-4-1. レイヤ別の細かい設定 (「設定」ボタン)
各レイヤ行の右側に **「設定」ボタン** があります。クリックすると、そのレイヤ固有の
設定編集ダイアログが開きます。

現在編集できる項目:
| レイヤ | 設定項目 |
|---|---|
| 音声取得 | 入力バッファ容量 (bytes) — VAD出力PCM を ASR に渡すバッファのバイト上限 |
| ASR | 認識結果バッファ件数 — ASR→翻訳 のキュー上限件数 |
| 翻訳 | 翻訳結果バッファ件数 — 翻訳→TTS のキュー上限件数 |
| TTS | 読み上げ速度 (rate) — SAPI 選択時のみ表示 |
| 音声出力 | 出力バッファ容量 (bytes) — TTS合成PCM を再生段に渡すバッファのバイト上限 |
| VAD | (まだ編集項目なし) |

「保存」ボタンで `config.yaml` の in-memory 値が更新されます。pipeline 関連の値は
**次の「▶ 開始」を押した時に Coordinator に渡される** ので、動作中なら一度停止
→開始してください。永続化したい場合は SettingsPanel の「設定を保存」を押します。

### 5-5. ログ出力先
ログとjsonl履歴の保存先フォルダ。既定は `./logs`。

---

## 6. 動作開始/停止
- アプリ起動直後はバックグラウンドでモデルがロードされます。各レイヤのステータスが Loading → Loaded
  と順に変化し、その間「▶ 開始」ボタンは「モデル準備中…」(無効)です。
- 全レイヤが `Loaded` になるとボタンが有効化されます。「▶ 開始」を押すと、**ロードはスキップして処理だけが起動**
  し、ほぼ即座に「■ 停止」に切り替わります(動作中)。
- 「■ 停止」で **処理スレッドのみ停止**します。モデルは常駐したままなので、再度 Start するときは
  ロード待ちなしです。
- 動作中は最新の翻訳テキスト履歴(右上「クリア」ボタンで消去可能)と直近の平均レイテンシが表示されます。
  - **平均レイテンシ**は「発話の終端が確定してから再生指示が出るまで」の時間(直近 10 件の平均)。
    発話そのものの長さは含まれないため、「喋り終わってから音が返ってくるまでの遅延」の体感に対応します。
    録音開始〜再生戻りまでのトータル時間や、各段(文字起こし/翻訳/音声合成)の内訳は
    `processtime.csv` で確認できます(設定でログを有効にした場合)。
- **致命的エラー**が発生すると履歴に `[致命的エラー] ...` を表示し、状態は「停止中(エラー)」になります。
  軽微な警告(無音スキップ等)は UI には出さず、`app.log` に記録されます(設定の `log.level` で抑制可)。

---

## 7. 設定の保存と読込
- 「設定を保存」: 現在の設定を `config.yaml` (カレントディレクトリ) に書き出します。
- 「設定を再読込」: ファイルから再読込します(編集後に反映したいときに)。
- 次回起動時には保存済み `config.yaml` が自動で読み込まれます。

### config.yaml を手で編集した内容を反映させる手順
`config.yaml` は **アプリ起動時に1回だけ in-memory に取り込まれます**。動作中のアプリは
ファイルを監視しないので、外部エディタで書き換えただけでは反映されません。

| やりたいこと | 手順 |
|---|---|
| 起動前に編集した | 通常起動するだけで反映される(=なにもしなくていい) |
| 起動中に編集した | GUI「設定を再読込」を押す → 次の「▶ 開始」で反映される |
| GUI で変更したものを残したい | GUI「設定を保存」を押す(ファイルに書き戻される) |

**注意**:
- 「設定を保存」を押すと **in-memory の状態でファイルが上書きされる**。手編集と GUI 操作を混ぜると上書き事故が起こり得るので、片方に統一するのが安全。
- `pipeline.*` のようなパイプライン関連値は **「▶ 開始」を押した時** に読み込まれて Coordinator に渡される。動作中に値を変えても、いったん「■ 停止」→「▶ 開始」しないと反映されない。
- `config.yaml` に書いていないキー(例: `pipeline:` セクションをまるごと省略した場合)は **既定値が自動で補完される** ので、書く必要のないキーは省略してかまわない。

---

## 8. ログ・履歴の場所
- アプリ全般ログ: `<ログ出力先>/app.log`
- 翻訳履歴(jsonl): `<ログ出力先>/translations.jsonl`
  - 1行 = 1発話、`{ts, seq_id, src_text, tgt_text, src_lang, tgt_lang, latency_ms, timeline}` を含む。
- (任意) 翻訳前テキスト: `<ログ出力先>/soundsrc.txt`
- (任意) 翻訳後テキスト: `<ログ出力先>/translated.txt`
  - 既定は OFF。`config.yaml` で個別に ON 可能(下記参照)。
  - 書式: `[YYYY-MM-DD HH:MM:SS] #SEQ [lang] text`

### seq_id で各種ログを突き合わせる
発話ごとに一意な連番 (`seq_id`) が振られ、`app.log` / `soundsrc.txt` / `translated.txt` / `translations.jsonl` のすべてに載る。
特定の発話を追いたいときは `grep "#42"`(テキストログ)や `jq 'select(.seq_id == 42)' translations.jsonl` のように突き合わせると、その発話が
どの段まで到達したか・どこでドロップしたかが判別できる。

### 翻訳前後テキストの個別ログ(デバッグ用)
翻訳品質を斜め読みでチェックしたいときに使う。`config.yaml` を編集して有効化:
```yaml
log:
  directory: ./logs
  jsonl_enabled: true
  src_text_enabled: true    # 翻訳前(soundsrc.txt)を出力する
  tgt_text_enabled: true    # 翻訳後(translated.txt)を出力する
```
- src/tgt 個別に ON/OFF 可能
- 空テキストの発話はスキップされる(無音応答ノイズを抑える)
- 追記モード(起動ごとに継続)、ローテーションなし
- **キューあふれで再生されなかった発話も** src/tgt は記録される(各段で直接書かれるため、再生されなくても翻訳結果は失われない)

### 同種エラーの連発で UI が埋もれる場合
`config.yaml` で UI 通知の集約・抑制を制御できる:
```yaml
notifications:
  throttle_sec: 5.0   # 既定 5秒。同じ (stage, 例外型) は 5 秒に1回しか UI に通知しない。
                      # 0 にすると無効化(全件通知)。
```
- 抑制された件数は次の通知メッセージに `(+N件抑制)` として表示
- **app.log には抑制せず全件記録**される(調査時はログを見ればOK)

### ログレベルを変える(SKIP のノイズを抑えたい等)
`config.yaml` で app.log に出すしきい値を制御できる:
```yaml
log:
  directory: ./logs
  level: WARNING   # 既定 INFO。SKIP(無音/空入力等)を抑えたければ WARNING に上げる
```
- 値: `DEBUG` / `INFO`(既定) / `WARNING` / `ERROR`
- severity 別の対応: FATAL → ERROR、RECOVERABLE → WARNING、WARN → WARNING、SKIP → INFO
- 設定変更後は再起動で反映

### ステージ間バッファの容量を変える
PC性能や発話頻度に応じて、ステージ間キューの上限を調整できる。`config.yaml` を編集:
```yaml
pipeline:
  # PCM 系(バイト基準)。1発話=サンプル数×4byte。
  # 16kHz×float32 換算: 10MB ≒ 約 156 秒分、5MB ≒ 約 78 秒分。
  captured_queue_max_bytes: 10000000     # Input → ASR(既定 10MB)
  synthesized_queue_max_bytes: 5000000   # TTS → Output(既定 5MB)
  # テキスト系(件数基準)。テキスト1発話は数百バイトなのでバイト基準より件数が直感的。
  recognized_queue_size: 10              # ASR → Translator
  translated_queue_size: 10              # Translator → TTS
```
- **PCM 系の挙動**: 「設定値を超えるまで積み、超えたら古い順に退避」。`push` した発話は必ず残り、合計が `max_bytes` を超えていれば古いものから捨てる。**運用上は設定値を少し超える前提**(1発話が単独で上限を超えても、その発話は残る)。
- **テキスト系の挙動**: `*_size` 件で頭打ち。あふれたら古いものから 1 件ずつ退避(従来通り)。
- 退避された発話は `app.log` に `queue overflow in ... dropped N utterance(s) (seq=[...])` で記録される。
- **使い分けの目安**:
  - 翻訳/TTS が時間的に詰まりやすい → ASR の入力 (`captured_queue_max_bytes`) を増やす
  - メモリ消費を抑えたい → 各 `*_max_bytes` を 200_000 〜 300_000 に下げる
- 設定変更後はアプリ再起動で反映(動作中の Coordinator には反映されない)。

### 各レイヤの処理時間を CSV で記録する(プロファイル用)
レイテンシのボトルネックを調査したいときに使う。`config.yaml` を編集して有効化:
```yaml
log:
  directory: ./logs
  process_time_enabled: true   # ./logs/processtime.csv に追記される
```

`processtime.csv` の各列(1 行 = 1 発話):

| 列 | 意味 |
|---|---|
| `timestamp` | 書き込み時刻(ISO 形式) |
| `seq_id` | 発話 ID(他ログと突き合わせ可) |
| `src_lang` / `tgt_lang` | 言語ペア |
| `utterance_ms` | `t_vad_end - t_capture`(発話の音声長 + VAD ラグ) |
| `asr_wait_ms` | `captured_queue` で待たされた時間 |
| `asr_proc_ms` | ASR の純処理時間(`transcribe` の所要) |
| `translate_wait_ms` | `recognized_queue` で待たされた時間 |
| `translate_proc_ms` | 翻訳の純処理時間 |
| `tts_wait_ms` | `translated_queue` で待たされた時間 |
| `tts_proc_ms` | TTS の純処理時間 |
| `output_wait_ms` | `synthesized_queue` で待たされた時間 |
| `output_proc_ms` | `output.play()` の所要時間 |
| `total_ms` | `t_playback - t_capture`(端から端まで) |
| `src_chars` / `tgt_chars` | テキスト長(参考値) |

- 既定 **OFF**。プロファイルしたい時だけ ON にする(常時 ON でも書込みは軽量)。
- 追記モード。起動ごとに継続される(ローテーションなし)。
- 失敗等で欠損したマーカーがあると、その列は空欄。
- ヒント:
  - `*_proc_ms` が大きい段が処理ボトルネック
  - `*_wait_ms` が大きい段は **直前のステージが詰まっている**(or バッファが小さい)
  - 例: `translate_wait_ms` が大きいなら ASR の出力速度に翻訳が追いつけていない

### GPU(CUDA / Apple Silicon)を使う
NLLB-200(翻訳)と faster-whisper(ASR)は GPU があれば自動的に使用します。
何もしなくても **`device: auto`** が既定で、CUDA → MPS → CPU の順に試行されます。

**現状を UI で確認**:
- **設定パネル** の各レイヤ右側のステータスラベルが `Loaded (cuda)` / `Loaded (cpu)` のように **実際に使われているデバイス**を表示します(ASR と 翻訳 が対象)。
- **動作パネル**の中ほどに **「演算: GPU (cuda)」「演算: CPU のみ」「演算: -(モデル準備中)」** という集約サマリが出ます。
  - **緑** = GPU を1つでも使用中
  - **オレンジ** = 全レイヤ CPU(動作はするが速度面で最速ではない)
  - **グレー** = 起動直後・モデル準備中

明示的にデバイスを指定したい場合(動作確認 / トラブルシュート):
```yaml
backends_config:
  nllb200:
    device: auto          # "auto" / "cuda" / "mps" / "cpu"
  faster_whisper:
    device: auto          # "auto" / "cuda" / "cpu"(CTranslate2 は MPS 未対応)
    compute_type: auto    # "auto" / "int8" / "float16" / "int8_float16" 等
                          # auto なら GPU=float16、CPU=int8 が選ばれる
```

- **CUDA 想定**: NVIDIA GPU。`nvidia-smi` でドライバが認識されていれば自動検出されます。
- **MPS 想定**: Apple Silicon(M1/M2/M3)。NLLB のみ対応(faster-whisper は CPU 落ち)。
- **CPU 想定**: その他すべて。デフォルトでも問題なく動作しますが翻訳速度が遅め。
- GPU で起動に失敗した場合は **自動的に CPU にフォールバック** します(ログに記録)。
- 設定変更後はアプリ再起動で反映。

### VAD の発話区切り設定(長文連続発話で詰まるとき)
ニュース放送のように **話者がほとんどポーズしない** 入力では、1 発話が数十秒〜数分の塊に
なってしまい、翻訳/TTS/再生が破綻する(レイテンシが入力速度に追いつかなくなる)ことが
ある。Silero VAD の挙動を `config.yaml` で調整できる:

```yaml
backends_config:
  silero:
    threshold: 0.5         # speech probability 判定しきい値(0〜1)。下げると敏感に
    min_silence_ms: 500    # 発話終了とみなす無音期間(ms)。短くすると早く区切れる
    speech_pad_ms: 100     # 発話前後の余白(ms)
    max_speech_sec: 8.0    # 1 発話の最大長(秒)。超えたら強制区切り。0 で無効化
```

- `max_speech_sec` を超えると、VAD の自然な end イベントを待たずに **強制的に区切られ**、
  次のサンプルから新しい発話として再開する(VAD のモデル内部状態は維持されるため、継続発話が
  きちんと拾える)。
- ニュース等のノンストップ素材では **`min_silence_ms: 200` + `max_speech_sec: 5.0`** くらいが
  目安。短い発話に意味の切れ目を作って下流の翻訳/再生を回しやすくする。
- 通常会話なら既定値(500ms / 8秒)で十分。
- 設定変更後はアプリ再起動で反映される(動作中の VAD には反映されない)。

### TTS の読み上げ速度を変える
SAPI(pyttsx3) の rate を `config.yaml` で変更可能:
```yaml
backends_config:
  sapi:
    rate: 220     # 既定 180。早口にすると再生時間が短くなる(キューあふれ抑制に有効)
```
- 既定 180(普通の早さ)→ 220 程度で早口、180 未満でゆっくり
- 設定変更後は GUI の「設定を再読込」ボタンを押すか、アプリ再起動で反映

---

## 9. トラブルシュート

### 9-1. 「入力と出力に同じデバイスは使用できません」と出る
入出力デバイスを別物に設定してください(フィードバック防止のため)。

### 9-2. 音声が出ない
- 出力デバイスが正しく選ばれているか
- OSのボリュームミキサーで該当デバイスがミュートになっていないか
- 入力デバイスからそもそも音が来ているか(波形表示はMVP未実装)

### 9-3. 翻訳されない
- 「停止中(エラー)」になっていないか
- `app.log` を確認(致命/警告がログされているはず)

### 9-4. レイテンシが大きい
- ASR モデルが `small` 以上だと CPU では数秒かかります。
  `tiny` に変えると速くなりますが精度が落ちます(現状GUIでは未公開、コード側で変更)。
- 翻訳(NLLB-200 600M)も CPU だと数百ms〜1秒程度かかります。
- GPU 利用は将来対応。

### 9-5. モデルDLでネット接続が必要なのに通信できない
- ASR: `~/.cache/huggingface/hub` または faster-whisper のキャッシュフォルダ
- NLLB-200: `~/.cache/huggingface/hub`
- 一度ダウンロードできれば以降はオフラインで動きます。

---

## 10. 開発者向け

開発用コマンド:
```bash
py -m uv run pytest                              # small のみ(既定 / 高速、毎コミット用)
py -m uv run pytest -m middle                    # middle のみ(機能更新時に確認)
py -m uv run pytest -m large                     # large のみ(実モデル/実デバイス必須、手動)
py -m uv run pytest -m "middle or large"         # リリース前まとめ実行
py -m uv run pytest -m "" --override-ini="addopts="  # 既定 addopts を override して全部
py -m uv run pytest --cov=src                    # カバレッジ表示
py -m uv run pytest -v                           # テスト名表示
py -m uv add <package>                           # 依存追加
```

テスト階層の方針: `CLAUDE.md` の「テスト階層 (small / middle / large)」を参照。

設計ドキュメント:
- [アーキテクチャ](design/Architecture.html)
- [クラス詳細](design/Class.md)
- [ユーザシナリオ](design/UserSinario.md)
- [全体タスク](design/TaskList.md)
