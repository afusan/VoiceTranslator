# refactor/pipeline-composite-backend — テスト計画

方針: 新規はすべて small(モック / 純関数直テスト)。実モデルを要する確認のみ large。
既存パイプラインテスト(`test_pipeline_*.py`)は **C-3 の等価性の証人**としてそのまま
全 pass を維持する(書き換えない)。

## C-1: 申告スキーマ → `tests/test_role_declarations.py` ✅

- [x] 各レイヤ ABC の既定申告が正しい(covers=自ロールのみ / consumes / produces が Plan §2 の表どおり)
- [x] PayloadKind と messages.py の payload 型の対応関数(payload_kind_of)が全型をカバー

## C-2: build_pipeline_plan(純関数 small)→ `tests/test_pipeline_plan.py` ✅

- [x] 標準構成(単体×6)→ 5 ステージ / 入力ステージは (Capture, VAD) 融合 / 隣接型整合
- [x] text_only(TTS/Output 除外)→ 3 ステージ、最終ステージ produces=TRANSLATED
- [x] ASR+Translator 複合 → 4 ステージ、TRANSLATOR が absorbed に載る
- [x] 複合 + text_only の併用 → 2 ステージ(入力 / ASR+Translator が最終)
- [x] 隣接 payload 型不整合 → PlanError(起動拒否)
- [x] covers_roles が非連続 / active 範囲外へはみ出す / 先頭不一致 → PlanError
- [x] absorbed ロールに別 backend が設定されていても plan は無視して成立する
- [x] PipelinePlan.active_layers / lead_layers / has_role / output_mode 派生の正しさ
- [x] DEFAULT_DECLARATIONS(fallback 表)とレイヤ ABC 既定申告の同期固定
- [x] select_adapter: 同形式 → 素通し / 不一致 → PlanError

## C-3: Coordinator plan 駆動化(等価性)✅

- [x] 既存 `test_pipeline_*.py` / `test_text_only_output.py` / `test_dynamic_languages.py` 全 pass
      (編成・挙動の後方互換の証明。既存テストは無修正。例外: `test_pipeline_retry.py` の
      構築ヘルパのみ — MagicMock が申告 I/F を持たない前提を明文化)
- [x] 標準構成のキュー名・ドロップ通知ステージ名・スレッド名は従来文字列を再現
      (既存 drop / エラー文脈テストが無修正で担保)
- [x] AppController._active_layers が plan 由来になり、text_only 縮退が従来どおり
- [x] 型不整合 plan で構築時 PlanError(`test_pipeline.py::TestPlanDrivenAssembly`)
- [x] plan プロパティの公開(標準 5 ステージ / absorbed 空)

## C-4: 複合 backend(faster_whisper_translate)✅

- [x] モック注入で: 複合 1 ステージが RAW → TRANSLATED を直接産出し、TTS 段に流れる
      (`tests/test_pipeline_composite.py`)
- [x] ledger に t_asr_start / t_translate のみ記録され、内側境界(t_asr / t_translate_start)は欠損
- [x] 空翻訳の破棄 + ledger リークなし
- [x] 複合 + text_only: 複合が最終ステージになり on_text_ready 発火
- [x] Translator レイヤの backend がロードされない(吸収)
      (`tests/test_composite_absorption.py`)
- [x] get_absorbed_roles / 翻訳先言語の provider 切替(複合 ⇔ 単体)
- [x] 吸収済み表示の logic 純関数(文言固定テスト: `test_logic_backend_display.py`)
- [x] ready 判定から吸収ロールを除外(`test_logic_ready_state.py`)
- [x] SettingsPanel 配線 smoke: 出力言語プルダウンが複合の対応言語(en のみ)で再構築され
      fallback 通知に複合名が入る(`test_settings_panel_lang.py`)
- [x] large: 実 faster-whisper(tiny)で task=translate → 契約どおりの 4 タプル
      (`tests/test_whisper_translate_large.py`。手元で 1 回完走確認済み)
- 取りやめ: 「ステータス snapshot に吸収ロール行が出ない」 — text_only でも TTS/Output 行は
  snapshot に出る(全 6 行固定)のが既存仕様のため、吸収ロールも同様に行は残す。
  設定パネル側の「(〜に吸収済み)」表示で区別する。

## 回帰確認

- [x] 各 Phase コミット前: `py -m uv run pytest`(small 全件 green)
- [x] C-5 完了時: `py -m uv run pytest -m middle` 実行 — middle マーカー付きテストは
      現在 0 件(階層は定義のみ)のため収集なしで終了。問題なし
