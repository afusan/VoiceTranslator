# Class (クラス・モジュール詳細)

`Architecture.html` で示したレイヤ構成と 5 スレッド構成を、**クラス/モジュール単位の責務まで落とし込んだ詳細**。
役割の上位ビュー(レイヤ・I/F・スレッド)については [Architecture.html](Architecture.html) を参照。

---

## 1. パイプラインステージ(各レイヤ)

各レイヤは「**抽象インタフェース + 具象実装**」で構成する。
MVPでは各レイヤ1実装のみ提供し、将来差し替え可能な拡張点として残す。

| インタフェース | I/F シグネチャ(R-2/R-3 で primitive 化) | MVP実装 | 備考 |
|---|---|---|---|
| `AudioCaptureBackend` | `start(id)` / `read_chunk(timeout) -> PcmChunk` / `stop()` | `SoundcardCaptureBackend` | Win/Mac/Linux 抽象化 |
| `VadBackend` | `process(chunk) -> list[VadSegment]` / `reset()` | `SileroVadBackend` | `VadSegment(pcm, started_at_monotonic)` を返す |
| `AsrBackend` | `transcribe(pcm, hint) -> (text, lang)` | `FasterWhisperAsrBackend` | `task=transcribe` 固定 |
| `TranslatorBackend` | `translate(src_text, src_lang, tgt_lang) -> str` | `Nllb200TranslatorBackend` | 200言語対応、ローカル動作 |
| `TtsBackend` | `synthesize(text, tgt_lang) -> (pcm, samplerate)` | `SapiTtsBackend` | pyttsx3経由、WAV経由でPCM取得 |
| `AudioOutputBackend` | `start(id)` / `play(pcm, samplerate)` / `stop()` | `SoundcardOutputBackend` | 出力デバイスを別途指定 |

---

## 2. パイプライン制御(5スレッド + 中央レジャ)

| クラス/モジュール | 役割 |
|---|---|
| `PipelineCoordinator` | **Input / ASR / Translator / TTS / Output の5スレッド**を起動・停止し、4本の上限付き `queue.Queue` で連携。各スレッドは `stop_event` で停止指示を受け、停止時はセンチネル投入で確実に終了する。発話メタの集約は `UtteranceLedger` に委譲。 |
| `UtteranceLedger` | seq_id をキーに、各ステージで生じる timeline / 言語 / テキスト等を集約するスレッドセーフな中央レジャ。`init / mark_time / record / pop / peek / clear` を提供。 |
| `SequenceGenerator` | 発話に一意な連番(seq_id)を発行する atomic counter。各レイヤのログ(app.log / soundsrc.txt / translated.txt / jsonl)に seq_id を載せて対応を取れるようにする。 |
| `PipelineMessage` | ステージ間キューを流れる封筒(`seq_id` + `payload`)。 |
| `RawPayload` / `TranscribedPayload` / `TranslatedPayload` / `SynthesizedPayload` | 各ステージで次段に渡す最小ペイロード。pcm 等の重いデータは「次段が要らなくなった時点」で運ばれない。 |
| `VadSegment` | VAD が確定した1発話分の `(pcm, started_at_monotonic)`。Input スレッドが ledger に正確な t_capture を記録するために運ぶ。 |

### スレッド/キュー構成

| スレッド | 入力 | 出力 | 主な処理 |
|---|---|---|---|
| Input | (capture) | `captured_queue`(maxsize=5) | `capture.read_chunk` → `vad.process` で VadSegment を取り出し、seq_id を発行して `RawPayload` を流す。`t_capture` / `t_vad_end` をレジャに記録。 |
| ASR | `captured_queue` | `recognized_queue`(maxsize=10) | `asr.transcribe(pcm, hint)` → `(text, lang)`。レジャに `src_text / src_lang / t_asr` を記録し、TextLogger に `write_src(seq_id, text, lang)`。pcm は次段に運ばれない(=ASR後に自然解放)。 |
| Translator | `recognized_queue` | `translated_queue`(maxsize=10) | `translator.translate(src_text, src_lang, tgt_lang)` → str。レジャに `tgt_text / tgt_lang / t_translate` を記録、TextLogger に `write_tgt(seq_id, text, lang)`。空翻訳はレジャを pop して打ち切り。 |
| TTS | `translated_queue` | `synthesized_queue`(maxsize=5) | `tts.synthesize(text, tgt_lang)` → `(pcm, samplerate)`。レジャに `t_tts` を記録。 |
| Output | `synthesized_queue` | (output) | `output.play(pcm, samplerate)` → `t_playback` 記録 → `ledger.pop(seq_id)` で record を取り出し `on_utterance_done(record)` 通知。 |

- **キュー上限**: テキスト系(recognized_queue/translated_queue)は 10、音声系(captured_queue/synthesized_queue)は 5。コンストラクタ引数で変更可。
- **あふれ時**: 最古の要素を捨てて新しいものを優先(リアルタイム性確保)。捨てた発話はレジャから即 pop されてリーク防止。テキストは ASR / Translator 段で既に書かれているため失われない。`on_dropped(seq_ids, stage_name)` で UI に通知。
- **エラー**: 各スレッド内で例外を捕捉し `ErrorHandler` に委譲。FATAL なら `stop_event` を立てて全スレッド停止。SKIP/RECOVERABLE は当該 seq_id をレジャから pop して継続。
- **停止シーケンス**: `stop_event` セット → Input スレッド終了 → 各処理スレッドにセンチネル投入 → 上流から順に join。
- **再 start**: 全キュー drain + `ledger.clear()` を実施し、前回の残骸を引きずらない。

### `UtteranceLedger` のレコード形式

```
{
  "seq_id": 42,
  "timeline": {"t_capture": 1234.5, "t_vad_end": 1234.6, "t_asr": 1234.9, ...},
  "src_text": "hello",
  "src_lang": "en",
  "tgt_text": "こんにちは",
  "tgt_lang": "ja",
}
```

- すべてのアクセスは内部 `threading.Lock` で保護(`mark_time` / `record` / `pop` 等)。
- `mark_time` / `record` は未登録 seq_id に対して自動 `init` する(取りこぼし防止)。
- `pop` は未登録 seq_id に対して空 dict を返す(`KeyError` しない)。
- メモリリーク防止のため、最終段(Output 完了)で必ず `pop` し、ドロップ時も `pop` する。

---

## 3. アプリ制御層

| クラス | 役割 |
|---|---|
| `AppController` | GUI と内部モジュールの仲介。設定アクセス・デバイス列挙・**Loader スレッド経由の非同期起動 (`start_pipeline_async`)** ・モデル状態の管理を一括で提供する。`UtteranceLedger` と `SequenceGenerator` を生成して `PipelineCoordinator` に渡す。 |
| `BackendRegistry` | レイヤ別バックエンドの登録/列挙/生成。GUIのプルダウン項目供給に使う。`register_default_backends(registry)` でMVP標準を一括登録。 |

### `AppController` の主要メソッド

| メソッド | 役割 |
|---|---|
| `load_models()` | 全レイヤのバックエンド実体を生成し `_backends` にキャッシュする(冪等。既ロードのレイヤは触らない)。各レイヤごとに `status = LOADING → LOADED` を通知。 |
| `load_models_async(on_done, on_failed)` | `load_models()` をバックグラウンドスレッドで実行(GUI 起動時に呼ぶ)。 |
| `is_loaded` | 全レイヤのバックエンドがメモリ常駐済みかを返すプロパティ。 |
| `start_pipeline_async(on_started, on_failed)` | DeviceValidator 同期チェック → Loader スレッドで「未ロードがあれば `load_models()`」+ Coordinator 起動 → 完了/失敗を UI コールバックで通知。ロード済みなら起動だけ。 |
| `start_pipeline()` | 同期版(テスト/スクリプト用)。GUI からは async 版を使う。 |
| `stop_pipeline()` | Coordinator のみ停止(バックエンド実体は `_backends` に常駐継続)。次回 Start でロード不要。 |
| `list_capture_sources()` / `list_output_devices()` | 設定中のバックエンドを使ってデバイス列挙(GUIプルダウン供給)。 |
| `get_setting()` / `set_setting()` / `save_settings()` / `load_settings()` | ConfigStore のラッパ。`set_setting("backends", layer, name)` は該当レイヤのキャッシュを破棄して再ロードを自動発火する。 |
| `set_callbacks(on_utterance_done, on_fatal, on_warn, on_status_change)` | GUI 側の更新ハンドラを登録する。`on_utterance_done(record: dict)` / `on_fatal(message, *, exc, stage, seq_id, suppressed)` / `on_warn(message, *, exc, stage, seq_id, suppressed)`。`suppressed` は抑制された件数(0 が通常)。使わない側は `**kwargs` で吸収可。 |
| `get_model_status(layer)` / `get_all_model_statuses()` | 各レイヤのモデル状態(`ModelStatus`)を取得。 |
| `_handle_utterance_done(record)` | Coordinator から呼ばれる(Output スレッド)。`TranslationLogger.write_record(record)` で jsonl 追記後、`on_utterance_done` を呼ぶ。 |
| `_handle_dropped(seq_ids, stage)` | Coordinator から呼ばれる。テキストは各段で既に書かれているのでログのみ。 |

### ロード/起動/停止 のライフサイクル

```
[起動]
  MainWindow → AppController.load_models_async()
              └→ Loader スレッド: 各レイヤ生成 (LOADING → LOADED)
[全レイヤ LOADED]
  ControlPanel: ボタン "▶ 開始" 有効化
[Start クリック]
  AppController.start_pipeline_async()
              └→ Loader スレッド: load_models()(no-op) → _start_coord()
[Stop クリック]
  AppController.stop_pipeline()
              └→ Coordinator.stop() のみ。_backends は残置
[バックエンド設定変更]
  AppController.set_setting("backends", layer, new_name)
              └→ _backends.pop(layer) → 別スレッドで _safe_load_layer(layer)
                                       └→ LOADING → LOADED
```

### モデル状態 (`ModelStatus`)

| 値 | 表示 | 色 | 意味 |
|---|---|---|---|
| `INIT` | "Init" | gray | 初期状態。まだロード処理を起動していない(アプリ起動直後やバックエンド差替直後)。 |
| `NOT_DOWNLOADED` | "Not Downloaded" | red | ロード試行に失敗(キャッシュ無 + DL失敗 等)。 |
| `LOADING` | "Loading..." | amber | DL中 or メモリへロード中。 |
| `LOADED` | "Loaded" | green | メモリ常駐済み(即使用可)。 |

- 初期表示はキャッシュ有無に関係なく **全レイヤ INIT** に統一する(キャッシュ有→ LOADED と出すと、その直後に自動ロードが走って `Loaded→Loading→Loaded` の不自然な遷移になるため)。
- 通常の遷移: `INIT → LOADING → LOADED`。失敗時のみ `→ NOT_DOWNLOADED`。バックエンド名を変更すると当該レイヤだけ `LOADED → INIT → LOADING → LOADED`。
- ControlPanel は `INIT` / `LOADING` のどちらか1つでも残っていれば「モデル準備中…」として開始ボタンを無効化する。

---

## 4. 横断機能(共通)

| クラス/モジュール | 役割 |
|---|---|
| `ConfigStore` | 設定値(選択中のバックエンド名、デバイス、言語ペア、ログ出力先 等)の永続化(YAML)と読込。 |
| `Logger`(`setup_app_logger`) | stdout + `app.log` への汎用アプリログ初期化。 |
| `TranslationLogger` | 翻訳1件 = jsonl 1行 として履歴ファイルに追記。R-3 で `write_record(record: dict)` に変更(ledger の pop 結果を直接書く)。ON/OFF 切替可。機械処理向け。 |
| `TextLogger` | R-3 で `write_src(seq_id, text, lang)` / `write_tgt(seq_id, text, lang)` に分離。各ステージから直接呼ぶ粒度。書式 `[YYYY-MM-DD HH:MM:SS] #SEQ [lang] text`。src/tgt 個別 ON/OFF。 |
| `ErrorHandler` | 例外を `AppError` 階層で分類し、致命=ダイアログ/回復=リトライ/スキップ/警告 のいずれかに振り分ける。`handle(exc, *, stage, seq_id)` で context を受け、ログ整形(`seq=N stage=X [SEVERITY] message (caused by ...)`)とコールバック通知に反映。`NotificationThrottle` を注入すると同 (stage, 例外型) の連発を時間窓で抑制可能(callback のみ。ログは全件残す)。詳細は `ErrorCatalog.md` を参照。 |
| `NotificationThrottle` | UI 通知の集約・抑制(キー別の時間窓 rate limit)。キーは `(stage, 例外クラス名)`。抑制された件数は次回 allow 時に `suppressed=N` として callback に渡る。スレッドセーフ。 |
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
| `MainWindow` | アプリのルートウィンドウ。SettingsPanel と ControlPanel を内包する(customtkinter)。**起動時に `AppController.load_models_async()` を呼んでモデルを先行ロード**。閉じる時にパイプライン停止を保証。 |
| `SettingsPanel` | レイヤ別実装の選択 / src/tgt 言語 / 入出力デバイス選択 / ログ出力先指定 / 設定保存・読込 + **レイヤ別モデルステータスラベル(色付き)**。レイヤ実装グループとデバイス選択グループの間に視覚的な区切り線を入れる。 |
| `ControlPanel` | 動作開始/停止トグル、レイヤ別ステータスを集約観測して "全 LOADED" でないと「▶ 開始」を有効化しない。最新翻訳テキスト履歴(`#seq` 付き、クリアボタンあり)、直近平均レイテンシ表示。**警告は UI には出さず、致命的エラーのみ履歴+「停止中(エラー)」表示**(警告も app.log には残る)。`on_utterance_done(record: dict)` の record から `timeline.t_capture` / `t_playback` を見てレイテンシ算出。 |

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

---

## 8. R-2 / R-3 リファクタの変更点(履歴)

| 項目 | 変更前 | 変更後 |
|---|---|---|
| 内部標準データ型 | `Utterance` (全フィールド一体) | retired。ステージ別 payload + `UtteranceLedger` に分離 |
| バックエンドI/F | Utterance を受け渡し | プリミティブ((pcm, hint) / (src_text, src_lang, tgt_lang) / (text, lang) / (pcm, sr)) |
| パイプライン | Input / Process / Output の 3 スレッド | Input / ASR / Translator / TTS / Output の 5 スレッド |
| キュー | q1 / q2(2本) | captured_queue / recognized_queue / translated_queue / synthesized_queue(4本) |
| ログ間対応 | 取れない | seq_id(SequenceGenerator)で対応 |
| TextLogger | `write(utt)` | `write_src(seq_id, text, lang)` / `write_tgt(seq_id, text, lang)` |
| TranslationLogger | `write(utt)` | `write_record(record: dict)`(ledger.pop の dict をそのまま) |
| on_utterance_done | `(utt: Utterance)` | `(record: dict)` |
| on_dropped | `(items: list[Utterance], stage)` | `(seq_ids: list[int], stage)` |
