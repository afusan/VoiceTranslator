# Class (クラス・モジュール詳細)

`Architecture.html` で示したレイヤ構成と 5 スレッド構成を、**クラス/モジュール単位の責務まで落とし込んだ詳細**。
役割の上位ビュー(レイヤ・I/F・スレッド)については [Architecture.html](Architecture.html) を参照。

---

## 1. パイプラインステージ(各レイヤ)

各レイヤは「**抽象インタフェース + 具象実装**」で構成する。
MVPでは各レイヤ1実装のみ提供し、将来差し替え可能な拡張点として残す。

| インタフェース | I/F シグネチャ(R-2/R-3 で primitive 化) | MVP実装 | 備考 |
|---|---|---|---|
| `AudioCaptureBackend` | `start(id)` / `read_chunk(timeout) -> PcmChunk` / `stop()` / `capture_kind() -> CaptureKind`(classmethod) | `SoundcardCaptureBackend` / `ProcTapCaptureBackend` | Win/Mac/Linux 抽象化。`capture_kind` で「デバイス単位」(`DEVICE`、既定)/「プロセス単位」(`PROCESS`)を宣言。SettingsPanel が `CaptureKind` 主体のプルダウン表記に使う(段階 1)。`ProcTapCaptureBackend` は段階 2 で proc-tap(WASAPI Process Loopback)を組み込み、source_id を PID 文字列として受ける(48kHz/2ch → 16kHz/mono への変換を内部で実装)。段階 3 で `list_sources()` を `process_enumerator` 経由の本実装に切替(空リスト仮実装は廃止)、UI 側は `SettingsPanel` の「プロセス選択…」ボタン → `ProcessSelectDialog` で PID を選ぶ。 |
| `VadBackend` | `process(chunk) -> list[VadSegment]` / `reset()` | `SileroVadBackend` / `WebRtcVadBackend` / `PyannoteVadBackend` / `PvcobraVadBackend` | `VadSegment(pcm, started_at_monotonic)` を返す。Silero 実装は `max_speech_sec` で 1 発話の最大長を制限し、超過時は強制区切り(継続発話扱いで再開)。Phase F1 で WebRTC/pyannote/Picovoice Cobra を追加(依存は optional extra `vad-extra`)。 |
| `AsrBackend` | `transcribe(pcm, hint) -> (text, lang)` + `supported_input_languages()` / `supports_auto_detect()` | `FasterWhisperAsrBackend` / `OpenAiWhisperAsrBackend` / `OpenAiWhisperApiAsrBackend` / `GoogleSttAsrBackend` / `DeepgramAsrBackend` | `task=transcribe` 固定。対応言語は backend ごとに宣言(Whisper 系は `common/whisper_languages.py` の 99 言語を共有)。クラウド backend は credential_spec / verify_credentials を実装し、サービス上限(OpenAI 25MB/req など)はそれぞれの backend が `transcribe()` 内で明示エラーにする。Google STT は `supports_auto_detect=False`(detect_language は別 API、未対応)。Deepgram は prerecorded 同期 API を使い、真のストリーミングは未対応。 |
| `TranslatorBackend` | `translate(src_text, src_lang, tgt_lang) -> str` + `supported_target_languages()` / `supported_source_languages()` | `Nllb200TranslatorBackend` / `DeepLTranslatorBackend` / `OpenAiGptTranslatorBackend` / `AnthropicClaudeTranslatorBackend` | 対応言語は ISO 639-1 で宣言(クラスメソッド、未ロードで問い合わせ可)。対称な backend なら source は default(target と同じ)、非対称ならオーバーライド。NLLB はローカル、DeepL は API、GPT / Claude は LLM 翻訳(temperature=0.2、system + user 構造)。 |
| `TtsBackend` | `synthesize(text, tgt_lang) -> (pcm, samplerate)` + `supported_output_languages()` | `SapiTtsBackend` / `PiperTtsBackend` / `ElevenLabsTtsBackend` / `OpenAiTtsBackend` / `GoogleCloudTtsBackend` | 対応読み上げ言語は classmethod で宣言(SAPI は `["ja", "en"]` 保守的)。SAPI=Windows 同梱、Piper=ONNX 軽量ローカル(マルチ OS、voice モデルは HF DL)、ElevenLabs=API key + プリメイド voice(クローニングは pendList)、OpenAI TTS=API key + 6 voice、Google Cloud TTS=サービスアカウント JSON。UI 側で「Translator 出力言語が TTS で読めない」なら警告バナー(ユーザ選択は変更しない)。 |
| `AudioOutputBackend` | `start(id)` / `play(pcm, samplerate)` / `stop()` | `SoundcardOutputBackend` | 出力デバイスを別途指定 |

---

## 2. パイプライン制御(5スレッド + 中央レジャ)

| クラス/モジュール | 役割 |
|---|---|
| `PipelineCoordinator` | **Input / ASR / Translator / TTS / Output の5スレッド**を起動・停止し、4本の上限付き `queue.Queue` で連携。各スレッドは `stop_event` で停止指示を受け、停止時はセンチネル投入で確実に終了する。発話メタの集約は `UtteranceLedger` に委譲。**出力モード**(`audio` / `text_only`)を持ち、`text_only` のときは TTS/Output スレッドを起動せず、Translator 完了で `on_text_ready` 発火 + `ledger.pop()` で即解放する。出力モードは `AppController.output_mode`(= `backends.tts` から派生)に従い、独立した ConfigStore キーは持たない。 |
| `UtteranceLedger` | seq_id をキーに、各ステージで生じる timeline / 言語 / テキスト等を集約するスレッドセーフな中央レジャ。`init / mark_time / record / pop / peek / clear` を提供。 |
| `SequenceGenerator` | 発話に一意な連番(seq_id)を発行する atomic counter。各レイヤのログ(app.log / soundsrc.txt / translated.txt / jsonl)に seq_id を載せて対応を取れるようにする。 |
| `PipelineMessage` | ステージ間キューを流れる封筒(`seq_id` + `payload`)。 |
| `RawPayload` / `TranscribedPayload` / `TranslatedPayload` / `SynthesizedPayload` | 各ステージで次段に渡す最小ペイロード。pcm 等の重いデータは「次段が要らなくなった時点」で運ばれない。 |
| `VadSegment` | VAD が確定した1発話分の `(pcm, started_at_monotonic)`。Input スレッドが ledger に正確な t_capture を記録するために運ぶ。 |

### スレッド/キュー構成

| スレッド | 入力 | 出力 | 主な処理 |
|---|---|---|---|
| Input | (capture) | `captured_queue`(**バイト基準** ByteBoundedQueue) | `capture.read_chunk` → `vad.process` で VadSegment を取り出し、seq_id を発行して `RawPayload` を流す。`t_capture` / `t_vad_end` をレジャに記録。 |
| ASR | `captured_queue` | `recognized_queue`(**件数基準** queue.Queue) | キュー取出後 `t_asr_start` 記録 → `asr.transcribe(pcm, hint)` → `(text, lang)`。レジャに `src_text / src_lang / t_asr` を記録し、TextLogger に `write_src(seq_id, text, lang)`。pcm は次段に運ばれない(=ASR後に自然解放)。 |
| Translator | `recognized_queue` | `translated_queue`(**件数基準** queue.Queue) | キュー取出後 `t_translate_start` 記録 → `translator.translate(src_text, src_lang, tgt_lang)` → str。レジャに `tgt_text / tgt_lang / t_translate` を記録、TextLogger に `write_tgt(seq_id, text, lang)`。空翻訳はレジャを pop して打ち切り。 |
| TTS | `translated_queue` | `synthesized_queue`(**バイト基準** ByteBoundedQueue) | キュー取出後 `t_tts_start` 記録 → `tts.synthesize(text, tgt_lang)` → `(pcm, samplerate)`。レジャに `t_tts` を記録。**TTS 完了直後に `on_text_ready(ledger.peek(seq_id))` を呼び、UI 履歴を音より前に出す前倒し通知を行う**(レジャは pop せずスナップショットのみ)。 |
| Output | `synthesized_queue` | (output) | キュー取出後 `t_playback_start` 記録 → `output.play(pcm, samplerate)` → `t_playback` 記録 → `ledger.pop(seq_id)` で record を取り出し `on_utterance_done(record)` 通知。 |

- **出力モード**(2026-06-05): 出力モードは独立キーで持たず、`backends.tts` から派生する。
  - `backends.tts = "none"`(UI 表記「(なし)」) → **text_only**: Input / ASR / Translator の 3 スレッドのみ起動。Translator 完了で `on_text_ready` を発火し、ledger を `pop` してバッファ即解放。`translated_queue` / `synthesized_queue` には何も流れず、TTS / Output レイヤの backend ロードもスキップ。`on_utterance_done` は呼ばない(Output が無いため)。jsonl / processtime / レイヤ別 処理時間バッファへの記録は `AppController._handle_text_ready` 側で兼ねる。
  - それ以外(SAPI / Piper / ElevenLabs ...) → **audio**: 上記 5 スレッド構成で動く(従来動作)。
- **キュー上限**: `config.yaml` の `pipeline` セクションで設定可能。
  - PCM 系 (`captured_queue` / `synthesized_queue`): **合計バイト数** で制限(`*_queue_max_bytes`、既定 500KB)。
  - テキスト系 (`recognized_queue` / `translated_queue`): **発話件数** で制限(`*_queue_size`、既定 10)。
- **PCM 系のあふれ時(`ByteBoundedQueue`)**: 「設定値を超えるまでは積み、超えたら先頭から退避」方針。`push_evicting` で必ず新規 item は保持し、合計が上限を超えていれば古いものから退避する(2件以上残っている場合のみ)。1 件の item が単独で上限を超える場合はその item を残す → **運用上、設定値を少し超える前提**。
- **テキスト系のあふれ時(`queue.Queue`)**: `maxsize` 到達時に古いものから捨てて新規 item を入れる(従来通り)。
- **共通**: 捨てた発話はレジャから即 pop されてリーク防止。テキストは ASR / Translator 段で既に書かれているため失われない。`on_dropped(seq_ids, stage_name)` で UI に通知。
- **エラー**: 各スレッド内で例外を捕捉し `ErrorHandler` に委譲。FATAL なら `stop_event` を立てて全スレッド停止。SKIP/RECOVERABLE は当該 seq_id をレジャから pop して継続。
- **停止シーケンス**: `stop_event` セット → Input スレッド終了 → 各処理スレッドにセンチネル投入 → 上流から順に join。
- **再 start**: 全キュー drain + `ledger.clear()` を実施し、前回の残骸を引きずらない。

### `UtteranceLedger` のレコード形式

```
{
  "seq_id": 42,
  "timeline": {
    "t_capture": 1234.5, "t_vad_end": 1234.6,
    "t_asr_start": 1234.65, "t_asr": 1234.9,
    "t_translate_start": 1234.95, "t_translate": 1235.1,
    "t_tts_start": 1235.15, "t_tts": 1235.3,
    "t_playback_start": 1235.32, "t_playback": 1235.5
  },
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
| `restart_pipeline_async(on_restarted, on_failed)` | `vt_restart` スレッドで `stop_pipeline()` → `start_pipeline()`(同期版)を直列実行する。P4 で動作中デバイス変更時に SettingsPanel から呼ばれる。動作中でない場合は no-op(`on_restarted` を即時呼ぶ)。多重起動は `on_failed("既に再開中です")` で拒否。callback は `vt_restart` スレッド上で呼ばれるため、UI 側は `widget.after(0, ...)` で marshalling すること。 |
| `list_capture_sources()` / `list_output_devices()` | 設定中のバックエンドを使ってデバイス列挙(GUIプルダウン供給)。 |
| `get_setting()` / `set_setting()` / `save_settings()` / `load_settings()` | ConfigStore のラッパ。`set_setting("backends", layer, name)` は該当レイヤのキャッシュを破棄して再ロードを自動発火する。`save_settings()` / `load_settings()` は段階 3 / A-7 で「PROCESS kind の capture backend が選ばれているとき `devices.input` を空文字に正規化する」フックを内包(`_strip_volatile_inputs_before_save` / `_normalize_volatile_inputs_after_load`)。これで PID を永続化せず、再起動時にも空扱いに揃える。 |
| `set_callbacks(on_utterance_done, on_text_ready, on_fatal, on_warn, on_status_change)` | GUI 側の更新ハンドラを登録する(後方互換の single callback)。`on_text_ready` は TTS 完了直後に呼ばれる「履歴前倒し通知」用(レジャのスナップショットを受ける)。`output_mode=text_only` ではここが最終通知になる(`on_utterance_done` は呼ばれない)。 |
| `output_mode` | プロパティ。`backends.tts` を読み、`"none"` / 空文字 / 未設定なら `"text_only"`、それ以外なら `"audio"` を返す。独立した `pipeline.output_mode` キーは持たない。 |
| `TTS_NONE` | 定数 `"none"`。TTS=(なし) を表す ConfigStore 上の内部値。BackendRegistry にこの名前は登録しない。 |
| `_active_layers()` | 現在の出力モードでロード対象のレイヤ一覧。`text_only` では TTS / Output を除外する。 |
| `add_status_listener(callback) -> Subscription` | UI 側からの multi-listener 購読(R2-6 / Phase A2)。解除は `Subscription.unsubscribe()`。 |
| `get_model_status(layer)` / `get_all_model_statuses()` | 各レイヤのモデル状態を取得。内部で `backends[layer].get_status()` に委譲(Phase A2 で `_model_status` dict を廃止、状態の真実は backend 側)。未ロード layer は `INIT`。 |
| `load_model_layer(layer)` | 単一レイヤだけをロードする(冪等)。Phase B の手動 Load ボタンの入口。 |
| `load_auto_load_layers_async(on_done, on_failed)` | `auto_load=True` のレイヤだけを Loader スレッドで順次ロードする(Phase B 起動シーケンス)。 |
| `get_recent_durations(layer) -> list[float]` | レイヤ別の直近 5 件処理時間(ms、古い→新しい順)。`_handle_utterance_done` で push される。Phase C の詳細ダイアログで使う。 |
| `get_status_summary() -> str` | 全レイヤの状態 + 直近エラーを 1 つのテキストにまとめる(Phase C3)。ControlPanel のステータステキストボックスが表示する。 |
| `get_credential_spec(layer, name) -> list[CredentialField]` | 指定 backend が要求する認証フィールド一覧(Phase E-2)。`CredentialDialog` が動的描画に使う。 |
| `get_capture_kind(backend_name) -> CaptureKind` | 指定 CAPTURE backend の取得単位(`DEVICE` / `PROCESS`)を返す。未登録 / 例外時は `DEVICE`(安全側)。backend クラスの `capture_kind()` を呼ぶだけでインスタンス化しない。 |
| `verify_and_save_credentials(layer, name, values) -> VerifyResult` | backend の `verify_credentials` を呼び、成功なら `CredentialsStore` 保存 + `credentials.verified.<backend>=True` 永続化。 |
| `is_backend_verified(backend_name) -> bool` | ConfigStore の `credentials.verified.<backend>` を返す。Start gate で参照。 |
| `invalidate_verification(backend_name)` | サブスク切れ / 401 等を観測したとき呼ぶ。`verified=False` に戻し、次回 Start を gate する。 |
| `_handle_text_ready(record)` | Coordinator から呼ばれる(TTS スレッド)。レジャのスナップショットを `on_text_ready` にそのまま中継し、UI 履歴を音より前に出す。 |
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
| `MISSING_CREDENTIALS` | "Missing Credentials" | red | クラウド backend で認証情報が未設定(Phase D で利用)。 |
| `NOT_DOWNLOADED` | "Not Downloaded" | red | ロード試行に失敗(キャッシュ無 + DL失敗 等)。 |
| `DOWNLOADING` | "Downloading..." | amber | モデル DL 中(R-3 / R2-1)。`ModelInfo.download_size_gb` をステータステキストボックスに併記。 |
| `LOADING` | "Loading..." | amber | メモリへロード中。 |
| `LOADED` | "Loaded" | green | メモリ常駐済み(即使用可)。 |

- 初期表示はキャッシュ有無に関係なく **全レイヤ INIT** に統一する(キャッシュ有→ LOADED と出すと、その直後に自動ロードが走って `Loaded→Loading→Loaded` の不自然な遷移になるため)。
- 通常の遷移: `INIT → (DOWNLOADING) → LOADING → LOADED`。キャッシュ有なら DOWNLOADING はスキップ。失敗時のみ `→ NOT_DOWNLOADED`。バックエンド名を変更すると当該レイヤだけ `LOADED → INIT → ... → LOADED`。
- ControlPanel は `INIT` / `LOADING` のどちらか1つでも残っていれば「モデル準備中…」として開始ボタンを無効化する(Phase B で挙動変更予定)。

### バックエンド基底 (`BackendBase`)

Phase A1 で導入した全 backend 共通の基底ミックスイン(`common/backend_base.py`)。
各レイヤの抽象基底(`AsrBackend`, `VadBackend`, ...)が `class AsrBackend(BackendBase, ABC)` の形で多重継承する。

| メソッド | 役割 |
|---|---|
| `get_status() -> ModelStatus` | 当該 backend の現状態を返す。 |
| `_set_status(status)` | サブクラス内部からの状態更新フック。同値遷移は notify を発火しない。 |
| `subscribe(callback) -> Subscription` | 状態変化購読。解除は `Subscription.unsubscribe()`(R2-6)。 |
| `record_error(exc, *, context=None)` | エラー履歴(リングバッファ 5 件)に積む。 |
| `get_recent_errors() -> list[ErrorRecord]` | 直近エラー履歴(古い→新しい)。 |
| `list_recommended_models() -> list[ModelInfo]` | 推奨モデル一覧(モデル概念のない backend は空)。 |

**設計判断 (R2-1)**: 状態の真実は backend 側にある。`AppController` は購読者として動き、UI に
re-broadcast するだけ。これによりかつての `AppController._model_status` dict を分散化し、
責務肥大化(R2-2)を抑える。`AppController` 側の統合は Phase A2 で実施。

### モデルメタ情報 (`ModelInfo`)

backend の `list_recommended_models()` の戻り値。GUI のドロップダウン項目 + リソース目安表示で参照。

| フィールド | 意味 |
|---|---|
| `name` | backend 内部での識別子(HF repo id 等)。 |
| `display_name` | GUI 表示名。 |
| `ram_gb` / `vram_gb_if_gpu` / `download_size_gb` | 概算リソース。`None` は不明。 |
| `target_proc_ms_per_sec_audio` | 音声 1 秒あたりの目安処理時間(ms)。翻訳のように非適用なら `None`。 |

### `BackendCapabilities` クラウド系フィールド(Phase A1 で追加)

| フィールド | 意味 |
|---|---|
| `is_cloud` | クラウド(外部 API)backend か。GUI で ☁ バッジ表示(Phase C)。 |
| `requires_credentials` | API key 等の認証情報が必要か。Phase D で利用。 |
| `service_name` | 同意ダイアログ等での表示名(例: "OpenAI Whisper API")。 |
| `terms_url` | 利用規約 URL。同意ダイアログで参照リンクを出す。 |

backend 実装者は **エラーを適切な `AppError` サブクラスに分けて包む**こと(R2-5)。
詳細は [`errors.py`](../../src/voice_translator/common/errors.py) の docstring を参照。

---

## 4. 横断機能(共通)

| クラス/モジュール | 役割 |
|---|---|
| `ConfigStore` | 設定値(選択中のバックエンド名、デバイス、言語ペア、ログ出力先 等)の永続化(YAML)と読込。 |
| `Logger`(`setup_app_logger`) | stdout + `app.log` への汎用アプリログ初期化。 |
| `TranslationLogger` | 翻訳1件 = jsonl 1行 として履歴ファイルに追記。R-3 で `write_record(record: dict)` に変更(ledger の pop 結果を直接書く)。ON/OFF 切替可。機械処理向け。 |
| `TextLogger` | R-3 で `write_src(seq_id, text, lang)` / `write_tgt(seq_id, text, lang)` に分離。各ステージから直接呼ぶ粒度。書式 `[YYYY-MM-DD HH:MM:SS] #SEQ [lang] text`。src/tgt 個別 ON/OFF。 |
| `ProcessTimeLogger` | 1発話=CSV1行で、各レイヤのキュー待ち/純処理時間/合計レイテンシを `processtime.csv` に追記。`derive_stage_durations(record)` で `UtteranceLedger.pop()` の timeline から各段の duration を計算する純関数を分離して提供(テスト容易)。`enabled=False` で完全無効化(既定 OFF)。プロファイル/最適化向け。 |
| `ErrorHandler` | 例外を `AppError` 階層で分類し、致命=ダイアログ/回復=リトライ/スキップ/警告 のいずれかに振り分ける。`handle(exc, *, stage, seq_id)` で context を受け、ログ整形(`seq=N stage=X [SEVERITY] message (caused by ...)`)とコールバック通知に反映。`NotificationThrottle` を注入すると同 (stage, 例外型) の連発を時間窓で抑制可能(callback のみ。ログは全件残す)。詳細は `ErrorCatalog.md` を参照。 |
| `NotificationThrottle` | UI 通知の集約・抑制(キー別の時間窓 rate limit)。キーは `(stage, 例外クラス名)`。抑制された件数は次回 allow 時に `suppressed=N` として callback に渡る。スレッドセーフ。 |
| `device` モジュール | `resolve_torch_device(pref)` / `resolve_ctranslate2_device(pref)` / `resolve_ctranslate2_compute_type(device, pref)` を提供。"auto" を実デバイス名に解決(CUDA → MPS → CPU の順)。配布方針(CLAUDE.md「配布方針」)に従い、コードパスは 1 本のまま GPU/CPU を吸収する。 |
| `ByteBoundedQueue` | 合計バイト数で容量を制限する FIFO キュー。`push_evicting(item) -> list` は「新規は必ず保持、超過したら古いものから退避」。`get/get_nowait` は `queue.Queue` 互換。PCM 系のステージ間バッファ(captured/synthesized)で使う。 |
| `StageDumpWriter` | パイプラインのステージ間データ(vad の PCM / asr のテキスト / translate のテキスト / tts の PCM)を `<dump_dir>/<run_id>/seq_NNNN_<stage>.{wav,json}` に書き出すフック。書き込みは内部のワーカスレッドで非同期に行い、本体パイプラインを止めない。`pipeline.dump.enabled=true` のとき `AppController` が生成して `PipelineCoordinator` に注入する。`voice_translator.dev.runner_*` の入力として使う。 |
| `NullStageDumpWriter` | `StageDumpWriter` の no-op 実装(Null Object)。dump 無効時に注入することで、Coordinator 側の分岐を増やさずにオーバーヘッドをほぼゼロにする。 |
| `AppError` (基底例外) | `severity` (FATAL/RECOVERABLE/SKIP/WARN) を持つ。各バックエンドは下位例外をこれに包んで送出する。 |
| `DeviceValidator` | 起動時に「入力デバイス ≠ 出力デバイス」を保証。違反時は FatalError で起動拒否。 |
| `cache_check` (モジュール) | `check_faster_whisper / check_nllb200 / check_silero / check_sapi / check_soundcard` の関数群。`huggingface_hub.try_to_load_from_cache` で軽量にキャッシュ有無を判定。 |
| `capture.process_enumerator` (モジュール) | WASAPI AudioSession を pycaw / psutil で列挙 + 試聴 peak を供給するヘルパー(段階 3)。役割は ProcTap backend と `ProcessSelectDialog` で共有する「音声出力中プロセスの一覧」と「peak 値の継続供給」の提供。**永続 COM ワーカスレッド `_PeakWorker` を 1 つだけ持ち**、全 pycaw 呼び出しはそのスレッド内で実行する(GUI スレッドの STA と pycaw の MTA 要求が競合するため)。公開 API は `enumerate_active_processes()` / `start_audition(pid) -> bool` / `stop_audition()` / `latest_peak() -> float` / `is_auditioning()` / `dispose()`。試聴中はワーカ内部で 5fps poll が peak を取って atomic float に保持、GUI スレッドは `latest_peak()` を atomic 読みするだけ(スレッド境界を毎ティックまたがない)。`enumerate_active_processes` は `AudioSessionState.Active` のみ採用 + PID 単位 dedupe + `psutil.Process(pid).name()` で名前補完(失敗時 `"unknown"`)。pycaw / psutil / comtypes 呼び出しは `_list_active_sessions` / `_resolve_process_name` / `_PeakWorker._run` に隔離、テストでは monkeypatch で完全置換できる。 |

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
| `SettingsPanel` | レイヤ別実装の選択 / src/tgt 言語 / 入出力デバイス選択 / ログ出力先指定 / 設定保存・読込 + **レイヤ別モデルステータスラベル(色付き)**。「バックエンド / デバイス / 翻訳」の 3 セクション独立折り畳み(P1)。各レイヤ行に「設定」ボタンがあり、`LayerSettingsDialog` を開いてレイヤ固有の設定を編集できる。LOADED 状態のとき、device 概念を持つレイヤ(ASR/Translator)は `Loaded (cuda)` のように **実デバイス名を併記**(`AppController.get_layer_device(layer)` 経由)。**入力言語(src)プルダウンは ASR backend ごとの対応言語に動的追従**:backend 切替時に `_refresh_input_language_choices` で選択肢を再構築し、既存設定値が新 backend で非対応なら自動 fallback(auto 対応なら `auto`、非対応なら先頭言語)+ 通知バナーで明示。表示形式は `"en (English)"`(共通言語テーブル `common/languages.py` で変換)。**入力ソース UI は CAPTURE backend の kind に応じて切替**(段階 3):`capture_kind == DEVICE` ならプルダウンで `list_sources()` を表示(P5 と同じ)、`PROCESS` なら「プロセス選択…」ボタンに切替し、押下で `ProcessSelectDialog` を開いて PID を選ぶ。ボタンラベルは現 PID を反映(`PID 1234 ▼`)。**動作中の入出力デバイス変更で自動 restart**(P4):`_on_capture_changed` / `_on_capture_select_clicked` / `_on_output_changed` で `AppController.restart_pipeline_async` を発火、NotificationBanner で「再開中…」を出す。 |
| `ControlPanel` | 動作開始/停止トグル、レイヤ別ステータスを集約観測して "全 LOADED" でないと「▶ 開始」を有効化しない。最新翻訳テキスト履歴(`#seq` 付き、クリアボタンあり)、直近平均レイテンシ表示。**警告は UI には出さず、致命的エラーのみ履歴+「停止中(エラー)」表示**(警告も app.log には残る)。`on_utterance_done(record: dict)` の record から `timeline.t_capture` / `t_playback` を見てレイテンシ算出。**アクセラレータ集約表示**:各レイヤの `device` を集約して「演算: GPU (cuda)」「演算: CPU のみ」を色付きで表示。**PROCESS kind の source 未選択時は Start disable**(段階 3 / A-7):`capture_kind == PROCESS` で `devices.input` が空のとき、Start ボタンを「プロセス未選択」で disable。`MISSING_CREDENTIALS` / `DOWNLOADING` の次に高優先度の分岐として `_sync_ready_state` で判定する。 |
| `LayerSettingsDialog` | 単一レイヤの設定編集ウィンドウ(CTkToplevel)。`layer_settings_schema.LAYER_SETTINGS` のスキーマに従ってラベル + 入力欄を動的に構築し、保存時に `AppController.set_setting` で ConfigStore に書き戻す。バックエンド条件付きフィールド(SAPI rate 等)に対応。 |
| `layer_settings_schema` モジュール | レイヤ別の編集可能な設定項目を `SettingField(keys, label, field_type, default, help_text, applies_when_backend)` の集まりで宣言。新項目はここに追加するだけで GUI に出る(スキーマ駆動)。 |
| `ProcessSelectDialog` | per-process キャプチャ用のプロセス選択ダイアログ(CTkToplevel)。段階 3 で追加。`capture_kind == PROCESS` の入力ソースを選ぶ専用 UI で、`SettingsPanel` の「プロセス選択…」ボタンから呼ばれる。構成: 列挙テーブル(プロセス名 + PID、ラジオ選択) / ↻ 更新 / ▶ 試聴開始・■ 停止トグル / レベルメータ(`CTkProgressBar`、`pycaw.IAudioMeterInformation.GetPeakValue()` を 30fps poll) / OK・Cancel。試聴は本番パイプラインと完全独立(WASAPI Process Loopback を開かない、pycaw メータのみ)。 |
| `ProcessSelectController` | `ProcessSelectDialog` の状態機械を GUI 非依存に切り出したもの。列挙 / 選択 PID / 試聴 ON-OFF / peak decay を保持。`_PeakProvider` Protocol を経由して peak 供給元(本番は `process_enumerator` モジュールの永続ワーカ、テストは fake)を差し替えられる。GUI 不要のロジック単体テストはこのクラスを直接生成して fake provider で検証する。 |

### スレッドセーフ規約

- tkinter ウィジェットはメインスレッドからしか触れない。Coordinator/Loader スレッドからの通知は **`widget.after(0, lambda: ...)`** でメインスレッドに戻して反映する。
- AppController の callback(`on_utterance_done` / `on_text_ready` / `on_fatal` / `on_warn` / `on_status_change`)は呼び出し元スレッドの上で実行されるため、UI 側は必ず `after()` 経由で処理すること。

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
