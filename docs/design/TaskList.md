# TaskList (実装タスクリスト)

実装の全体タスク。Phase単位で粒度を揃え、ブランチ単位の詳細は `docs/design/<branch-name>/Plan.md` に切り出す。

凡例: ☐ 未着手 / ◐ 着手中 / ☑ 完了

---

## Phase 0: 設計・骨格

| 状態 | タスク | 備考 |
|---|---|---|
| ☑ | プロジェクト方針定義(CLAUDE.md) | 完了 |
| ☑ | Architecture.md 作成 | 本Phaseで完了 |
| ☑ | UserSinario.md 作成 | 本Phaseで完了 |
| ☑ | TaskList.md 作成 | 本Phaseで完了(=本ファイル) |
| ☐ | ErrorCatalog.md 雛形作成 | エラー方針集約用。実装と並行で埋める |
| ☐ | manual.md 雛形作成 | 内容は実装後に肉付け |

---

## Phase 1: MVP (機能を最低限縦に通す)

「とりあえず英語のYouTubeを日本語音声で聞ける」が達成ライン。

| 状態 | タスク | 担当レイヤ | 備考 |
|---|---|---|---|
| ☐ | プロジェクト初期化(`src/` 配下、依存管理ファイル、エントリポイント) | - | poetry or uv 等で構築 |
| ☐ | `Utterance` / `UtteranceTimeline` の実装 | 共通 | データクラス |
| ☐ | `AppError` 階層と severity の定義 | 共通 | |
| ☐ | `AudioCaptureBackend` I/F + `SoundcardCaptureBackend` | 入力 | デバイス選択+リサンプル |
| ☐ | `DeviceValidator` (入力=出力チェック) | 入力 | 起動時バリデーション |
| ☐ | `VadBackend` I/F + `SileroVadBackend` | VAD | 発話区切り |
| ☐ | `AsrBackend` I/F + `FasterWhisperAsrBackend` | ASR | task=transcribe |
| ☐ | `TranslatorBackend` I/F + `Nllb200TranslatorBackend` | 翻訳 | 別ステージ必須 |
| ☐ | `TtsBackend` I/F + `SapiTtsBackend` (pyttsx3) | TTS | 仮実装 |
| ☐ | `AudioOutputBackend` I/F + `SoundcardOutputBackend` | 出力 | 出力デバイス指定 |
| ☐ | `PipelineCoordinator` 実装 | 制御 | 各ステージ直列接続 |
| ☐ | `ConfigStore` (YAML 読書き) | 横断 | |
| ☐ | `Logger` (画面+jsonl、ON/OFF) | 横断 | |
| ☐ | `ErrorHandler` (4分類振り分け) | 横断 | |
| ☐ | `MainWindow` / `SettingsPanel` / `ControlPanel` (customtkinter) | GUI | 必要最小機能のみ |
| ☐ | `BackendRegistry` (バックエンド登録/列挙) | GUI | プルダウン項目供給 |
| ☐ | レイテンシ表示パネル | GUI | timeline 集計 |
| ☐ | small テスト整備(pytest 想定) | テスト | バックエンド毎にモック単位 |
| ☐ | 録音WAV を使ったパイプラインE2Eテスト | テスト | デバイス非依存で再現可能 |

---

## Phase 2: レイヤ抽象化の実用化

| 状態 | タスク | 備考 |
|---|---|---|
| ☐ | 各レイヤに **第2のバックエンド** を追加(例: ASR を whisper.cpp、TTS を VOICEVOX 等) | 切替の実証 |
| ☐ | GUI からの動的切替を稼働中でも反映 | 現状は次回開始時反映 |
| ☐ | レイテンシ比較ビュー(直近N発話の平均) | バックエンド評価用 |
| ☐ | ErrorCatalog の本格整備 | ライブラリ別エラー一覧 |

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
