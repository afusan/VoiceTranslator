# refactor/pipeline-composite-backend — テスト計画

方針: 新規はすべて small(モック / 純関数直テスト)。実モデルを要する確認のみ large。
既存パイプラインテスト(`test_pipeline_*.py`)は **C-3 の等価性の証人**としてそのまま
全 pass を維持する(書き換えない)。

## C-1: 申告スキーマ

- [ ] 各レイヤ ABC の既定申告が正しい(covers=自ロールのみ / consumes / produces が §2 の表どおり)
- [ ] PayloadKind と messages.py の payload 型の対応関数(kind_of 等)が全型をカバー

## C-2: build_pipeline_plan(純関数 small)

- [ ] 標準構成(単体×6)→ 5 ステージ / 入力ステージは (Capture, VAD) 融合 / 隣接型整合
- [ ] text_only(TTS/Output 除外)→ 3 ステージ、最終ステージ produces=TRANSLATED
- [ ] ASR+Translator 複合 → 4 ステージ、TRANSLATOR が absorbed に載る
- [ ] 複合 + text_only の併用 → 3 ステージ(入力 / ASR+Translator 融合 / なし…最終が複合)
- [ ] 隣接 payload 型不整合 → PlanError(起動拒否)
- [ ] covers_roles が非連続 / active 範囲外へはみ出す → PlanError
- [ ] absorbed ロールに別 backend が設定されていても plan は無視して成立する
- [ ] PipelinePlan.active_layers / has_role / output_mode 派生の正しさ

## C-3: Coordinator plan 駆動化(等価性)

- [ ] 既存 `test_pipeline_*.py` 全 pass(編成・挙動の後方互換の証明)
- [ ] 標準構成で生成されるキュー名・ドロップ通知のステージ名が従来文字列と一致
- [ ] text_only で TTS/Output スレッド・キューが作られない(従来テストで担保)
- [ ] AppController._active_layers が plan 由来になり、text_only の縮退が従来どおり
- [ ] 型不整合 plan で start が FatalError(起動拒否)

## C-4: 複合 backend(faster_whisper_translate)

- [ ] モック注入で: 複合 1 スレッドが RAW → TRANSLATED を直接産出し、TTS 段に流れる
- [ ] Translator レイヤの backend がロードされない(吸収)
- [ ] ledger に t_asr_start / t_translate のみ記録され、欠損縮退が働く
- [ ] 吸収済み表示の logic 純関数(文言固定テスト)+ SettingsPanel 配線 smoke
- [ ] ステータス snapshot に吸収ロール行が出ない
- [ ] large: 実 faster-whisper(small モデル)で task=translate 1 発話 → 英語テキストが返る
      (モデル DL 済み前提・手動実行のみ)

## 回帰確認

- [ ] 各 Phase コミット前: `py -m uv run pytest`(small 全件)
- [ ] C-5 完了時: `py -m uv run pytest -m middle` も実行
