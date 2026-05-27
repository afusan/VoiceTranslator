# Class (クラス・モジュール詳細)

`Architecture.html` で示したレイヤ構成と 3 スレッド構成を、**クラス/モジュール単位の責務まで落とし込んだ詳細**。
役割の上位ビュー(レイヤ・I/F・スレッド)については [Architecture.html](Architecture.html) を参照。

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
| `TtsBackend` | テキストを音声(PCM)に合成する | `SapiTtsBackend` | pyttsx3経由、WAV経由でPCM取得 |
| `AudioOutputBackend` | PCM を指定デバイスで再生する | `SoundcardOutputBackend` | 出力デバイスを別途指定 |

---

## 2. パイプライン制御

| クラス | 役割 |
|---|---|
| `PipelineCoordinator` | **Input/Process/Output の3スレッド**を起動・停止し、上限付き `queue.Queue` で連携。各スレッドは `stop_event` で停止指示を受け、停止時はセンチネル投入で確実に終了する。 |
| `Utterance` | 1発話を表すデータクラス。生PCM、書き起こしテキスト、翻訳テキスト、合成PCM、各ステージのタイムスタンプ(`timeline`)を保持。 |
| `UtteranceTimeline` | `Utterance` 内の時刻記録用辞書のラッパ。`mark(stage_name)` で `monotonic()` を打つ。 |

### `PipelineCoordinator` の3スレッド責務

| スレッド | 処理 | キュー |
|---|---|---|
| Input | `capture.read_chunk` → `vad.process` で Utterance を生成して q1 に投入。 | q1 へ書き込み |
| Process | q1 から取り出し、`asr.transcribe` → `translator.translate` → `tts.synthesize` を直列実行し q2 に投入。**ASR完了直後に `utt.pcm = None` でメモリ解放**。 | q1 から読み / q2 へ書き込み |
| Output | q2 から取り出し、`output.play` で再生 → `on_utterance_done` コールバックを発火。 | q2 から読み |

- **キュー上限**: 既定 3(コンストラクタ引数 `queue_size` で変更可)。テストでは大きめ。
- **あふれ時**: 最古の要素を捨てて新しいものを優先(リアルタイム性確保)。捨てた発話は `on_dropped(items, stage_name)` コールバックで通知され、AppController が TextLogger に転送して**テキストだけは記録される**(再生はされない)。
- **エラー**: 各スレッド内で例外を捕捉し `ErrorHandler` に委譲。FATAL なら `stop_event` を立てて全スレッド停止。

### `Utterance` のフィールド

| フィールド | 型 | 付与タイミング | 内容 |
|---|---|---|---|
| `pcm` | `np.ndarray[float32]` \| `None` | VAD確定時 (ASR後に None でメモリ解放) | 発話区間のPCM(16kHz/mono/float32) |
| `src_lang` | `str` | 設定 or ASR検出 | 入力言語コード(例: "en", "ja", "auto") |
| `src_text` | `str` | ASR完了時 | 書き起こしテキスト |
| `tgt_lang` | `str` | 設定 | 翻訳先言語コード(例: "ja") |
| `tgt_text` | `str` | Translator完了時 | 翻訳テキスト |
| `tts_pcm` | `np.ndarray` | TTS完了時 | 合成音声PCM(エンジン依存形式) |
| `tts_samplerate` | `int` | TTS完了時 | 合成音声のサンプルレート(0 は未設定→既定値を使用) |
| `timeline` | `UtteranceTimeline` | 各ステージで mark | `{stage_name: monotonic_time}` |

---

## 3. アプリ制御層

| クラス | 役割 |
|---|---|
| `AppController` | GUI と内部モジュールの仲介。設定アクセス・デバイス列挙・**Loader スレッド経由の非同期起動 (`start_pipeline_async`)** ・モデル状態の管理を一括で提供する。 |
| `BackendRegistry` | レイヤ別バックエンドの登録/列挙/生成。GUIのプルダウン項目供給に使う。`register_default_backends(registry)` でMVP標準を一括登録。 |

### `AppController` の主要メソッド

| メソッド | 役割 |
|---|---|
| `start_pipeline_async(on_started, on_failed)` | DeviceValidator 同期チェック → Loader スレッドでバックエンド生成 → 完了/失敗を UI コールバックで通知。 |
| `start_pipeline()` | 同期版(テスト/スクリプト用)。GUI からは async 版を使う。 |
| `stop_pipeline()` | Coordinator を停止し参照を解放。複数回呼ばれても安全。 |
| `list_capture_sources()` / `list_output_devices()` | 設定中のバックエンドを使ってデバイス列挙(GUIプルダウン供給)。 |
| `get_setting()` / `set_setting()` / `save_settings()` / `load_settings()` | ConfigStore のラッパ。 |
| `set_callbacks(on_utterance_done, on_fatal, on_warn, on_status_change)` | GUI 側の更新ハンドラを登録する。 |
| `get_model_status(layer)` / `get_all_model_statuses()` | 各レイヤのモデル状態(`ModelStatus`)を取得。 |

### モデル状態 (`ModelStatus`)

| 値 | 表示 | 意味 |
|---|---|---|
| `NOT_DOWNLOADED` | "Not Downloaded" | キャッシュ無し。起動時にDLが走る。 |
| `LOADING` | "Loading..." | DL or メモリへロード中。 |
| `LOADED` | "Loaded" | メモリにロード済み or キャッシュ済み(即ロード可)。 |

---

## 4. 横断機能(共通)

| クラス/モジュール | 役割 |
|---|---|
| `ConfigStore` | 設定値(選択中のバックエンド名、デバイス、言語ペア、ログ出力先 等)の永続化(YAML)と読込。 |
| `Logger`(`setup_app_logger`) | stdout + `app.log` への汎用アプリログ初期化。 |
| `TranslationLogger` | 翻訳1件 = jsonl 1行 として履歴ファイルに追記。ON/OFF 切替可。機械処理向け。 |
| `TextLogger` | 翻訳前テキスト(`soundsrc.txt`)と翻訳後テキスト(`translated.txt`)を個別に追記。src/tgt 個別 ON/OFF。デバッグ用、書式 `[YYYY-MM-DD HH:MM:SS] [lang] text`。 |
| `ErrorHandler` | 例外を `AppError` 階層で分類し、致命=ダイアログ/回復=リトライ/スキップ/警告 のいずれかに振り分ける。`ErrorCatalog`(未作成) を参照。 |
| `AppError` (基底例外) | `severity` (FATAL/RECOVERABLE/SKIP/WARN) を持つ。各バックエンドは下位例外をこれに包んで送出する。 |
| `DeviceValidator` | 起動時に「入力デバイス ≠ 出力デバイス」を保証。違反時は FatalError で起動拒否。 |
| `cache_check` (モジュール) | `check_faster_whisper / check_nllb200 / check_silero / check_sapi / check_soundcard` の関数群。`huggingface_hub.try_to_load_from_cache` で軽量にキャッシュ有無を判定。 |

### `AppError` の severity と挙動

| severity | 例 | 挙動 |
|---|---|---|
| `FATAL` | モデルロード失敗、デバイス消失、設定ファイル破損 | ダイアログ表示 → 動作停止 → 必要なら再起動を促す |
| `RECOVERABLE` | 翻訳APIタイムアウト、ASR一時失敗 | N回までリトライ → ログ出力して継続 |
| `SKIP` | 無音/短すぎ、ASR結果が空 | 当該発話を破棄してログのみ |
| `WARN` | レイテンシ閾値超過、音量過大/過小 | GUIに通知バナー、ログ蓄積 |

---

## 5. GUI

| クラス | 役割 |
|---|---|
| `MainWindow` | アプリのルートウィンドウ。SettingsPanel と ControlPanel を内包する(customtkinter)。閉じる時にパイプライン停止を保証。 |
| `SettingsPanel` | レイヤ別バックエンド選択 / src/tgt 言語 / 入出力デバイス選択 / ログ出力先指定 / 設定保存・読込 + **レイヤ別モデルステータスラベル(色付き)**。 |
| `ControlPanel` | 動作開始/停止トグル、ロード中ステータス表示、最新翻訳テキスト履歴、直近平均レイテンシ表示。`start_pipeline_async` を呼んでUIをブロックしない。 |

### スレッドセーフ規約

- tkinter ウィジェットはメインスレッドからしか触れない。Coordinator/Loader スレッドからの通知は **`widget.after(0, lambda: ...)`** でメインスレッドに戻して反映する。
- AppController の callback(`on_utterance_done` / `on_fatal` / `on_warn` / `on_status_change`)は呼び出し元スレッドの上で実行されるため、UI 側は必ず `after()` 経由で処理すること。

---

## 6. 拡張時の追加例(参考)

- **OS別音声取得を追加**: `AudioCaptureBackend` を OS 別に実装(例: `GstreamerCaptureBackend`、`WasapiProcessCaptureBackend`)し、`BackendRegistry.register()` する。
- **TTS差し替え**: `TtsBackend` の新実装(例: `VoicevoxTtsBackend`)を登録するだけで GUI のプルダウンに出現。
- **LLM翻訳**: `TranslatorBackend` の追加実装(例: `OllamaTranslatorBackend`)を登録。
- **新バックエンドのキャッシュ判定追加**: `cache_check` に check 関数を追加し、`AppController._CACHE_CHECKER_NAMES` に登録。

---

## 7. 命名・配置の規約

- ファイル配置は `src/voice_translator/<layer>/<implementation>.py`(例: `src/voice_translator/asr/faster_whisper_backend.py`)。
- 抽象I/Fは `src/voice_translator/<layer>/backend.py`(例: `src/voice_translator/asr/backend.py` に `AsrBackend`)。
- バックエンドクラスの命名は `<実装名>Backend` で揃える。
- 各クラスの**冒頭1〜2行コメントに役割を明記**(CLAUDE.md 準拠)。
