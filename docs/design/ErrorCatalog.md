# ErrorCatalog (例外カタログ・雛形)

各バックエンドが現状 raise している `AppError` の一覧と、`Severity` の判定根拠。
**現状の挙動を可視化するだけ**で、将来の調整(特に `RecoverableError` への格上げ判定)は
実機運用で集まった例外サンプルを元に行う。

凡例:
- ✅ 実装済(コードで raise している)
- ⚠️ 課題(現状の severity が再考の余地あり)
- 🔲 未実装(将来必要かもしれない分類)

---

## ASR (`FasterWhisperAsrBackend`)

| 場面 | raise する例外 | severity | 備考 |
|---|---|---|---|
| ✅ モデル初期化失敗(`WhisperModel(...)` の例外) | `FatalError("初期化に失敗", cause=元例外)` | FATAL | モデルロード不能は復旧不可 |
| ✅ pcm が None / 空 | `SkipError("ASR入力PCMが空")` | SKIP | 短い無音が来た等、当該発話のみ破棄 |
| ✅ 推論失敗(`model.transcribe` の例外) | `FatalError("推論失敗", cause=元例外)` | FATAL | ⚠️ 一時的 OOM の可能性もある(将来 RECOVERABLE 候補) |

## Translator (`Nllb200TranslatorBackend`)

| 場面 | raise する例外 | severity | 備考 |
|---|---|---|---|
| ✅ モデル/トークナイザのロード失敗 | `FatalError("ロードに失敗", cause=元例外)` | FATAL | |
| ✅ 入力テキストが空 | (raise しない。空文字を返す) | — | Coordinator 側でスキップ判定 |
| ✅ 翻訳推論失敗 | `FatalError("翻訳失敗", cause=元例外)` | FATAL | ⚠️ ネット系のバックエンド(DeepL等)導入時は RECOVERABLE が要る |
| ✅ 翻訳結果が空 | `SkipError("翻訳結果が空")` | SKIP | |

## TTS (`SapiTtsBackend`)

| 場面 | raise する例外 | severity | 備考 |
|---|---|---|---|
| ✅ pyttsx3 のロード失敗 | `FatalError("pyttsx3 のロードに失敗", cause=元例外)` | FATAL | |
| ✅ 入力テキストが空 | `SkipError("TTS入力テキストが空")` | SKIP | |
| ✅ 合成失敗(SAPI / WAV 読込) | `FatalError("SAPI/TTS 合成失敗", cause=元例外)` | FATAL | ⚠️ SAPI は一時的におかしくなることがあり RECOVERABLE 候補 |
| ✅ 合成 PCM が空 | `SkipError("合成された音声が空")` | SKIP | |

## VAD (`SileroVadBackend`)

| 場面 | raise する例外 | severity | 備考 |
|---|---|---|---|
| ✅ silero-vad モジュールのロード失敗 | `FatalError("silero-vad のロードに失敗")` | FATAL | |
| ✅ モデル初期化失敗 | `FatalError("silero-vad の初期化に失敗")` | FATAL | |
| ✅ 推論失敗 | `FatalError("silero-vad 推論失敗", cause=元例外)` | FATAL | |

## AudioCapture (`SoundcardCaptureBackend`)

| 場面 | raise する例外 | severity | 備考 |
|---|---|---|---|
| ✅ デバイス未発見 | `FatalError("指定された入力ソースが見つかりません")` | FATAL | |
| ✅ レコーダ未初期化での read | `RuntimeError`(包んでいない) | (FATAL扱い) | ⚠️ AppError でラップする方が綺麗 |

## AudioOutput (`SoundcardOutputBackend`)

| 場面 | raise する例外 | severity | 備考 |
|---|---|---|---|
| ✅ デバイス未発見 | `FatalError("指定された出力デバイスが見つかりません")` | FATAL | |
| ✅ 再生対象 PCM が空 / None | `SkipError("再生対象の TTS PCM が空")` | SKIP | |
| ✅ pcm が ndarray でない | `FatalError("tts_pcm は np.ndarray が必要")` | FATAL | 内部バグ相当 |
| ✅ 再生失敗(`player.play` の例外) | `FatalError("音声再生に失敗", cause=元例外)` | FATAL | ⚠️ デバイス一時切断は RECOVERABLE 候補 |
| ✅ start 前の play | `RuntimeError`(包んでいない) | (FATAL扱い) | ⚠️ AppError でラップする方が綺麗 |

---

## 設定ロード / 設備系

| 場面 | raise する例外 | severity |
|---|---|---|
| ✅ ConfigStore 書き込み失敗 | `FatalError("設定ファイルを書き出せません", cause=OSError)` | FATAL |
| ✅ ConfigStore 読込失敗 | `FatalError("設定ファイルの読込に失敗", cause=YAMLError)` | FATAL |
| ✅ ConfigStore 構造不正 | `FatalError("設定ファイルの構造が不正")` | FATAL |
| ✅ DeviceValidator: 入出力が同じ | `FatalError("入力と出力に同じデバイスは使用できません")` | FATAL |

---

## 振り分け方針(整理)

| Severity | 該当条件 | 挙動 |
|---|---|---|
| **FATAL** | モデルロード不能 / デバイス消失 / 設定破損 など、続行しても次も失敗する見込み | パイプライン停止、UI に通知 |
| **RECOVERABLE** | 一時的失敗(ネット瞬断、瞬間的なリソース不足等)。**現在の MVP では誰も throw していない**。将来 tenacity 等でリトライ対象に | リトライ |
| **SKIP** | 当該発話固有の問題(空入力、無音、結果が空) | その発話のみ破棄、継続 |
| **WARN** | 動作には支障ないが運用者に伝えたい(レイテンシ閾値超等)。**現在の MVP では誰も throw していない**。 | バナー表示、ログ蓄積 |

## 未対応 / 将来課題

- **RECOVERABLE の実装**: 現状どのバックエンドも RECOVERABLE を throw していない。tenacity 等のライブラリ導入時にあわせて整理(別作業)。
- **WARN の実装**: latency 監視は config に閾値があるが未実装(shortcutList B-4)。
- **未包装の RuntimeError**: `Capture.read_chunk` start 前 や `Output.play` start 前 は素の `RuntimeError`。AppError で包む方が綺麗(機能上は ErrorHandler が FATAL 扱いするので動作はする)。

## ログ書式(ErrorHandler 出力)

```
seq=42 stage=ASR [FATAL] faster-whisper 推論失敗 (caused by RuntimeError: oom)
seq=7 stage=Translator [SKIP] 翻訳結果が空
stage=Capture [FATAL] 指定された入力ソースが見つかりません
```

- `seq=` は Input 段で発行後にのみ付与される(Capture/VAD 段の例外には付かない)。
- `stage=` は Coordinator 内の `_dispatch_error(..., stage=...)` 呼び出しで指定。
- `caused by` は例外チェーン(`__cause__`)があれば自動付与。
