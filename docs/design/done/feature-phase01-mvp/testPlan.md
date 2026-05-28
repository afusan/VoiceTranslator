# testPlan: feature/phase01-mvp

Phase 1 (MVP) のテスト項目。`small` 中心、`middle/large` は正常系のみ(CLAUDE.md準拠)。

凡例: ☐ 未着手 / ◐ 着手中 / ☑ 完了

---

## 1. small テスト(単体)

### 1-1. データ/共通
| 状態 | 項目 | 期待 |
|---|---|---|
| ☑ | `UtteranceTimeline.mark()` で時刻が記録される | 同名キーで上書き、未記録は None |
| ☑ | `Utterance` のフィールド初期化と段階的追記 | 各ステージで追加可能 |
| ☑ | `AppError` severity 別の判定 | FATAL/RECOVERABLE/SKIP/WARN が正しく分類 |
| ☑ | `ErrorHandler` の振り分け | severity に応じてダイアログ/リトライ/スキップ/警告 |
| ☑ | `ConfigStore` の保存・読込 | YAML round-trip、デフォルト値、マージ |
| ☑ | `Logger` の jsonl 出力 | ON/OFF切替、1行=1発話の形式 |
| ☑ | `ModelStatus` enum と `cache_check.*` の判定 | キャッシュ有→LOADED / 無→NOT_DOWNLOADED |

### 1-2. バックエンド(モック化)
| 状態 | 項目 | 期待 |
|---|---|---|
| ☑ | `SoundcardCaptureBackend` のリサンプリング | stereo→mono(平均)、float32化 |
| ☑ | `SileroVadBackend` の発話切り出し | start/end イベントで Utterance を返す |
| ☑ | `FasterWhisperAsrBackend` の入出力 | PCM → src_text(モデル呼び出しはモック) |
| ☑ | `Nllb200TranslatorBackend` の入出力 | src_text → tgt_text(モデル呼び出しはモック) |
| ☑ | `SapiTtsBackend` の入出力 | tgt_text → tts_pcm/samplerate(エンジンはモック) |
| ☑ | `SoundcardOutputBackend` の出力 | デバイス指定が反映される(モック) |
| ☑ | `DeviceValidator`: 入力=出力で起動拒否 | FatalError 発生 |
| ☑ | `DeviceValidator`: 入力≠出力でOK | 例外なし |
| ☑ | `BackendRegistry` の登録/列挙/生成 | 登録順・名前検索・上書き |
| ☑ | `register_default_backends` の挙動 | 6レイヤすべてに登録される |

### 1-3. パイプライン制御
| 状態 | 項目 | 期待 |
|---|---|---|
| ☑ | `PipelineCoordinator.start/stop`(3スレッド) | ライフサイクル呼び出し + 全スレッド join |
| ☑ | パイプライン上の Utterance フロー(モック) | 各ステージが順に呼ばれ、timelineが記録される |
| ☑ | ステージ途中での例外発生時の振り分け | SKIPは継続、FATALは停止 |
| ☑ | `AppController.start_pipeline_async` | Loader スレッドで起動し on_started が呼ばれる |
| ☑ | `AppController` の ModelStatus listener | レイヤ毎に LOADING → LOADED が通知される |

---

## 2. middle テスト(結合・正常系のみ)

| 状態 | 項目 | 期待 |
|---|---|---|
| ☑ | 合成PCM → 3スレッドパイプライン → モック出力(`test_pipeline_e2e`) | 期待数の発話が timeline 完備で再生される |
| ☑ | 実 WAV → `WavReplayCapture` → パイプライン | E2E が再現可能に通る |
| ☐ | WAV(英語短文) → 実 ASR + 翻訳 + TTS → 出力WAV保存 | 実モデルで縦通し(`@pytest.mark.slow`、未整備) |
| ☐ | GUI操作シミュレーション(開始 → 数発話 → 停止) | 実機目視 |
| ☑ | 設定保存→再起動→設定読込 | 値が完全に再現される |

---

## 3. large テスト(E2E・正常系のみ)

| 状態 | 項目 | 期待 |
|---|---|---|
| ☐ | 実機: ループバック入力(YouTube動画再生) → 別デバイス出力 | 数発話分が翻訳音声として出力される(手動確認) |
| ☐ | 実機: マイク入力 → スピーカ出力(別デバイス) | 自分の発話が翻訳音声として出力される(手動確認) |

※ middle / large は基本正常系。異常系は network/DB/外部I/F に該当しないため重視しない(CLAUDE.md準拠)。

---

## 4. 異常系(重点項目)

CLAUDE.md「network/DB/システム間I/F は異常系も十分に想定」に該当する:
- 外部モデルロード(faster-whisper, NLLB-200)
- オーディオデバイス(soundcard 経由のWASAPI)

| 状態 | 項目 | 期待 |
|---|---|---|
| ☑ | モデル初期化失敗(各バックエンド) | FATAL 例外で包まれる(各 *_backend テスト内で検証) |
| ☐ | モデルファイル未DL時の挙動(実機) | UI: NOT_DOWNLOADED 表示 + 起動時に DL が走る |
| ☐ | オーディオデバイス消失(動作中に抜線、実機) | FATAL → 停止 + 通知 |
| ☐ | デバイス権限なし(実機) | FATAL → 起動拒否 |
| ☑ | ASR/翻訳の一時失敗(空文字、例外) | SKIP → 発話破棄、継続(モックで検証) |
| ☐ | レイテンシ閾値超過(実機) | WARN → バナー、継続 |

---

## 5. 実行方法

```bash
py -m uv run pytest                # 全体(現状139件)
py -m uv run pytest -v             # テスト名表示
py -m uv run pytest --cov=src      # カバレッジ付き
```

将来 `@pytest.mark.slow` を付けて実モデル中の middle/large テストを分離する想定(現状は未整備)。
