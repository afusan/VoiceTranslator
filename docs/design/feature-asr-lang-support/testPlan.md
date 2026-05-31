# feature/asr-lang-support テスト計画

[Plan.md](Plan.md) 参照。

## small テスト

### AsrBackend I/F
- `AsrBackend.supported_input_languages()` の default 実装が空リストを返す(基底動作)
- `AsrBackend.supports_auto_detect()` の default が False(基底動作)
- 既存テストの `Fake*` 系が新 I/F を満たして既存スイートが通る

### FasterWhisperAsrBackend
- `supported_input_languages()` が Whisper の対応言語(ja / en / zh 等を含む)を返す
- `supports_auto_detect()` が True
- 返却リストに重複なし / ソート規則が安定(UI 表示の安定性)

### AppController
- `get_supported_input_languages("faster_whisper")` が faster-whisper の値と一致
- `get_supported_input_languages("unknown_backend")` が空リスト(防御)
- `supports_auto_detect("faster_whisper")` が True、未登録は False

### SettingsPanel(UI ロジック層、widget は最小限のテスト)
- ASR backend 切替で `_refresh_input_language_choices(new_name)` が呼ばれること
- backend が `supports_auto_detect=True` のとき選択肢の先頭に `auto` が入る
- backend が `supports_auto_detect=False` のとき `auto` が選択肢から除外される
- 既存設定値が新 backend で非対応のときの fallback ロジック:
  - auto 対応 backend なら `auto` に戻す
  - auto 非対応なら先頭言語に戻す
  - 通知メッセージ文字列を生成する

## middle テスト
- WAV ベース E2E は ASR I/F 不変(transcribe シグネチャ変更なし)なので追加なし
- 既存テストが通ること

## large テスト
- 本ブランチでは追加しない(I/F 拡張のみで実 backend 追加は次ブランチ)

## 既存テストへの影響
- `tests/test_pipeline.py` 等の `FakeAsr` クラスが新 I/F を満たす必要あり
  - default 実装に頼るなら `Fake*` の変更は不要
  - 明示で `[]` / `False` を返すよう書き加えるかは設計判断(明示推奨、保守性のため)
- `tests/test_app_controller.py` の populated_registry fixture に「対応言語問い合わせ」項目を追加

## カバレッジ目標
- `asr/backend.py` の追加 I/F: 100%(短いので全分岐網羅可)
- `gui/settings_panel.py` の言語連動ロジック: 80%
