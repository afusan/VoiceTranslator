# Architecture (アーキテクチャ)

本ドキュメントはアプリの**レイヤ構成**と**主要クラスの役割**を定義する。
各クラスはコード側のクラスコメントにも同じ役割を1〜2行で明記する(CLAUDE.md の前提に準拠)。

---

## 1. 全体レイヤ図

```
 ┌──────────────┐    ┌──────┐    ┌──────┐    ┌────────────┐    ┌──────┐    ┌──────────────┐
 │ AudioCapture │ →  │ VAD  │ →  │ ASR  │ →  │ Translator │ →  │ TTS  │ →  │ AudioOutput  │
 └──────────────┘    └──────┘    └──────┘    └────────────┘    └──────┘    └──────────────┘
       │                                                                          │
       └─────────────────── PipelineCoordinator (発話単位を流す) ────────────────────┘
                                          │
                  ┌───────────────────────┼───────────────────────────┐
                  ↓                       ↓                           ↓
            ConfigStore               Logger                    ErrorHandler
            (設定保存/読込)         (画面+jsonl)             (致命/回復/スキップ判定)
```

- **データはステージ間を `Utterance`(発話単位)で流れる**。
- 各ステージは**抽象インタフェース + 1つの具象実装**で構成し、後で差し替え可能にする。
- 監視・操作系として GUI と Logger/ErrorHandler/ConfigStore が横断する。

---

## 2. クラス・モジュール一覧

### 2-1. パイプラインステージ(各レイヤ)

| クラス/インタフェース | 役割 | MVP実装 |
|---|---|---|
| `AudioCaptureBackend` (I/F) | 音声デバイスから PCM チャンクを取得して内部標準形式に正規化する | `SoundcardCaptureBackend` |
| `VadBackend` (I/F) | PCM ストリームから発話区間を検出し、発話単位に切り出す | `SileroVadBackend` |
| `AsrBackend` (I/F) | 発話単位の音声を入力言語のテキストに書き起こす | `FasterWhisperAsrBackend` |
| `TranslatorBackend` (I/F) | テキストを src 言語 → tgt 言語に翻訳する | `Nllb200TranslatorBackend` |
| `TtsBackend` (I/F) | テキストを音声(PCM)に合成する | `SapiTtsBackend` (pyttsx3) |
| `AudioOutputBackend` (I/F) | PCM を指定デバイスで再生する | `SoundcardOutputBackend` |

### 2-2. パイプライン制御

| クラス | 役割 |
|---|---|
| `PipelineCoordinator` | 各ステージを直列につなぎ、発話単位データ(`Utterance`)を流す。ステージのライフサイクル(start/stop)を管理する。 |
| `Utterance` | 1発話を表すデータクラス。生PCM、書き起こしテキスト、翻訳テキスト、各ステージのタイムスタンプ(`timeline`)を保持。 |
| `UtteranceTimeline` | `Utterance` 内の時刻記録用辞書のラッパ。`mark(stage_name)` で `monotonic()` を打つ。 |

### 2-3. 横断機能

| クラス | 役割 |
|---|---|
| `ConfigStore` | 設定値(選択中のバックエンド名、デバイス、言語ペア、ログ出力先 等)の永続化(YAML)と読込。 |
| `Logger` | アプリ全般のログ + 翻訳履歴の jsonl 出力。出力先と各種ON/OFFは `ConfigStore` から取得。 |
| `ErrorHandler` | 例外を `AppError` 階層で分類し、致命=ダイアログ/回復=リトライ/スキップ/警告 のいずれかに振り分ける。`ErrorCatalog` を参照。 |
| `AppError` (基底例外) | `severity` (FATAL/RECOVERABLE/SKIP/WARN) を持つ。各バックエンドは下位例外をこれに包んで送出する。 |
| `DeviceValidator` | 起動時に「入力デバイス ≠ 出力デバイス」を保証。違反時は警告ダイアログで起動拒否。 |

### 2-4. GUI

| クラス | 役割 |
|---|---|
| `MainWindow` | アプリのルートウィンドウ。設定パネルと動作パネルを内包する(customtkinter)。 |
| `SettingsPanel` | レイヤ別バックエンド選択 / src/tgt 言語 / 入出力デバイス選択 / ログ出力先指定 / 設定保存・読込のUI。 |
| `ControlPanel` | 動作開始/停止トグル、直近レイテンシ表示、最新の翻訳テキスト表示。 |
| `BackendRegistry` | 各レイヤの利用可能バックエンドを登録・列挙する。GUIのプルダウン項目供給に使う。 |

---

## 3. データフロー(発話1件あたり)

```
[1] AudioCapture: PCMチャンク連続取得
        ↓ (16kHz/mono/float32 に正規化)
[2] VAD: 無音区切りで Utterance を確定
        ↓ Utterance(pcm, t_capture, t_vad_end)
[3] ASR: 書き起こし
        ↓ Utterance(+src_text, t_asr)
[4] Translator: 翻訳
        ↓ Utterance(+tgt_text, t_translate)
[5] TTS: 音声合成
        ↓ Utterance(+tts_pcm, t_tts)
[6] AudioOutput: 再生
        ↓ Utterance(+t_playback)
[7] Logger: jsonl に1行記録(t_*, src_text, tgt_text, 言語, レイテンシ)
```

---

## 4. 拡張ポイント

- **新しいバックエンド追加**: 該当 I/F を実装 → `BackendRegistry` に登録するだけで GUI に出現。
- **OS別音声取得**: `AudioCaptureBackend` を OS 別に実装(Win=Soundcard/Wpatch、Linux=GStreamer 等)し、`BackendRegistry` 経由で切替。
- **per-app 取得**: 上記 OS別バックエンドの一部として追加(将来。pendList参照)。
- **ローカルLLM翻訳**: `TranslatorBackend` の実装を追加(Ollama 経由など)。

---

## 5. 横断方針

- **役割の表明**: 各クラスのコメント/Docstring 冒頭1〜2行に役割を明記する(CLAUDE.md準拠)。
- **エラー**: 各バックエンドは内部例外を `AppError`(severity 付き) に包んで送出。中央で `ErrorHandler` が振り分け。詳細は `docs/design/ErrorCatalog.md` に集約予定(未作成)。
- **タイムスタンプ**: 全ステージで `Utterance.timeline.mark()` を呼ぶことを規約化。
- **デバイス分離**: `DeviceValidator` が起動時にチェック。AECは将来オプション。
