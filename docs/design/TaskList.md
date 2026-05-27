# TaskList (実装タスクリスト)

実装の全体タスク。Phase単位で粒度を揃え、ブランチ単位の詳細は `docs/design/<branch-name>/Plan.md` に切り出す。

凡例: ☐ 未着手 / ◐ 着手中 / ☑ 完了

---

## Phase 0: 設計・骨格

| 状態 | タスク | 備考 |
|---|---|---|
| ☑ | プロジェクト方針定義(CLAUDE.md) | 完了 |
| ☑ | Architecture.html 作成 | 完了(HTMLで図を視覚化、3スレッド構成も記載) |
| ☑ | Class.md 作成 | 完了(AppController/ModelStatus含む) |
| ☑ | UserSinario.md 作成 | 完了 |
| ☑ | TaskList.md 作成 | 完了(=本ファイル) |
| ☑ | ErrorCatalog.md 雛形作成 | feature/error-context で完了。現状の raise 点と severity 判定を整理。 |
| ☑ | manual.md 雛形作成 → 肉付け | 完了(Step6で肉付け済み) |

---

## Phase 1: MVP (機能を最低限縦に通す)

「とりあえず英語のYouTubeを日本語音声で聞ける」が達成ライン。

| 状態 | タスク | 担当レイヤ | 備考 |
|---|---|---|---|
| ☑ | プロジェクト初期化(`src/` 配下、依存管理ファイル、エントリポイント) | - | uv で構築 |
| ☑ | `Utterance` / `UtteranceTimeline` の実装 | 共通 | `tts_samplerate` 追加済 |
| ☑ | `AppError` 階層と severity の定義 | 共通 | |
| ☑ | `AudioCaptureBackend` I/F + `SoundcardCaptureBackend` | 入力 | デバイス選択+リサンプル |
| ☑ | `DeviceValidator` (入力=出力チェック) | 入力 | 起動時バリデーション |
| ☑ | `VadBackend` I/F + `SileroVadBackend` | VAD | 発話区切り |
| ☑ | `AsrBackend` I/F + `FasterWhisperAsrBackend` | ASR | task=transcribe |
| ☑ | `TranslatorBackend` I/F + `Nllb200TranslatorBackend` | 翻訳 | 別ステージ必須 |
| ☑ | `TtsBackend` I/F + `SapiTtsBackend` (pyttsx3) | TTS | WAV経由でPCM取得 |
| ☑ | `AudioOutputBackend` I/F + `SoundcardOutputBackend` | 出力 | 出力デバイス指定 |
| ☑ | `PipelineCoordinator` 実装 | 制御 | **3スレッド構成(B+案)** に進化 |
| ☑ | `ConfigStore` (YAML 読書き) | 横断 | |
| ☑ | `Logger` (画面+jsonl、ON/OFF) | 横断 | |
| ☑ | `ErrorHandler` (4分類振り分け) | 横断 | |
| ☑ | `MainWindow` / `SettingsPanel` / `ControlPanel` (customtkinter) | GUI | 最小機能 + モデルステータスラベル |
| ☑ | `BackendRegistry` (バックエンド登録/列挙) | GUI | `register_default_backends` 提供 |
| ☑ | `AppController`(GUI仲介+非同期Loader) | 制御 | B+案で追加 |
| ☑ | `cache_check` + `ModelStatus` | 横断 | 起動時に各モデルのキャッシュ有無を判定し UI 表示 |
| ☑ | レイテンシ表示パネル | GUI | timeline 集計、直近10件平均 |
| ☑ | small テスト整備(pytest 想定) | テスト | バックエンド毎にモック単位 |
| ☑ | 録音WAV を使ったパイプラインE2Eテスト | テスト | デバイス非依存で再現可能(`WavReplayCapture`) |

---

## Phase 2: レイヤ抽象化の実用化

| 状態 | タスク | 備考 |
|---|---|---|
| ☐ | 各レイヤに **第2のバックエンド** を追加(例: ASR を whisper.cpp、TTS を VOICEVOX 等) | 切替の実証 |
| ☐ | GUI からの動的切替を稼働中でも反映 | 現状は次回開始時反映 |
| ☐ | レイテンシ比較ビュー(直近N発話の平均) | バックエンド評価用 |
| ☐ | ErrorCatalog の本格整備 | ライブラリ別エラー一覧 |
| ☐ | モデル DL 進捗の UI 表示(huggingface_hub フック) | 現状は "Loading..." のみ |

---

## Phase 3: ローカルLLM導入

| 状態 | タスク | 備考 |
|---|---|---|
| ☐ | `TranslatorBackend` の Ollama 実装(例: Qwen) | NLLB-200 との比較 |
| ☐ | 翻訳テキストのトーン調整(LLMでリライト) | TTS入力前のオプション処理 |
| ☐ | レイテンシとのトレードオフ評価 | |

---

## Phase 4: マルチOS対応

| 状態 | タスク | 備考 |
|---|---|---|
| ☐ | macOS 動作確認(soundcard + BlackHole 前提) | |
| ☐ | Linux 動作確認(soundcard + PulseAudio/PipeWire monitor) | |
| ☐ | OS別 `AudioCaptureBackend` の追加検討 | pendList の GStreamer 移行検討と連動 |

---

## Phase 5: 将来要件

| 状態 | タスク | 備考 |
|---|---|---|
| ☐ | 出力先を仮想オーディオケーブル経由でDiscord/会議へ | |
| ☐ | プロセス単位の音声取得(Win: PROCESS_LOOPBACK / Mac: ScreenCaptureKit / Linux: sink-input) | |
| ☐ | 重い処理のサーバオフロード(リモート推論バックエンド) | |
| ☐ | AEC オプション(WebRTC) | フィードバック対策の保険 |
| ☐ | 各ステージのさらなる並列化(案C: ASR/翻訳/TTSを別スレッド) | スループットが足りなくなった時 |
