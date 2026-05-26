# testPlan: feature/phase01-mvp

Phase 1 (MVP) のテスト項目。`small` 中心、`middle/large` は正常系のみ(CLAUDE.md準拠)。

凡例: ☐ 未着手 / ◐ 着手中 / ☑ 完了

---

## 1. small テスト(単体)

### 1-1. データ/共通
| 状態 | 項目 | 期待 |
|---|---|---|
| ☐ | `UtteranceTimeline.mark()` で時刻が記録される | 同名キーで上書きされない or 規約通り上書き |
| ☐ | `Utterance` のフィールド初期化と段階的追記 | 各ステージで追加可能 |
| ☐ | `AppError` severity 別の判定 | FATAL/RECOVERABLE/SKIP/WARN が正しく分類 |
| ☐ | `ErrorHandler` の振り分け | severity に応じてダイアログ/リトライ/スキップ/警告 |
| ☐ | `ConfigStore` の保存・読込 | YAML round-trip、デフォルト値 |
| ☐ | `Logger` の jsonl 出力 | ON/OFF切替、1行=1発話の形式 |

### 1-2. バックエンド(モック化)
| 状態 | 項目 | 期待 |
|---|---|---|
| ☐ | `SoundcardCaptureBackend` のリサンプリング | 48k→16k、stereo→mono、float32化 |
| ☐ | `SileroVadBackend` の発話切り出し | 無音区切りで Utterance を返す |
| ☐ | `FasterWhisperAsrBackend` の入出力 | WAV → src_text(モデル呼び出しはモック) |
| ☐ | `Nllb200TranslatorBackend` の入出力 | src_text → tgt_text(モデル呼び出しはモック) |
| ☐ | `SapiTtsBackend` の入出力 | tgt_text → tts_pcm(エンジンはモック) |
| ☐ | `SoundcardOutputBackend` の出力 | デバイス指定が反映される(モック) |
| ☐ | `DeviceValidator`: 入力=出力で起動拒否 | 例外 or False返却 |
| ☐ | `DeviceValidator`: 入力≠出力でOK | True返却 |
| ☐ | `BackendRegistry` の登録/列挙 | 登録順・名前検索 |

### 1-3. パイプライン制御
| 状態 | 項目 | 期待 |
|---|---|---|
| ☐ | `PipelineCoordinator.start/stop` | ステージのライフサイクル呼び出し |
| ☐ | パイプライン上の Utterance フロー(モック) | 各ステージが順に呼ばれ、timelineが記録される |
| ☐ | ステージ途中での例外発生時の振り分け | SKIPは継続、FATALは停止 |

---

## 2. middle テスト(結合・正常系のみ)

| 状態 | 項目 | 期待 |
|---|---|---|
| ☐ | WAV(英語短文) → ASR → Translator → TTS → 出力WAV保存 | 期待される日本語TTSが生成される(モデル実物使用、結果のテキストは一定範囲で許容) |
| ☐ | GUI操作シミュレーション(開始 → 数発話 → 停止) | パイプラインが起動・停止し、レイテンシが記録される |
| ☐ | 設定保存→再起動→設定読込 | 値が完全に再現される |

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
| ☐ | モデルファイル未DL時の挙動 | FATAL → ダイアログ |
| ☐ | モデル初期化失敗(破損ファイル等) | FATAL → ダイアログ |
| ☐ | オーディオデバイス消失(動作中に抜線) | FATAL → 停止 + 通知 |
| ☐ | デバイス権限なし | FATAL → 起動拒否 |
| ☐ | ASR/翻訳の一時失敗(空文字、例外) | SKIP → 発話破棄、継続 |
| ☐ | レイテンシ閾値超過 | WARN → バナー、継続 |

---

## 5. 実行方法(予定)

```bash
uv run pytest                  # 全体
uv run pytest -m "not slow"    # smallのみ
uv run pytest --cov=src        # カバレッジ付き
```

`@pytest.mark.slow` で middle/large を分け、smallは常時、middle/largeは手動or任意で実行する想定。
