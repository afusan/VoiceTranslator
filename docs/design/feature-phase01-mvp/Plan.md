# Plan: feature/phase01-mvp

Phase 1 (MVP) の作業計画。
全体タスクは [docs/design/TaskList.md](../TaskList.md) を参照。本ファイルはこのブランチで行う実装作業の分解。

---

## 目的(達成ライン)

**「英語のYouTube/Twitchを日本語音声で聞ける」**
- 入力デバイスのループバックから取得 → VAD → ASR → 翻訳 → TTS → 出力デバイスで再生
- GUI から開始/停止、設定保存ができる
- 入力≠出力 のバリデーション動作

---

## スコープ

### IN(このブランチでやること)
- プロジェクト初期化(uv、`src/` ツリー、エントリポイント)
- 各レイヤの抽象I/F + MVP1実装(soundcard / Silero-VAD / faster-whisper / NLLB-200 / SAPI / soundcard)
- `Utterance` / `UtteranceTimeline` / `AppError` / `ErrorHandler` / `ConfigStore` / `Logger` / `DeviceValidator` / `PipelineCoordinator` / `BackendRegistry`
- 最小GUI(customtkinter): `MainWindow` / `SettingsPanel` / `ControlPanel`
- レイテンシ計測表示
- pytest 環境整備(smallテスト + WAV入力E2E)

### OUT(このブランチではやらない)
- 各レイヤの第2バックエンド追加(Phase 2)
- LLM翻訳(Phase 3)
- マルチOS動作確認(Phase 4)
- per-app取得・AEC・サーバオフロード(Phase 5)

---

## 実装ステップ(粒度: コミット単位を想定)

### ステップ 1: 土台
1. `uv init` でプロジェクト初期化、`pyproject.toml` 整備
2. `src/` ツリー作成(`src/<layer>/` 構成、`__init__.py`)
3. 共通: `Utterance`, `UtteranceTimeline`, `AppError`, `ErrorHandler` の実装
4. 共通: `ConfigStore`(YAML読書き)、`Logger`(stdout + jsonl)

### ステップ 2: パイプライン骨格
5. 各レイヤの抽象I/F(`AudioCaptureBackend` 等)を定義
6. `PipelineCoordinator` の実装(直列接続、start/stop)
7. `BackendRegistry` の実装

### ステップ 3: 入出力 + バリデーション
8. `SoundcardCaptureBackend` 実装(デバイス選択、16k/mono/f32 正規化)
9. `SoundcardOutputBackend` 実装(指定デバイスへ再生)
10. `DeviceValidator` 実装(起動時チェック)

### ステップ 4: VAD/ASR/翻訳/TTS
11. `SileroVadBackend` 実装(発話単位の切り出し)
12. `FasterWhisperAsrBackend` 実装(`task=transcribe`)
13. `Nllb200TranslatorBackend` 実装
14. `SapiTtsBackend` 実装(pyttsx3経由)

### ステップ 5: GUI
15. `MainWindow` 雛形(customtkinter)
16. `SettingsPanel` 実装(バックエンド/デバイス/言語/ログ先 + 保存読込)
17. `ControlPanel` 実装(開始停止、最新翻訳テキスト表示)
18. レイテンシ表示パネル

### ステップ 6: 縦通し + テスト
19. パイプライン全体の縦通し動作確認(WAV入力で再現可能なE2Eテスト整備)
20. smallテストの追補(各バックエンドのモック単位)
21. README とユーザマニュアル(`docs/manual.md`)の肉付け

### ステップ 7(追加): スレッド分割(B+案)とモデルステータスUI
22. `PipelineCoordinator` を Input/Process/Output の 3 スレッド構成に書き換え(上限付きキュー、あふれは最古を捨てる)
23. `AppController` に Loader スレッド経由の `start_pipeline_async()` を追加
24. `ModelStatus`(NOT_DOWNLOADED / LOADING / LOADED, 英語表示固定)と `cache_check` の追加
25. `SettingsPanel` にレイヤ別ステータスラベル(色付き)を表示
26. `ControlPanel` を非同期起動対応にし、ロード中状態を表示

---

## 想定コミット粒度

- 上記の各ステップ単位(またはステップ内の論理的な区切り)で1コミット。
- コミットメッセージは `feat:` プレフィックス。docs だけの場合は `docs:`、テスト追加は `test:`。
- ビルドとテストが通ることをコミット前に確認(CLAUDE.md準拠)。

---

## 完了条件 (Definition of Done)

- [x] uv 環境で `uv sync` → `py -m uv run python -m voice_translator` でGUIが起動する
- [x] 録音WAVを入力源にできるE2Eテストが通る(`pytest` 一発、139件)
- [ ] 入力デバイスをスピーカ(ループバック)、出力デバイスを別物に設定して、英語音声 → 日本語TTS が再生される(実機目視確認)
- [ ] 入力=出力 のバリデーションで起動拒否されることを目視確認(実機目視確認)
- [x] 設定の保存/読込ができる
- [x] レイテンシが画面に表示される
- [x] UI フリーズが起きない(Loader スレッドでモデル初期化を非同期化)
- [x] レイヤ別モデルステータスが UI に表示される(英語: Not Downloaded / Loading... / Loaded)

---

## 既知の留意事項

- faster-whisper の初回モデルDLに時間がかかる。manual に記載予定。
- NLLB-200 のモデルサイズ(数百MB〜1GB)を許容する前提。
- SAPI(pyttsx3)はWindows以外では動かない。MVPはWindows優先のため許容。
- レイテンシ目標値は未確定(計測は実装する)。
- マルチOS対応・per-app取得は本Phaseでは扱わない。

---

## 関連ドキュメント

- アーキテクチャ: [Architecture.html](../Architecture.html)
- クラス詳細: [Class.md](../Class.md)
- ユーザシナリオ: [UserSinario.md](../UserSinario.md)
- 全体タスク: [TaskList.md](../TaskList.md)
- テスト項目: [testPlan.md](testPlan.md)
