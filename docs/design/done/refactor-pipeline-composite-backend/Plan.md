# refactor/pipeline-composite-backend — 実施計画

作成: 2026-06-09(調査ベース) / 改訂: 2026-06-10(着手時。UI 3 Move リファクタ完了後の
コードベースで再調査し、設計判断を確定)。

---

## 0. 背景とゴール

### 0-1. ユーザの元リクエスト(原文)

> 1. パイプライン処理の改造
> 現在のアプリではパイイプラインを順番に実行することを前提としていますが、
> 未対応のバックエンドでは音声入力→発話データの生成まで一括でおこなったり、
> ASRと翻訳を一括で実施できるようなバックエンドの採用を視野に入れたい。
> そのために、未設定(スキップする)のパイプラインがリソースを無駄に使わないようにしたり
> もともとパイプラインAで処理したデータをバッファAに入れることを固定にしていた処理を
> バッファBの形式に整えて格納するような調整役が必要になると思っています。

### 0-2. 合意済みの抽象イメージ(2026-06-10 ユーザ確認済み)

**「固定 5 段のベルトコンベア」を「起動時に編成表を作ってから組むコンベア」に変える。**

- 調整役の判断は**起動時に一度だけ**。各 backend の申告(担当ロール・入出力形式)から
  編成表(plan)を起こし、走行中の各ステージは渡された指示書に従うだけ。
  走行中に調整役へ問い合わせる動的ルーティングは**作らない**。
  - 理由: (1) 編成を変える要因は設定だけで、設定は走行中に変わらない
    (2) 構成ミスを起動時に検出して拒否でき、走行中に突然壊れない
    (3) テストが「編成表を作る純関数」に集中でき、組合せ爆発を避けられる
- **スキップ = 編成表に載らない**こと。載らないロールはスレッドもキューも作られず、
  モデルもロードされない。text_only(TTS=none)は特例ハードコードから
  「TTS/Output が表に無い編成」の一例に格下げする。
- **既存構成のユーザ体感はゼロ差分**: 単体 backend×5 の従来構成では、組み上がる結果
  (5 スレッド / 4 キュー / エラー・リトライ・停止挙動)は改造前と同一。

### 0-3. 確定済みの関連判断

- **リトライ機構は現状維持**(RecoverableError → 指数バックオフ 3 回 → 枯渇で STOP)。
  3 連続失敗 ≒ API 側障害とみなして停止し、ユーザはローカル backend への切替でしのぐ、
  という運用が意図的設計(pendList「リトライ機構の効果検証」2026-06-10 クローズ参照)。
  改造では `_call_with_retry` を共通ワーカーループの 1 箇所に集約して移植する。
- 動作中の backend 切替・自動 restart は**スコープ外**(pendList 管理。本改造は静的編成のみ)。

---

## 1. 設計判断(本ブランチで確定)

### 1-1. Role は LayerKind と統合(新 enum を作らない)

`LayerKind` が既に 6 ロール(capture/vad/asr/translator/tts/output)を列挙し、
registry・config キー・ステータス表示の全経路で使われている。別 enum を並走させると
変換ノイズだけが増えるため、**設計用語の「ロール」= コードの `LayerKind`** とする。

### 1-2. PayloadKind を追加(キューを流れるデータ形式の名前)

`messages.py` の 4 payload 型に対応する enum + 終端を表す `NONE`:

| PayloadKind | 対応 dataclass | 意味 |
|---|---|---|
| `RAW` | `RawPayload` | 発話 PCM + 言語ヒント |
| `TRANSCRIBED` | `TranscribedPayload` | 認識テキスト + 言語 |
| `TRANSLATED` | `TranslatedPayload` | 翻訳テキスト + 言語 |
| `SYNTHESIZED` | `SynthesizedPayload` | 合成 PCM + samplerate |
| `NONE` | (なし) | 先頭の入力なし / 終端の出力なし |

### 1-3. 申告はレイヤ抽象基底の classmethod

`covers_roles()` / `consumes_payload()` / `produces_payload()` を**各レイヤ ABC**
(`AsrBackend` 等)に既定実装で置く(BackendBase には置かない — レイヤごとに自明な
既定値があるため)。複合 backend だけがオーバーライドする。

- classmethod にする理由: plan はロード前(設定変更時・起動前)に組む必要がある。
  `supported_input_languages()` と同じ判断。
- registry 登録に `backend_cls` が無い backend は**レイヤ既定の申告で fallback**
  (既存単体 backend はすべて既定値どおりなので安全)。

### 1-4. 先頭の Capture+VAD は plan 構築時に 1 ステージへ融合する

VAD より前には「発話」という単位が存在せず、ステージ間キュー(発話単位の
`PipelineMessage`)を置けない。これは構造的事実なので、plan builder が先頭の
Capture〜VAD 連続区間を常に 1 つの「入力ステージ」に融合する。
ストリーミング型複合(Capture+VAD+ASR 一括)の入口はスコープ外(将来、入力ステージの
産出 payload を申告で変えられる余地は残す)。

### 1-5. PayloadAdapter は「整流の seam」として最小実装

隣接ステージの `produces` ≠ `consumes` は **plan 構築時に起動拒否(FatalError)**。
変換を伴う整流が必要になった時に差し込める seam(Protocol + identity 実装)だけを
用意し、現時点で実変換は作らない(YAGNI。ユーザ案の「バッファ A→B の整形役」の置き場
を確保するのが目的)。

### 1-6. 複合ステージの timeline は入口・出口のみ記録

例: ASR+Translator 複合は `t_asr_start`(入口)と `t_translate`(出口)のみ。
内側の細粒度時刻は欠損とし、`ProcessTimeLogger.derive_stage_durations` /
`_push_recent_durations` の既存の欠損スキップで縮退する(UI は「-」)。

### 1-7. 吸収済みロールの扱い

- 複合 backend は**先頭ロールで registry に登録**(例: ASR+Translator 複合は
  `LayerKind.ASR` に登録)。後続ロールは plan 構築時に「吸収済み」となる。
- 吸収されたロールは active 対象から外れる: ロードされない・認証 gate の対象外・
  ステータス行に出ない(text_only の TTS/Output と同じ縮退経路)。
- SettingsPanel では吸収されたロールの行に「(〜に吸収済み)」を表示する
  (判断は `gui/logic/` の純関数、UI 実装規約どおり)。

### 1-8. 検証ターゲットの複合 backend

`faster_whisper_translate`(faster-whisper の `task=translate`)を ASR+Translator
複合として追加する。実装が軽く(既存 faster-whisper 呼び出しのタスク違い)、
追加依存ゼロで CPU floor を満たす。制約: 翻訳先は英語固定(Whisper translate の仕様)、
源言語テキストは得られる(transcribe 相当の segments は出ないため src_text は空)。

---

## 2. 改造後の構造

```
設定(backends.*)
   │ 申告を収集(registry の backend_cls → classmethod、無ければレイヤ既定)
   ▼
build_pipeline_plan(declarations, text_only)   … 純関数(common/pipeline_plan.py)
   │  - Capture〜VAD を入力ステージに融合
   │  - covers_roles の連続性・隣接 payload 型整合を検証(不正は PlanError)
   ▼
PipelinePlan(stages=[StageSpec(roles, lead, consumes, produces)], absorbed={...})
   │
   ▼
PipelineCoordinator  … plan に従いステージ数ぶんのスレッド + (ステージ数-1) 本のキューを編成
   - 入力ステージ: capture.read_chunk → vad.process → 産出 payload を先頭キューへ(専用ループ)
   - 中間/終端ステージ: 共通ワーカーループ(キュー get → ロール処理 → キュー put / 終端処理)
     ロール処理 = backend 呼び出し + ledger 計時 + dump + テキストログ(ロール別の小さな関数)
   - リトライ/STOP/SKIP/ドロップ処理は共通ループに 1 回だけ書く
   - 最終ステージが Output でない plan → 最終ステージ完了で on_text_ready + ledger.pop
     Output を含む plan → Output 完了で on_utterance_done(従来どおり)
```

標準構成での編成(改造前と同一の 5 スレッド / 4 キュー):

| ステージ | roles | consumes → produces | キュー(下流) |
|---|---|---|---|
| 入力 | Capture, VAD | NONE → RAW | captured_queue(バイト基準) |
| ASR | ASR | RAW → TRANSCRIBED | recognized_queue(件数基準) |
| 翻訳 | Translator | TRANSCRIBED → TRANSLATED | translated_queue(件数基準) |
| TTS | TTS | TRANSLATED → SYNTHESIZED | synthesized_queue(バイト基準) |
| 出力 | Output | SYNTHESIZED → NONE | — |

キュー種別は payload 形式から導出(RAW/SYNTHESIZED=バイト基準、テキスト系=件数基準)。
既存 config キー(`pipeline.captured_queue_max_bytes` 等)はそのまま対応づける。

---

## 3. 実施フェーズ

| Phase | 内容 | 挙動変更 |
|---|---|---|
| **C-1** | `PayloadKind` 追加 + 各レイヤ ABC に申告 classmethod(既定値) | なし(スキーマのみ) |
| **C-2** | `common/pipeline_plan.py`: `build_pipeline_plan` 純関数 + `PipelinePlan` + adapter seam。Coordinator 未接続 | なし(新規モジュール) |
| **C-3** | Coordinator を plan 駆動に改造(共通ワーカーループ・動的編成・text_only ハードコード撤去)+ AppController の `_active_layers` を plan 由来へ | なし(標準構成で同一編成・同一挙動をテストで固定) |
| **C-4** | `faster_whisper_translate` 複合 backend 追加 + 吸収済み UI 表示 + テスト | 複合選択時のみ新挙動 |
| **C-5** | ドキュメント回収(Architecture.html / Class.md / manual.md) | — |
| **C-6** | 認証あり(有償)複合の追加(2026-06-10 ユーザ依頼、`feature/composite-cloud-backends`): `openai_whisper_api_translate`(translations、英語固定)+ `gpt_audio_translate`(GPT 音声入力、任意言語・原文取得)。複合候補カタログ `append/compositeBackendCandidates.html` 新設 | 複合選択時のみ新挙動 |

各 Phase で pytest 全 pass を確認してからコミット。マージはユーザレビュー後。

---

## 4. リスクと対策

| リスク | 対策 |
|---|---|
| Coordinator 改造で既存パイプラインテストが大量に壊れる | コンストラクタ署名・公開 API(`is_running`/`set_languages`/`get_drop_counts`/`ledger`/`sequence`)を維持。drop 通知のステージ名・エラー stage 名は標準構成で従来文字列を再現 |
| 抽象化しすぎてテスト組合せが爆発 | 複合は C-4 の 1 種のみ。plan builder の検証は純関数 small テストに集中 |
| 複合 backend の timeline / 処理時間表示が崩れる | 欠損キーは既存の縮退(スキップ / 「-」表示)をそのまま使う(§1-6) |
| 吸収済みロールの UI が分かりにくい | SettingsPanel に「(〜に吸収済み)」表示(C-4)。文言は logic 側 + 固定文字列テスト |

## 5. スコープ外

- 動作中 backend 切替の自動 restart(pendList)
- ストリーミング型複合(Capture+VAD+ASR 一括)の実装(入口の構造だけ §1-4 で温存)
- Coordinator レベルのリトライ方針変更(§0-3 で現状維持と確定)
