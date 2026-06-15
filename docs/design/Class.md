# Class (クラス・モジュール詳細)

`Architecture.html` で示したレイヤ構成とステージ編成(標準 5 ステージ)を、**クラス/モジュール単位の責務まで落とし込んだ詳細**。
役割の上位ビュー(レイヤ・I/F・スレッド)については [Architecture.html](Architecture.html) を参照。

---

## 1. パイプラインステージ(各レイヤ)

各レイヤは「**抽象インタフェース + 具象実装**」で構成し、差し替え可能な拡張点として残す。

全 backend は編成への申告 classmethod(`covers_roles()` / `consumes_payload()` /
`produces_payload()`)を持つ。各レイヤの抽象基底が単体ロールの既定実装を提供するため、
単体 backend は何も書かない。複合 backend(複数ロールを 1 つで担う)だけがオーバーライドする。

| インタフェース | I/F シグネチャ(プリミティブのみ) | 実装 | 備考 |
|---|---|---|---|
| `AudioCaptureBackend` | `start(id)` / `read_chunk(timeout) -> PcmChunk` / `stop()` / `capture_kind() -> CaptureKind`(classmethod) | `SoundcardCaptureBackend` / `ProcTapCaptureBackend` | Win/Mac/Linux 抽象化。`capture_kind` で「デバイス単位」(`DEVICE`、既定)/「プロセス単位」(`PROCESS`)を宣言し、SettingsPanel は `CaptureKind` 主体のプルダウン表記に使う。`ProcTapCaptureBackend` は proc-tap(WASAPI Process Loopback)で PID 単位にキャプチャする(source_id = PID 文字列、48kHz/2ch → 16kHz/mono への変換を内部で実装。`input_gain` で取得 PCM を内部増幅可能 — 対象プロセスの再生音量が小さい場合用、±1.0 クリップ)。プロセス一覧は `process_enumerator` 経由で供給し、UI 側は `SettingsPanel` の「プロセス選択…」ボタン → `ProcessSelectDialog` で PID を選ぶ。 |
| `VadBackend` | `process(chunk) -> list[VadSegment]` / `reset()` | `SileroVadBackend` / `WebRtcVadBackend` / `PyannoteVadBackend` / `PvcobraVadBackend` | `VadSegment(pcm, started_at_monotonic)` を返す。Silero 実装は `max_speech_sec` で 1 発話の最大長を制限し、超過時は強制区切り(継続発話扱いで再開)。WebRTC / pyannote / Picovoice Cobra は optional extra `vad-extra` で導入する。 |
| `AsrBackend` | `transcribe(pcm, hint) -> (text, lang)` + `supported_input_languages()` / `supports_auto_detect()` | `FasterWhisperAsrBackend` / `OpenAiWhisperAsrBackend` / `OpenAiWhisperApiAsrBackend` / `GoogleSttAsrBackend` / `DeepgramAsrBackend` | `task=transcribe` 固定。対応言語は内部標準 ISO 639-3 で宣言(Whisper 系は `common/whisper_languages.py` の 99 言語=639-1 を共有し、境界で 639-3 へ変換)。hint / 戻り値の lang も 639-3。クラウド backend は credential_spec / verify_credentials を実装し、サービス上限(OpenAI 25MB/req など)はそれぞれの backend が `transcribe()` 内で明示エラーにする。Google STT は `supports_auto_detect=False`(detect_language は別 API、未対応)。Deepgram は prerecorded 同期 API を使い、真のストリーミングは未対応。 |
| `TranslatorBackend` | `translate(src_text, src_lang, tgt_lang) -> str` + `supported_target_languages()` / `supported_source_languages()` | `Nllb200TranslatorBackend` / `DeepLTranslatorBackend` / `OpenAiGptTranslatorBackend` / `AnthropicClaudeTranslatorBackend` | 対応言語は内部標準 ISO 639-3 で宣言(クラスメソッド、未ロードで問い合わせ可)。各 backend のベンダ変換表は 639-1 キーのまま据え置き、境界で `common/languages.py` の `iso1_to_iso3`/`iso3_to_iso1` を一段挟む。対称な backend なら source は default(target と同じ)、非対称ならオーバーライド。NLLB はローカル、DeepL は API、GPT / Claude は LLM 翻訳(temperature=0.2、system + user 構造)。 |
| `TtsBackend` | `synthesize(text, tgt_lang) -> (pcm, samplerate)` + `supported_output_languages()` | `SapiTtsBackend` / `PiperTtsBackend` / `MmsTtsBackend` / `ElevenLabsTtsBackend` / `OpenAiTtsBackend` / `GoogleCloudTtsBackend` | 対応読み上げ言語は classmethod で宣言(SAPI は `["ja", "en"]` 保守的)。SAPI=Windows 同梱、Piper=ONNX 軽量ローカル(マルチ OS、voice モデルは HF DL)、MMS=Meta MMS-TTS(VITS、多言語、**言語単位の遅延ロード** + `prefetch_language()`、モデルは CC-BY-NC 非商用)、ElevenLabs=API key + プリメイド voice(クローニングは pendList)、OpenAI TTS=API key + 6 voice、Google Cloud TTS=サービスアカウント JSON。UI 側で「Translator 出力言語が TTS で読めない」なら警告バナー(ユーザ選択は変更しない)。 |
| `AudioOutputBackend` | `start(id)` / `play(pcm, samplerate)` / `stop()` | `SoundcardOutputBackend` | 出力デバイスを別途指定 |
| `AsrTranslatorBackend`(複合) | `transcribe_translate(pcm, hint, tgt_lang) -> (src_text, src_lang, tgt_text, tgt_lang)` + `supported_input_languages()` / `supported_target_languages()` / `supports_auto_detect()` | `FasterWhisperTranslateBackend` / `OpenAiWhisperApiTranslateBackend` / `GptAudioTranslateBackend` | **ASR + Translator の 2 ロールを 1 ステージで担う**(End-to-End 音声翻訳)。ASR レイヤに登録され、選択すると Translator ロールが吸収される(Translator backend はロード・認証 gate・編成の対象外)。Whisper translate 系 2 種(ローカル faster_whisper_translate / クラウド openai_whisper_api_translate)は英語固定・源言語テキストなし(`src_text=""`)。`GptAudioTranslateBackend` は GPT 音声入力(input_audio)で任意の翻訳先 + 原文テキストも取得(STRICT JSON 契約、崩れたら本文全体を訳として縮退)。クラウド 2 種は OpenAI API key(未設定なら同系 backend の保存 key に fallback)。候補比較は `append/compositeBackendCandidates.html`。 |

---

## 2. パイプライン制御(編成表駆動のステージ列 + 中央レジャ)

| クラス/モジュール | 役割 |
|---|---|
| `pipeline_plan` モジュール | **編成表の構築(純関数)**。`build_pipeline_plan(declarations, text_only)` が backend の申告(`RoleDeclaration`)からステージ編成(`PipelinePlan` = `StageSpec` の列 + 吸収マップ)を組み、申告の欠落・covers の非連続・隣接 payload 形式の不整合を `PlanError`(FatalError 系)で起動拒否する。発話 payload が生まれる前の区間(Capture〜VAD)は常に 1 つの入力ステージに融合する。形式変換が必要になった時の差し込み口 `PayloadAdapter`(現在は素通しのみ)もここ。走行中の動的ルーティングは行わない(編成を変える要因は設定だけで、設定は走行中に変わらない)。 |
| `PipelineCoordinator` | **編成表どおりにステージスレッドとキューを組み立てて起動・停止**する。標準構成では Input / ASR / Translator / TTS / Output の 5 ステージ・4 キュー。各ステージは `stop_event` で停止指示を受け、停止時はセンチネル投入で確実に終了する。ステージ共通の流れ(キュー get → ロール処理 → 次段 put / 終端処理 / エラー縮退 / リトライ)は `_worker_loop` に 1 回だけ書かれ、ロール固有の中身(backend 呼び出し・計時・dump・テキストログ)は `_process_<role>` 関数群に分離。発話メタの集約は `UtteranceLedger` に委譲。終端の一般則: 最終ステージが Output なら `on_utterance_done`、でなければ最終ステージ完了で `on_text_ready` + `ledger.pop()`。`plan` プロパティで確定済み編成表を公開。 |
| `UtteranceLedger` | seq_id をキーに、各ステージで生じる timeline / 言語 / テキスト等を集約するスレッドセーフな中央レジャ。`init / mark_time / record / pop / peek / clear` を提供。 |
| `SequenceGenerator` | 発話に一意な連番(seq_id)を発行する atomic counter。各レイヤのログ(app.log / soundsrc.txt / translated.txt / jsonl)に seq_id を載せて対応を取れるようにする。 |
| `PipelineMessage` | ステージ間キューを流れる封筒(`seq_id` + `payload`)。 |
| `PayloadKind` | ステージ間データ形式の識別子(RAW / TRANSCRIBED / TRANSLATED / SYNTHESIZED / NONE)。backend の申告と編成の型整合検証に使う。 |
| `RawPayload` / `TranscribedPayload` / `TranslatedPayload` / `SynthesizedPayload` | 各ステージで次段に渡す最小ペイロード。pcm 等の重いデータは「次段が要らなくなった時点」で運ばれない。 |
| `VadSegment` | VAD が確定した1発話分の `(pcm, started_at_monotonic)`。Input スレッドが ledger に正確な t_capture を記録するために運ぶ。 |

### ステージ/キュー構成(標準編成)

| ステージ | 入力 | 出力 | 主な処理 |
|---|---|---|---|
| Input(Capture+VAD 融合) | (capture) | `captured_queue`(**バイト基準** ByteBoundedQueue) | `capture.read_chunk` → `vad.process` で VadSegment を取り出し、seq_id を発行して `RawPayload` を流す。`t_capture` / `t_vad_end` をレジャに記録。 |
| ASR | `captured_queue` | `recognized_queue`(**件数基準** queue.Queue) | キュー取出後 `t_asr_start` 記録 → `asr.transcribe(pcm, hint)` → `(text, lang)`。レジャに `src_text / src_lang / t_asr` を記録し、TextLogger に `write_src(seq_id, text, lang)`。pcm は次段に運ばれない(=ASR後に自然解放)。 |
| Translator | `recognized_queue` | `translated_queue`(**件数基準** queue.Queue) | キュー取出後 `t_translate_start` 記録 → `translator.translate(src_text, src_lang, tgt_lang)` → str。レジャに `tgt_text / tgt_lang / t_translate` を記録、TextLogger に `write_tgt(seq_id, text, lang)`。空翻訳は破棄(レジャ解放は worker loop 側)。 |
| TTS | `translated_queue` | `synthesized_queue`(**バイト基準** ByteBoundedQueue) | キュー取出後 `t_tts_start` 記録 → `tts.synthesize(text, tgt_lang)` → `(pcm, samplerate)`。レジャに `t_tts` を記録。**TTS 完了直後に `on_text_ready(ledger.peek(seq_id))` を呼び、UI 履歴を音より前に出す前倒し通知を行う**(レジャは pop せずスナップショットのみ)。 |
| Output | `synthesized_queue` | (output) | キュー取出後 `t_playback_start` 記録 → `output.play(pcm, samplerate)` → `t_playback` 記録 → `ledger.pop(seq_id)` で record を取り出し `on_utterance_done(record)` 通知。 |
| ASR+Translator(複合選択時) | `captured_queue` | `translated_queue` | ASR と Translator の 2 段を 1 ステージで担う。`transcribe_translate(pcm, hint, tgt_lang)` の 1 呼び出しで `TranslatedPayload` を産出し、`recognized_queue` は使われない。timeline は入口(`t_asr_start`)と出口(`t_translate`)のみ記録し、内側の境界時刻は欠損(処理時間表示は「-」に縮退)。 |

- **出力モード**: 出力モードは独立キーで持たず、`backends.tts` から派生する。text_only は「TTS / Output が編成表に載らない」縮退(スキップの一般則の一例)。
  - `backends.tts = "none"`(UI 表記「(なし)」) → **text_only**: 編成は Input / ASR / Translator の 3 ステージ。最終ステージ(Translator)完了で `on_text_ready` を発火し、ledger を `pop` してバッファ即解放。`translated_queue` / `synthesized_queue` には何も流れず、TTS / Output レイヤの backend ロードもスキップ。`on_utterance_done` は呼ばない(Output が無いため)。jsonl / processtime / レイヤ別 処理時間バッファへの記録は `AppController._handle_text_ready` 側で兼ねる。
  - それ以外(SAPI / Piper / ElevenLabs ...) → **audio**: 標準 5 ステージ編成で動く。
- **キュー上限**: `config.yaml` の `pipeline` セクションで設定可能。
  - PCM 系 (`captured_queue` / `synthesized_queue`): **合計バイト数** で制限(`*_queue_max_bytes`、既定 500KB)。
  - テキスト系 (`recognized_queue` / `translated_queue`): **発話件数** で制限(`*_queue_size`、既定 10)。
- **PCM 系のあふれ時(`ByteBoundedQueue`)**: 「設定値を超えるまでは積み、超えたら先頭から退避」方針。`push_evicting` で必ず新規 item は保持し、合計が上限を超えていれば古いものから退避する(2件以上残っている場合のみ)。1 件の item が単独で上限を超える場合はその item を残す → **運用上、設定値を少し超える前提**。
- **テキスト系のあふれ時(`queue.Queue`)**: `maxsize` 到達時に古いものから捨てて新規 item を入れる。
- **共通**: 捨てた発話はレジャから即 pop されてリーク防止。テキストは ASR / Translator 段で既に書かれているため失われない。`on_dropped(seq_ids, stage_name)` で UI に通知。
- **エラー**: 各ステージ内で例外を捕捉し `ErrorHandler` に委譲。FATAL(およびリトライ枯渇)なら `stop_event` を立てて全ステージ停止。SKIP / WARN は当該 seq_id をレジャから pop して継続。RECOVERABLE は指数バックオフで最大 3 回リトライ(枯渇 → 停止は意図的設計。ユーザはローカル backend への切替でしのぐ)。
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
| `AppController` | GUI と内部モジュールを繋ぐ**ランタイム**: 設定の反映・backend のロード/キャッシュ・パイプラインの起動/停止。`UtteranceLedger` と `SequenceGenerator` を生成して `PipelineCoordinator` に渡す。UI への通知はすべてイベント emit(`add_<event>_listener`)で行う。 |
| `BackendRegistry` | レイヤ別バックエンドの登録/列挙/生成。GUIのプルダウン項目供給に使う。`register_default_backends(registry)` で標準実装を一括登録。opt-in extras backend は登録時に `requires_modules=("httpx",)` のように**必要 import 名を宣言**する(導入済み判定の材料。base 依存だけで動く backend は宣言不要)。 |
| `BackendCatalog` | **backend クラスのメタ情報問合せ口(状態なし)**。capture_kind / 対応言語 / credential_spec / capability hint を**インスタンス化せずに**引く。未登録・例外時は安全側の既定値に縮退(GUI の防御縮退が依存する規約)。`get_supported_target_languages` は `layer=` 指定で複合 backend(ASR レイヤ登録)にも問い合わせ可能。`is_backend_available(layer, name)` は宣言された必要 import 名を `find_spec` で**実 import せずに**判定する(未導入の extras backend をプルダウンに列挙しないための材料。宣言なし / 判定不能は True = 隠さない方向に縮退)。`AppController.catalog` で公開。 |
| `CredentialsService` | **認証情報の保管・疎通確認・verified フラグ管理**。CredentialsStore(lazy 初期化)+ ConfigStore の `credentials.*` に閉じる。`verify_and_save` は成功時のみ保存 + verified=True。`get_auth_state(layer, name)` は選択中 backend の認証準備状態(`AuthState`: NOT_REQUIRED / MISSING / UNVERIFIED / VERIFIED)を**インスタンス不要で静的判定**する(判定順は start 時の認証 gate と同一)。backend キャッシュには触らない(認証成功後の evict はランタイム側)。`AppController.credentials` で公開。 |

### `AppController` の主要メソッド

| メソッド | 役割 |
|---|---|
| `load_models()` | 全レイヤのバックエンド実体を生成し `_backends` にキャッシュする(冪等。既ロードのレイヤは触らない)。各レイヤごとに `status = LOADING → LOADED` を通知。 |
| `load_models_async(on_done, on_failed)` | `load_models()` をバックグラウンドスレッドで実行(「↻ ロード」ボタンの入口)。 |
| `is_loaded` | 全レイヤのバックエンドがメモリ常駐済みかを返すプロパティ。 |
| `start_pipeline_async(on_started, on_failed)` | DeviceValidator 同期チェック → Loader スレッドで「未ロードがあれば `load_models()`」+ Coordinator 起動 → 完了/失敗を UI コールバックで通知。ロード済みなら起動だけ。 |
| `start_pipeline()` | 同期版(テスト/スクリプト用)。GUI からは async 版を使う。 |
| `stop_pipeline()` | Coordinator のみ停止(バックエンド実体は `_backends` に常駐継続)。次回 Start でロード不要。 |
| `restart_pipeline_async(on_restarted, on_failed)` | `vt_restart` スレッドで `stop_pipeline()` → `start_pipeline()`(同期版)を直列実行する。動作中でない場合は no-op(`on_restarted` を即時呼ぶ)。多重起動は `on_failed("既に再開中です")` で拒否。主経路は `set_setting("devices", ...)` の反応系: 動作中に devices.* が書かれると自動で本メソッドが呼ばれ、ライフサイクルが restart イベント(started / completed / failed)として UI に届く(SettingsPanel がバナー表示)。 |
| `list_capture_sources()` / `list_output_devices()` | 設定中のバックエンドを使ってデバイス列挙(GUIプルダウン供給)。 |
| `get_setting()` / `set_setting()` / `save_settings()` / `load_settings()` | ConfigStore のラッパ。`set_setting("backends", layer, name)` は該当レイヤのキャッシュを破棄して INIT に戻す**だけ**(再ロードは自動発火しない。実ロードは 開始 / ↻ ロード / auto_load の 3 経路。押し間違いで重いロードを走らせない・ロード中の再変更で UI を固めないため)。`load_settings()` は**実効内容が変わったレイヤだけ** evict する(比較は「選択 backend 名 + その backend の backends_config」。同一レイヤはロード済みインスタンスと状態表示を維持 — 全破棄だと再読込のたびに重いモデルの再ロードが必要になるため)。PROCESS kind の capture backend が選ばれているときの `devices.input`(PID)は**ファイルに永続化しない**(A-7。PID はアプリ再起動で別プロセスに振られるため。誤って別アプリの音を取り込む事故も防ぐ): `save_settings()` は書き出し用コピーからのみ除外し **in-memory のセッション中選択は維持する**(`ConfigStore.save(transform=...)` + `_strip_volatile_inputs_for_save`)、`load_settings()` は読み込み直後に in-memory 側も空に正規化する(`_normalize_volatile_inputs_after_load`)。 |
| `add_<event>_listener(callback) -> Subscription` | UI への全通知の購読口(Subscription 1 本に統一)。イベント種: `status`(layer, status)/ `text_ready`(record。TTS 完了直後の履歴前倒し通知。`output_mode=text_only` ではここが最終通知)/ `utterance_done`(record)/ `fatal` / `warn`(message + context kwargs)/ `settings`(set_setting のキー tuple。認証情報の変化も `("credentials", <backend>)` でここに流れる — AuthState は status イベントが出ない経路で変わるため)/ `restart`(`PipelineRestartEvent`)/ `running`(bool。パイプライン起動完了 / 停止。SettingsPanel の動作中バックエンドロック等、Panel 間の「動作中かどうか」同期に使う)。listener は emit 元スレッドで呼ばれるため UI 側は `after(0, ...)` で marshalling する。 |
| `output_mode` | プロパティ。`backends.tts` を読み、`"none"` / 空文字 / 未設定なら `"text_only"`、それ以外なら `"audio"` を返す。独立した `pipeline.output_mode` キーは持たない。 |
| `TTS_NONE` | 定数 `"none"`。TTS=(なし) を表す ConfigStore 上の内部値。BackendRegistry にこの名前は登録しない。 |
| `_current_plan()` / `_active_layers()` | 現在の設定(backends.*)から編成表を組み、ロード/起動/認証 gate の対象レイヤ(= 編成表の lead)を返す。text_only の TTS / Output、複合 backend に吸収されたロールは含まれない(どちらも「編成表に載らない」の一例)。申告は registry の backend クラスから取り、`backend_cls` 未登録はレイヤ既定(単体ロール)で fallback。 |
| `get_absorbed_roles() -> dict[LayerKind, LayerKind]` | 複合 backend に吸収されているロール → 吸収先レイヤ。UI の「(〜側で実行)」表示と ready 判定の除外に使う。 |
| `get_target_language_provider()` / `get_effective_target_languages()` | 翻訳先言語の候補を決める backend(吸収時は複合側)とその対応言語。SettingsPanel の出力言語プルダウンが使う。 |
| `get_model_status(layer)` / `get_all_model_statuses()` | 各レイヤのモデル状態を取得。内部で `backends[layer].get_status()` に委譲(状態の真実は backend 側)。未ロード layer は `INIT`。 |
| `load_model_layer(layer)` | 単一レイヤだけをロードする(冪等)。実体 `_load_layer` は**モデル構築をロック外で行う**: `_load_lock` は `_backends` / in-flight 集合 / 世代カウンタの短い読み書きに限る(UI スレッドが evict のためにロックを取っても待たされない)。同一レイヤの並行ロードは in-flight 待ち合わせで 1 回の構築を共有し、構築中に evict(設定変更)で世代が進んだら完成品を破棄して最新の設定でロードし直す(last-write-wins。構築は中断できないため完走 → 破棄)。 |
| `get_auth_state(layer)` / `get_all_auth_states()` | 選択中 backend の認証準備状態(`AuthState`)。実体は `CredentialsService.get_auth_state`(静的判定・ロード不要)。SettingsPanel の行ステータス上書きと ready_state の開始ガードの入力。 |
| `load_auto_load_layers_async(on_done, on_failed)` | `auto_load=True` のレイヤだけを Loader スレッドで順次ロードする(起動シーケンス。既定では対象なし)。 |
| `get_recent_durations(layer) -> list[float]` | レイヤ別の直近 5 件処理時間(ms、古い→新しい順)。`_handle_utterance_done` で push される。詳細ダイアログ(LayerSettingsDialog)で使う。 |
| `get_status_snapshot() -> tuple[list[LayerStatusLine], list[tuple[LayerKind, ErrorRecord]]]` | 全レイヤの状態 + 直近エラーを**整形前のデータ**で返す。各行には編成上の扱い(`disposition`: active / absorbed / skipped と吸収先情報)と認証準備状態(`auth: AuthState`)も載り、UI は「動かないレイヤ」「認証未完了」を実態どおりに描画できる。文字列への整形は UI 側 `gui/logic/status_summary.py` の役割。 |
| `catalog` / `credentials` プロパティ | メタ問合せの実体 `BackendCatalog` と認証フローの実体 `CredentialsService` を公開する。新規コードはこちらを直接使う。 |
| メタ問合せ / 認証の互換窓(`get_capture_kind` / `get_supported_*_languages` / `supports_auto_detect` / `get_credential_spec` / `get_backend_capability_hint` / `get_credential` / `set_credential` / `delete_credential` / `has_credential` / `is_backend_verified` / `invalidate_verification`) | 実装本体は `BackendCatalog` / `CredentialsService` にあり、これらは既存呼び出し元互換の **1 行委譲**(参照の全付け替えと削除は将来の整理候補)。 |
| `verify_and_save_credentials(layer, name, values) -> VerifyResult` | `CredentialsService.verify_and_save`(疎通確認 + 保存 + verified 永続化)に委譲した上で、後処理を行う: 認証成功時、該当レイヤで本 backend が選択中かつロード済みなら evict して INIT に戻す(古い認証情報のインスタンスを使い続けない。即時再ロードはしない — lazy 方針)。成功時は `("credentials", <backend>)` の settings イベントも emit。backend キャッシュに触るためこの後処理だけはランタイムの責務。 |
| `_handle_text_ready(record)` | Coordinator から呼ばれる(TTS スレッド)。レジャのスナップショットを text_ready イベントにそのまま中継し、UI 履歴を音より前に出す。 |
| `_handle_utterance_done(record)` | Coordinator から呼ばれる(Output スレッド)。`TranslationLogger.write_record(record)` で jsonl 追記後、utterance_done イベントを emit。 |
| `_handle_dropped(seq_ids, stage)` | Coordinator から呼ばれる。テキストは各段で既に書かれているのでログのみ。 |

### ロード/起動/停止 のライフサイクル

```
[起動]
  MainWindow → AppController.load_auto_load_layers_async()
              └→ Loader スレッド: auto_load=True のレイヤだけ生成(既定は対象なし → 即時完了)
[Start クリック](ボタンは常時押下可)
  AppController.start_pipeline_async()
              └→ 同期検証(DeviceValidator + 認証 gate)
              └→ Loader スレッド: load_models()(未ロード分のみ) → _start_coord()
[Stop クリック]
  AppController.stop_pipeline()
              └→ Coordinator.stop() のみ。_backends は残置
[バックエンド設定変更]
  AppController.set_setting("backends", layer, new_name)
              └→ _backends.pop(layer) + 世代カウンタ +1 → INIT(破棄のみ。自動再ロードしない)
                 次の Start / ↻ ロード / auto_load で新しい選択がロードされる。
                 ロード構築中に変更された場合は、完成品を破棄して最新の選択を
                 ロードし直す(last-write-wins。UI はロックを待たないので固まらない)
[動作中のデバイス変更]
  AppController.set_setting("devices", input|output, id)
              └→ vt_restart スレッド: stop_pipeline() → start_pipeline()
                 + restart イベント(started / completed / failed)を emit
```

### モデル状態 (`ModelStatus`)

| 値 | 表示 | 色 | 意味 |
|---|---|---|---|
| `INIT` | "Init" | gray | 初期状態。まだロード処理を起動していない(アプリ起動直後やバックエンド差替直後)。 |
| `MISSING_CREDENTIALS` | "Missing Credentials" | red | クラウド backend で認証情報が未設定。 |
| `NOT_DOWNLOADED` | "Not Downloaded" | red | ロード試行に失敗(キャッシュ無 + DL失敗 等)。 |
| `DOWNLOADING` | "Downloading..." | amber | モデル DL 中。`ModelInfo.download_size_gb` をステータステキストボックスに併記。 |
| `LOADING` | "Loading..." | amber | メモリへロード中。 |
| `LOADED` | "Loaded" | green | メモリ常駐済み(即使用可)。 |

- 初期表示はキャッシュ有無に関係なく **全レイヤ INIT** に統一する(キャッシュ有→ LOADED と出すと、その直後に自動ロードが走って `Loaded→Loading→Loaded` の不自然な遷移になるため)。
- 通常の遷移: `INIT → (DOWNLOADING) → LOADING → LOADED`。キャッシュ有なら DOWNLOADING はスキップ。失敗時のみ `→ NOT_DOWNLOADED`。バックエンド名を変更すると当該レイヤだけ `LOADED → INIT`(再ロードは次の 開始 / ↻ ロード)。

### 認証準備状態 (`AuthState`) と表示の上書き

`ModelStatus` がインスタンスの状態であるのに対し、`AuthState` は**選択中 backend の
認証準備状態を設定情報だけで静的判定**したもの(NOT_REQUIRED / MISSING / UNVERIFIED /
VERIFIED)。未ロード(Init)でも「認証が必要・未完了」を表示・ガードに使える。

| AuthState | 行ステータス表示 | 色 | 意味 |
|---|---|---|---|
| `MISSING` | "Missing Credentials" | red | 必要な認証情報が未入力(表記はインスタンス由来の MISSING_CREDENTIALS と同一)。 |
| `UNVERIFIED` | "Not Verified" | amber | 鍵は保存済みだが疎通確認(verify)未実施。 |
| `NOT_REQUIRED` / `VERIFIED` | (上書きなし) | — | 通常の ModelStatus 表示に委譲。 |

- 認証未完了の表示は ModelStatus より優先する(「Loaded(緑)なのに Start で認証エラー」の矛盾を見せない)。判断は `gui/logic/auth_display.py` の純関数。
- 開始ボタンは常時押下可。disable になるのは 認証未設定(`AuthState.MISSING` or
  `MISSING_CREDENTIALS`)/ 認証未検証(`AuthState.UNVERIFIED`)/ `DOWNLOADING` /
  PROCESS kind で入力未選択 のときだけ。`INIT` / `LOADING` 残存時はラベルで補助表示
  (「停止中(押下時にロードします)」等)。判定は `gui/logic/ready_state.py` の純関数。
  押下時の認証 gate(`_check_missing_credentials_gate` の FatalError)は最後の防波堤として残る。

### バックエンド基底 (`BackendBase`)

全 backend 共通の基底ミックスイン(`common/backend_base.py`)。
各レイヤの抽象基底(`AsrBackend`, `VadBackend`, ...)が `class AsrBackend(BackendBase, ABC)` の形で多重継承する。

| メソッド | 役割 |
|---|---|
| `get_status() -> ModelStatus` | 当該 backend の現状態を返す。 |
| `_set_status(status)` | サブクラス内部からの状態更新フック。同値遷移は notify を発火しない。 |
| `subscribe(callback) -> Subscription` | 状態変化購読。解除は `Subscription.unsubscribe()`。 |
| `record_error(exc, *, context=None)` | エラー履歴(リングバッファ 5 件)に積む。 |
| `get_recent_errors() -> list[ErrorRecord]` | 直近エラー履歴(古い→新しい)。 |
| `list_recommended_models() -> list[ModelInfo]` | 推奨モデル一覧(モデル概念のない backend は空)。 |

**設計判断**: 状態の真実は backend 側にある。`AppController` は購読者として動き、UI に
re-broadcast するだけ(中央 dict での二重管理をしない)。

### モデルメタ情報 (`ModelInfo`)

backend の `list_recommended_models()` の戻り値。GUI のドロップダウン項目 + リソース目安表示で参照。

| フィールド | 意味 |
|---|---|
| `name` | backend 内部での識別子(HF repo id 等)。 |
| `display_name` | GUI 表示名。 |
| `ram_gb` / `vram_gb_if_gpu` / `download_size_gb` | 概算リソース。`None` は不明。 |
| `target_proc_ms_per_sec_audio` | 音声 1 秒あたりの目安処理時間(ms)。翻訳のように非適用なら `None`。 |

### `BackendCapabilities` クラウド系フィールド

| フィールド | 意味 |
|---|---|
| `is_cloud` | クラウド(外部 API)backend か。GUI で ☁ バッジ表示。 |
| `requires_credentials` | API key 等の認証情報が必要か(認証 gate / 認証ボタンの表示条件)。 |
| `service_name` | 同意ダイアログ等での表示名(例: "OpenAI Whisper API")。 |
| `terms_url` | 利用規約 URL。同意ダイアログで参照リンクを出す。 |

backend 実装者は **エラーを適切な `AppError` サブクラスに分けて包む**こと。
詳細は [`errors.py`](../../src/voice_translator/common/errors.py) の docstring を参照。

---

## 4. 横断機能(共通)

| クラス/モジュール | 役割 |
|---|---|
| `ConfigStore` | 設定値(選択中のバックエンド名、デバイス、言語ペア、ログ出力先 等)の永続化(YAML)と読込。 |
| `Logger`(`setup_app_logger`) | stdout + `app.log` への汎用アプリログ初期化。 |
| `TranslationLogger` | 翻訳1件 = jsonl 1行 として履歴ファイルに追記。`write_record(record: dict)` で ledger の pop 結果を直接書く。ON/OFF 切替可。機械処理向け。 |
| `TextLogger` | `write_src(seq_id, text, lang)` / `write_tgt(seq_id, text, lang)` を提供し、各ステージから直接呼ぶ。書式 `[YYYY-MM-DD HH:MM:SS] #SEQ [lang] text`。src/tgt 個別 ON/OFF。 |
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
| `capture.process_enumerator` (モジュール) | WASAPI AudioSession を pycaw / psutil で列挙 + 試聴 peak を供給するヘルパー。役割は ProcTap backend と `ProcessSelectDialog` で共有する「音声出力中プロセスの一覧」と「peak 値の継続供給」の提供。**永続 COM ワーカスレッド `_PeakWorker` を 1 つだけ持ち**、全 pycaw 呼び出しはそのスレッド内で実行する(GUI スレッドの STA と pycaw の MTA 要求が競合するため)。公開 API は `enumerate_active_processes()` / `start_audition(pid) -> bool` / `stop_audition()` / `latest_peak() -> float` / `is_auditioning()` / `dispose()`。試聴中はワーカ内部で 5fps poll が peak を取って atomic float に保持、GUI スレッドは `latest_peak()` を atomic 読みするだけ(スレッド境界を毎ティックまたがない)。`enumerate_active_processes` は **`AudioSessionState` が Active(1) または Inactive(0) を採用**(Expired のみ除外、Sndvol と一致)+ **全 Render エンドポイント走査**(`IMMDeviceEnumerator → EnumAudioEndpoints(eRender, ACTIVE) → 各デバイスから IAudioSessionManager2 を Activate`)+ PID 単位 dedupe(同 PID が複数エンドポイントに居る場合は最初の 1 件のみ採用)+ `psutil.Process(pid).name()` で名前補完(失敗時 `"unknown"`)。**`AudioUtilities.GetAllSessions()` を使わない理由**: Win11 + 複数オーディオデバイス構成(HDMI / Bluetooth / 仮想デバイス等)で、Chrome/Firefox 等がデフォルト以外のエンドポイントに紐づくと取りこぼされるため(実機確認済み)。pycaw / psutil / comtypes 呼び出しは `_list_active_sessions` / `_resolve_process_name` / `_PeakWorker._run` に隔離、テストでは monkeypatch で完全置換できる。 |

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
| `MainWindow` | アプリのルートウィンドウ。NotificationBanner / SettingsPanel / ControlPanel を内包する(customtkinter)。起動時に `load_auto_load_layers_async()` を呼ぶ(auto_load=True のレイヤのみ先行ロード、既定は対象なし)。Panel 間の参照注入はしない(各 Panel が AppController のイベントを自身で購読する)。閉じる時にパイプライン停止を保証。 |
| `SettingsPanel` | レイヤ別実装の選択 / src/tgt 言語 / 入出力デバイス選択 / ログ出力先指定 / 設定保存・読込 + **レイヤ別モデルステータスラベル(色付き)**。「バックエンド / デバイス / 翻訳」の 3 セクション独立折り畳み。各レイヤ行に「設定」ボタンがあり、`LayerSettingsDialog` を開いてレイヤ固有の設定を編集できる。LOADED 状態のとき、device 概念を持つレイヤ(ASR/Translator)は `Loaded (cuda)` のように **実デバイス名を併記**(`AppController.get_layer_device(layer)` 経由)。**入力言語(src)プルダウンは ASR backend ごとの対応言語に動的追従**:backend 切替時に選択肢を再構築し、既存設定値が新 backend で非対応なら自動 fallback(auto 対応なら `auto`、非対応なら先頭言語)+ 通知バナーで明示(判断は `gui/logic/language_choices.py`)。表示形式は `"eng (English)"`(共通言語テーブル `common/languages.py` で変換。内部標準は ISO 639-3)。**入力ソース UI は CAPTURE backend の kind に応じて切替**:`capture_kind == DEVICE` ならプルダウンで `list_sources()` を表示、`PROCESS` なら「プロセス選択…」ボタンに切替し、押下で `ProcessSelectDialog` を開いて PID を選ぶ。ボタンラベルは現 PID を反映(`PID 1234 ▼`)。**動作中の入出力デバイス変更**: ハンドラは `set_setting("devices", ...)` を書くだけ(自動 restart は AppController の反応系)。restart イベントを購読して NotificationBanner に「再開中…」(started、永続)→ dismiss(completed)/ show_error(failed)を反映する。状態ラベルの更新は `add_status_listener` を自身で購読。「設定を再読込」は動作中 / ロード中は拒否して警告バナーを出す(全 backend evict が走るため)。**編成表示(動かないレイヤの実態表示)**: 吸収されたレイヤ(例: ASR+翻訳複合選択時の翻訳行)は**プルダウンと設定ボタンを disabled + ステータス欄は空表示**(無効化で「使われない」が伝わるため文言は出さない。代行 backend 名の明示は動作タブのステータス集約の役割)。text_only の TTS/Output 行はステータス欄に「(なし)」(`gui/logic/backend_display.py` の `skipped_status_text()`)。選択値そのものは保存され、複合をやめた瞬間に元の選択が復帰する。**未導入 backend の非列挙**: プルダウン候補は `catalog.is_backend_available` で導入済みに絞る(「未導入のものを選んで Not Downloaded」の混乱を候補の時点で防ぐ。判定失敗・全滅時は無濾過に縮退)。**認証状態の上書き表示**: 選択中 backend の `AuthState` が MISSING / UNVERIFIED の行は、ステータス欄を "Missing Credentials"(赤)/ "Not Verified"(琥珀)で上書きする(未ロードでも表示。編成表示の上書きはさらに優先。判断は `gui/logic/auth_display.py`)。認証情報の変化は settings イベント `("credentials", ...)` を購読して再描画。**動作中ロック**: running イベントを購読し、動作中は全バックエンド行のプルダウン / 設定ボタンを disable(動作に反映されない変更で「何で動いているのか」が表示と食い違うのを防ぐ)。停止で復元し、吸収 / TTS=(なし) 由来の disable を再適用。devices / languages は動作中変更に対応済みなので対象外。出力言語プルダウンは「翻訳ロールを実際に担う backend(吸収時は複合側)の対応言語 ∩ TTS の読み上げ可能言語」で構築される(TTS の対応言語が不明 / TTS=(なし) は絞らない。積が空になる組合せは絞らず警告に委ねる。判断は `gui/logic/language_choices.py:restrict_to_tts`)。TTS 切替時も候補を再構築する。**言語の検索選択**: src/tgt 各行に「🔍」ボタンがあり、押下で `LanguageSelectDialog`(検索付き)を現在の候補で開く。MMS-TTS の多言語対応で出力候補が 100 超になり得る(`OptionMenu` は検索非対応)ため。選択結果はプルダウン選択と同じハンドラ(`_on_*_lang_changed`)へ流し、保存 / fallback / TTS 互換チェックを共有する。絞り込みの判断は `gui/logic/language_filter.py`。 |
| `ControlPanel` | 動作開始/停止トグル(**常時押下可**。「認証情報未設定」「認証未検証」「モデル DL 中…」「プロセス未選択」のときだけ disable)、「↻ ロード」「🔊 出力テスト」ボタン、最新翻訳テキスト履歴(`#seq` 付き、クリアボタンあり)、直近平均レイテンシ表示、ステータス集約テキストボックス。**警告は UI には出さず、致命的エラーのみ履歴+「停止中(エラー)」表示**(警告も app.log には残る)。レイテンシは `timeline` の `t_vad_end → t_playback_start` 区間。**アクセラレータ集約表示**:各レイヤの `device` を集約して「演算: GPU (cuda)」「演算: CPU のみ」を色付きで表示。ボタン状態の判定はすべて `gui/logic/ready_state.py` の純関数(PROCESS kind で `devices.input` が空なら「プロセス未選択」disable。PID 選択完了は settings イベント購読で即時 enable に遷移)。**🔊 出力テストボタン**(出力切り分け用):`AppController.test_output_playback("テスト音声")` を別スレッドで呼び、TTS → Output → スピーカの経路を 1 回だけ叩く。text_only(`🔊 (TTS なし)`) / `devices.output` 空(`🔊 出力未選択`) / 動作中(`🔊 (動作中)`) で disable。**通知はすべて AppController の listener 購読**(status / text_ready / utterance_done / fatal / warn / settings の 6 本)。30 秒周期の再描画はイベント化されていない backend エラー履歴(RECOVERABLE/SKIP)の遅延表示専用。 |
| `LayerSettingsDialog` | 単一レイヤの設定編集ウィンドウ(CTkToplevel)。`layer_settings_schema.LAYER_SETTINGS` のスキーマに従ってラベル + 入力欄を動的に構築し、保存時に `AppController.set_setting` で ConfigStore に書き戻す。バックエンド条件付きフィールド(SAPI rate 等)に対応。 |
| `layer_settings_schema` モジュール | レイヤ別の編集可能な設定項目を `SettingField(keys, label_key, field_type, default, help_key, applies_when_backend)` の集まりで宣言。新項目はここに追加するだけで GUI に出る(スキーマ駆動)。ラベル/ヘルプは **i18n キー**(`label_key` / `help_key`)で持ち、`LayerSettingsDialog` が `tr()` で表示時に解決する(文言は `gui/i18n.py` の `layer_settings.*`)。 |
| `ProcessSelectDialog` | per-process キャプチャ用のプロセス選択ダイアログ(CTkToplevel)。`capture_kind == PROCESS` の入力ソースを選ぶ専用 UI で、`SettingsPanel` の「プロセス選択…」ボタンから呼ばれる。構成: 列挙テーブル(プロセス名 + PID、ラジオ選択) / ↻ 更新 / ▶ 試聴開始・■ 停止トグル / レベルメータ(`CTkProgressBar`) / OK・Cancel。試聴は本番パイプラインと完全独立(WASAPI Process Loopback を開かない、pycaw メータのみ)。 |
| `ProcessSelectController` | `ProcessSelectDialog` の状態機械を GUI 非依存に切り出したもの。列挙 / 選択 PID / 試聴 ON-OFF / peak decay を保持。`_PeakProvider` Protocol を経由して peak 供給元(本番は `process_enumerator` モジュールの永続ワーカ、テストは fake)を差し替えられる。GUI 不要のロジック単体テストはこのクラスを直接生成して fake provider で検証する。 |
| `gui/logic` パッケージ | **UI 判断ロジックの純関数集**。「現在の状態 → UI に表示すべき値」の計算だけを行い、widget / AppController / ConfigStore には触らない(依存は common の純粋モジュールと標準ライブラリのみ)。Panel は「入力収集 → logic → widget へ反映」の配線役に徹する。文言は logic に直書きせず `gui/i18n.py` の `tr()` で引く(後述)。内訳: `ready_state.py`(開始/ロード/出力テストボタンの文言・有効無効。`compute_ready_state`。text_only / 吸収ロールは判定から除外。認証未設定/未検証ガードも担う)/ `language_choices.py`(言語候補・fallback 判定・通知文言)/ `language_filter.py`(言語検索の絞り込み: コード/英語名の部分一致 + 前方一致優先の並び替え。`LanguageSelectDialog` の判断部)/ `backend_display.py`(TTS「(なし)」`tts_none_display()`・CAPTURE kind の表示↔内部値変換・対象外レイヤの `skipped_status_text()`)/ `auth_display.py`(AuthState → 行ステータス上書き文言・色。文言は `ModelStatus` 表示と揃えるミラーで i18n 対象外)/ `status_summary.py`(ステータス集約テキストの整形。golden テストで形式固定)/ `accel_summary.py`(演算: GPU/CPU 集約判定)/ `restart_messages.py`(自動 restart バナー文言)/ `palette.py`(配色定数)。テストは `tests/test_logic_*.py`(GUI 不要の純 small)。 |
| `i18n` モジュール | **UI 表示文言を集約する言語別カタログとアクセス API**(`gui/i18n.py`。文言は「データ」で logic の純関数とは責務が異なり、`common/messages.py` との同名衝突も避けるため gui 直下に置く)。画面に出る文言を言語別カタログ(`_CATALOGS`)に一元化し、`tr(key, **kwargs)` でキー経由で引く(`str.format` で動的差し込み。未登録キー・引数不足は例外)。`current_locale()` がロケール解決の単一窓口(現状 ja 固定・起動後不変。将来 en/zh/es を辞書追加で拡張、即時切替は可変化 + 再描画で対応)。logic / widget は文言を直書きせず必ず `tr()` を通すのが規約で、キーは**文脈単位**(同一文言でも出る場所が違えば別キー)・**リテラル渡し**(動的キー禁止)、`tr()` は**表示する瞬間(関数内)で呼ぶ**(モジュールレベルで定数に焼かない)。カタログに入れるのは翻訳対象の文言のみ(enum value のミラー等は源を直接参照)。欠落/死に/動的キー・トップレベル `tr()`・gui/logic の CJK 直書き残存・テンプレ引数不足を AST 解析の small テスト(`tests/test_i18n.py`)で検出する。 |

### スレッドセーフ規約

- tkinter ウィジェットはメインスレッドからしか触れない。Coordinator/Loader スレッドからの通知は **`widget.after(0, lambda: ...)`** でメインスレッドに戻して反映する。
- AppController の listener(`add_<event>_listener` で登録したもの)は emit 元スレッド
  (Loader / Coordinator / vt_restart 等)の上で実行されるため、UI 側は必ず `after()` 経由で処理すること。

---

## 6. 拡張時の追加例(参考)

- **OS別音声取得を追加**: `AudioCaptureBackend` を OS 別に実装(例: `GstreamerCaptureBackend`、`WasapiProcessCaptureBackend`)し、`BackendRegistry.register()` する。
- **TTS差し替え**: `TtsBackend` の新実装(例: `VoicevoxTtsBackend`)を登録するだけで GUI のプルダウンに出現。
- **LLM翻訳**: `TranslatorBackend` の追加実装(例: `OllamaTranslatorBackend`)を登録。
- **新バックエンドのキャッシュ判定追加**: `cache_check` に check 関数を追加し、`AppController._CACHE_CHECKER_NAMES` に登録。
- **複合バックエンド(複数ロール一括)**: 該当する複合 I/F(ASR+Translator なら `AsrTranslatorBackend`)を実装し、先頭ロールのレイヤに登録する。`covers_roles()` の申告だけで編成・吸収・UI 表示が連動する。新しいロール組合せ(TTS+Output 等)は複合 I/F と `PipelineCoordinator._process_*` の追加が必要。

---

## 7. 命名・配置の規約

- ファイル配置は `src/voice_translator/<layer>/<implementation>.py`(例: `src/voice_translator/asr/faster_whisper_backend.py`)。
- 抽象I/Fは `src/voice_translator/<layer>/backend.py`(例: `src/voice_translator/asr/backend.py` に `AsrBackend`)。
- バックエンドクラスの命名は `<実装名>Backend` で揃える。
- 各クラスの**冒頭1〜2行コメントに役割を明記**(CLAUDE.md 準拠)。
