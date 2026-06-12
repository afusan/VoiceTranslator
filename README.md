# voice-translator

ローカル PC で完結するリアルタイム音声翻訳アプリ。

- **PC 内で再生されている音声**(YouTube / 配信 / 通話など)や**マイク入力**を取り込み、
  別言語に翻訳した音声を**別の出力デバイス**から再生する
- 既定構成は**すべてローカルで動作**(音声を外部に送らない)。クラウド backend は opt-in
- 仮想マイクと組み合わせれば「自分の声を翻訳して通話相手に送る」使い方も可能

```
[入力デバイス] → VAD → ASR → 翻訳 → TTS → [出力デバイス]
              (発話区切り)(書き起こし)(翻訳)(音声合成)
```

使い方の詳細は **[docs/manual.md](docs/manual.md)** を参照。

---

## 特徴

- **CPU だけで動く**(floor 方針)。NVIDIA GPU があれば自動で使い、ASR / 翻訳が大幅高速化
- **レイヤごとに backend を差し替え可能**(ローカル / クラウドを GUI のプルダウンで選択)
- **per-process キャプチャ**(Windows): 特定アプリの音だけを翻訳対象にできる
- **ASR+翻訳の複合 backend**: 書き起こしと翻訳を 1 回で行う End-to-End 構成も選べる
- **テキスト字幕モード**: TTS=「(なし)」で翻訳テキスト表示のみの軽量構成
- クラウド backend は**同意ダイアログ + API key の疎通テスト**を通すまで起動をブロック
  (誤課金・誤送信の防止)

---

## 動作環境 / OS 対応状況

| OS | 状態 | 備考 |
|---|---|---|
| **Windows 11** | ✅ 完全対応(主ターゲット) | 全 backend が動く |
| macOS / Linux | ⚠ 部分動作 | 下記「Windows 専用」を別 backend に置き換える |

**Windows 専用**: `sapi` TTS(既定 TTS。代替: `piper` / クラウド TTS / 字幕モード)、
`proc-tap`(per-process キャプチャ。代替なし)、スピーカループバックの仕組み
(macOS: BlackHole 等 / Linux: PipeWire monitor を別途用意)。
VAD / ASR / 翻訳 / Piper TTS / クラウド系はクロス OS で動作する。

---

## クイックスタート

```bash
# 1) uv をインストール(いずれか)
py -m pip install --user uv
# or: powershell -c "irm https://astral.sh/uv/install.ps1 | iex"

# 2) 依存と Python(3.12+)を自動セットアップ — cpu / cuda はどちらか必須
py -m uv sync --extra cpu            # CPU 専用(誰でも動く)
# py -m uv sync --extra cuda         # NVIDIA GPU 向け(+約3GB)

#    追加 backend を全部入れる場合(任意):
# py -m uv sync --extra cpu  --extra full
# py -m uv sync --extra cuda --extra full

# 3) 起動 — sync 時と同じ extras を付ける
py -m uv run --extra cpu python -m voice_translator
```

> **`uv run` のクセに注意**: `uv run` は既定で「extras 無し」の sync を再実行するため、
> 起動時に `--extra` を省くと入れた extras が剥がされます。起動コマンドにも sync と
> 同じ extras を付けるか、`--no-sync` を付けてください。

> **cpu / cuda は排他**です。**CUDA Toolkit のインストールは不要**(wheel に同梱。
> NVIDIA ドライバがあれば動く)。Apple Silicon は `--extra cpu` で MPS が自動利用されます。

> **初回起動**: 「▶ 開始」時に ASR モデル(数百 MB)と翻訳モデル(NLLB-200 約 2.5GB)の
> ダウンロードが走ります(数分〜十数分)。以降はキャッシュから読み、オフラインで動きます。

**デバイス選択(重要)**: 入力と出力には**別のデバイス**を選んでください(同一だと
フィードバックループになるため起動時に弾かれます)。
例: 入力 `[LB] Speakers`(ループバック)/ 出力 `Headphones`。

---

## レイヤ構成と backend 一覧

各レイヤは差し替え可能な抽象 I/F で設計されている。**既定(MVP)構成は同意手続き不要の
ローカル backend のみ**。追加 backend は extras を入れた分だけ GUI のプルダウンに現れる
(未導入のものは列挙されない)。

| レイヤ | 既定(ローカル) | 追加 backend(extras) |
|---|---|---|
| 音声取得 | `soundcard`(デバイス / ループバック) | `proc-tap`(Windows、per-process)`capture-proctap` |
| VAD | `silero` | `webrtcvad` / `pyannote` / `pvcobra` — `vad-extra` |
| ASR | `faster_whisper` | `openai_whisper`(公式)`asr-whisper-official` / `openai_whisper_api`・`google_stt`・`deepgram`(クラウド)各 `asr-*` |
| 翻訳 | `nllb200`(200 言語) | `deepl`・`openai_gpt`・`anthropic_claude`(クラウド)各 `translator-*` |
| TTS | `sapi`(Windows) | `piper`(ローカル)`tts-piper` / `elevenlabs`・`openai_tts`・`google_cloud_tts`(クラウド)各 `tts-*` / **「(なし)」= 字幕モード** |
| 音声出力 | `soundcard` | — |

**ASR+翻訳の複合 backend**(ASR レイヤで選択。翻訳レイヤは自動的に省略される):
`faster_whisper_translate`(ローカル・英語固定)/ `openai_whisper_api_translate`
(クラウド・英語固定)/ `gpt_audio_translate`(クラウド・任意言語・原文付き)。

全部入れる集約プリセット: `--extra full`(cpu / cuda と併用)。

---

## 利用同意・ライセンス(追加 backend)

追加 backend は**それぞれのサービス規約 / モデルライセンスへの同意が前提**になる。
アプリは選択時に同意ダイアログで規約 URL を提示し、クラウド backend は API key の
疎通テストを通すまで起動をブロックする(自動で同意することはない)。

| backend | 形態 | 同意 / 認証 |
|---|---|---|
| `soundcard` / `silero` / `webrtcvad` / `faster_whisper` / `nllb200` / `sapi` / `piper` / `proc-tap` / `openai_whisper` | ローカル | OSS ライセンス(MIT / BSD / Apache 等)。同意手続き・認証不要 |
| `pyannote`(VAD) | ローカル(gated) | [HF でモデル利用同意](https://huggingface.co/pyannote/segmentation-3.0) + HF Token |
| `pvcobra`(VAD) | ローカル | [Picovoice](https://picovoice.ai/pricing/)(個人非商用 / 商用は別ライセンス)+ Access Key |
| `openai_whisper_api` / `openai_tts` / `openai_gpt` / `gpt_audio_translate` 等 | クラウド | [OpenAI 利用規約](https://openai.com/policies/terms-of-use) + API key |
| `google_stt` / `google_cloud_tts` | クラウド | [Google Cloud 規約](https://cloud.google.com/terms) + サービスアカウント JSON |
| `deepgram` | クラウド | [Deepgram Terms](https://deepgram.com/terms-of-service) + API key |
| `deepl` | クラウド | [DeepL 規約](https://www.deepl.com/pro-license) + API key |
| `elevenlabs` | クラウド | [ElevenLabs Terms](https://elevenlabs.io/terms-of-use) + API key |

API key 等は **OS の資格情報ストア**(Windows Credential Manager / macOS Keychain)に
保管される。使えない環境ではプロジェクト直下 `local.secrets` に平文 fallback(git 管理外)。

---

## 開発

```bash
py -m uv run pytest               # 単体テスト(small)
py -m uv run pytest --cov=src     # カバレッジ
```

テスト方針・運用ルールは [CLAUDE.md](CLAUDE.md)、テスト階層(small / middle / large)も同所を参照。

## ドキュメント

- [使い方マニュアル](docs/manual.md)
- [アーキテクチャ](docs/design/Architecture.html)
- [クラス詳細](docs/design/Class.md)
- [ユーザシナリオ](docs/design/UserSinario.md)
- [保留・暫定決定](docs/design/pendList.md)
