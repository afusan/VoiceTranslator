# Class (クラス・モジュール詳細)

`Architecture.html` で示したレイヤ構成を、**クラス/モジュール単位の責務まで落とし込んだ詳細**。
役割の上位ビュー(レイヤ・I/F)については [Architecture.html](Architecture.html) を参照。

---

## 1. パイプラインステージ(各レイヤ)

各レイヤは「**抽象インタフェース + 具象実装**」で構成する。
MVPでは各レイヤ1実装のみ提供し、将来差し替え可能な拡張点として残す。

| インタフェース | 役割 | MVP実装 | 備考 |
|---|---|---|---|
| `AudioCaptureBackend` | 音声デバイスから PCM チャンクを取得して内部標準形式に正規化する | `SoundcardCaptureBackend` | Win/Mac/Linux 抽象化 |
| `VadBackend` | PCM ストリームから発話区間を検出し、発話単位に切り出す | `SileroVadBackend` | パラメータ(無音閾値等)は設定化 |
| `AsrBackend` | 発話単位の音声を入力言語のテキストに書き起こす | `FasterWhisperAsrBackend` | `task=transcribe` 固定 |
| `TranslatorBackend` | テキストを src 言語 → tgt 言語 に翻訳する | `Nllb200TranslatorBackend` | 200言語対応、ローカル動作 |
| `TtsBackend` | テキストを音声(PCM)に合成する | `SapiTtsBackend` | pyttsx3経由、後で差し替え予定 |
| `AudioOutputBackend` | PCM を指定デバイスで再生する | `SoundcardOutputBackend` | 出力デバイスを別途指定 |

---

## 2. パイプライン制御

| クラス | 役割 |
|---|---|
| `PipelineCoordinator` | 各ステージを直列につなぎ、発話単位データ(`Utterance`)を流す。ステージのライフサイクル(start/stop)を管理する。 |
| `Utterance` | 1発話を表すデータクラス。生PCM、書き起こしテキスト、翻訳テキスト、合成PCM、各ステージのタイムスタンプ(`timeline`)を保持。 |
| `UtteranceTimeline` | `Utterance` 内の時刻記録用辞書のラッパ。`mark(stage_name)` で `monotonic()` を打つ。 |

### `Utterance` のフィールド(暫定)

| フィールド | 型 | 付与タイミング | 内容 |
|---|---|---|---|
| `pcm` | `np.ndarray[float32]` | VAD確定時 | 発話区間のPCM(16kHz/mono/float32) |
| `src_lang` | `str` | 設定 or ASR検出 | 入力言語コード(例: "en", "ja", "auto") |
| `src_text` | `str` | ASR完了時 | 書き起こしテキスト |
| `tgt_lang` | `str` | 設定 | 翻訳先言語コード(例: "ja") |
| `tgt_text` | `str` | Translator完了時 | 翻訳テキスト |
| `tts_pcm` | `np.ndarray` | TTS完了時 | 合成音声PCM(エンジン依存形式) |
| `timeline` | `UtteranceTimeline` | 各ステージで mark | `{stage_name: monotonic_time}` |

---

## 3. 横断機能

| クラス | 役割 |
|---|---|
| `ConfigStore` | 設定値(選択中のバックエンド名、デバイス、言語ペア、ログ出力先 等)の永続化(YAML)と読込。 |
| `Logger` | アプリ全般のログ + 翻訳履歴の jsonl 出力。出力先と各種ON/OFFは `ConfigStore` から取得。 |
| `ErrorHandler` | 例外を `AppError` 階層で分類し、致命=ダイアログ/回復=リトライ/スキップ/警告 のいずれかに振り分ける。`ErrorCatalog`(未作成) を参照。 |
| `AppError` (基底例外) | `severity` (FATAL/RECOVERABLE/SKIP/WARN) を持つ。各バックエンドは下位例外をこれに包んで送出する。 |
| `DeviceValidator` | 起動時に「入力デバイス ≠ 出力デバイス」を保証。違反時は警告ダイアログで起動拒否。 |

### `AppError` の severity と挙動

| severity | 例 | 挙動 |
|---|---|---|
| `FATAL` | モデルロード失敗、デバイス消失、設定ファイル破損 | ダイアログ表示 → 動作停止 → 必要なら再起動を促す |
| `RECOVERABLE` | 翻訳APIタイムアウト、ASR一時失敗 | N回までリトライ → ログ出力して継続 |
| `SKIP` | 無音/短すぎ、ASR結果が空 | 当該発話を破棄してログのみ |
| `WARN` | レイテンシ閾値超過、音量過大/過小 | GUIに通知バナー、ログ蓄積 |

---

## 4. GUI

| クラス | 役割 |
|---|---|
| `MainWindow` | アプリのルートウィンドウ。設定パネルと動作パネルを内包する(customtkinter)。 |
| `SettingsPanel` | レイヤ別バックエンド選択 / src/tgt 言語 / 入出力デバイス選択 / ログ出力先指定 / 設定保存・読込のUI。 |
| `ControlPanel` | 動作開始/停止トグル、直近レイテンシ表示、最新の翻訳テキスト表示。 |
| `BackendRegistry` | 各レイヤの利用可能バックエンドを登録・列挙する。GUIのプルダウン項目供給に使う。 |

---

## 5. 拡張時の追加例(参考)

- **OS別音声取得を追加**: `AudioCaptureBackend` を OS 別に実装(例: `GstreamerCaptureBackend`、`WasapiProcessCaptureBackend`)し、`BackendRegistry.register()` する。
- **TTS差し替え**: `TtsBackend` の新実装(例: `VoicevoxTtsBackend`)を登録するだけで GUI のプルダウンに出現。
- **LLM翻訳**: `TranslatorBackend` の追加実装(例: `OllamaTranslatorBackend`)を登録。

---

## 6. 命名・配置の規約

- ファイル配置は `src/<layer>/<implementation>.py`(例: `src/asr/faster_whisper_backend.py`)。
- 抽象I/Fは `src/<layer>/backend.py`(例: `src/asr/backend.py` に `AsrBackend`)。
- バックエンドクラスの命名は `<実装名>Backend` で揃える。
- 各クラスの**冒頭1〜2行コメントに役割を明記**(CLAUDE.md 準拠)。
