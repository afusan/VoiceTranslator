# refactor/pipeline-composite-backend — 計画(調査ベース)

作成: 2026-06-09。`docs/design/append/backendCandidates.html` / `AppControllerResponsibilities.html` /
`pipeline.py` / `messages.py` を読んでの整理。**まだ branch は切っていない**(着手前)。

**着手予定**: 役割分離(`refactor/roles-rebalance` 系統)が一段落してから。本計画は時間が空いてから
再着手するため、コンテキストが落ちても再開できるよう §0 に背景情報を集約している。

---

## 0. 着手再開のためのコンテキスト(2026-06-09 時点)

> **重要**: この計画は役割分離(MVVM 再構築含む)の後に着手する予定。再開時はコードベースが
> 変わっている可能性が高いので、**§3 以降の具体名は再調査前提**。意図と方針(§0〜§2)を出発点に
> 使うこと。

### 0-1. ユーザの元リクエスト(原文)

> 1. パイプライン処理の改造
> 現在のアプリではパイイプラインを順番に実行することを前提としていますが、
> 未対応のバックエンドでは音声入力→発話データの生成まで一括でおこなったり、
> ASRと翻訳を一括で実施できるようなバックエンドの採用を視野に入れたい。
> そのために、未設定（スキップする）のパイプラインがリソースを無駄に使わないようにしたり
> もともとパイプラインAで処理したデータをバッファAに入れることを固定にしていた処理を
> バッファBの形式に整えて格納するような調整役が必要になると思っています。
> （上記は案レベルであり、この対応方法が妥当かよりよい案があるかについても検証が必要。）

ユーザは「案レベル」と明言しており、**この計画の方針(ロール/ペイロード/バックエンドの 3 層分離 +
PayloadAdapter)もまだ検証段階**。再開時にはまず他の選択肢が無いかを再検討するところから。

### 0-2. ユーザが想定している複合バックエンドの例(分かっている範囲)

- **音声入力 → 発話データ生成 を一括**: ストリーミング ASR / 統合 VAD(WhisperX, SeamlessM4T 等の VAD 内蔵型)
- **ASR + 翻訳 を一括**: End-to-End Speech Translation(Whisper の `task=translate`、SeamlessM4T、Cascaded LLM 等)
- 未提示だが想定可能: TTS + Output 統合(クラウド TTS が直接スピーカに流す形)、Translator + TTS 統合

### 0-3. 現在のコードベース基準点(再着手時に grep する起点)

| ファイル | 役割 | 改造の中心か |
|---|---|---|
| `src/voice_translator/common/pipeline.py` | PipelineCoordinator(5 スレッド版) | ★ 中心 |
| `src/voice_translator/common/messages.py` | PipelineMessage + 4 種の Payload | ★ 中心 |
| `src/voice_translator/common/types.py` | LayerKind / PcmChunk / ModelStatus 等 | ★ 中心(Role / PayloadKind を足す) |
| `src/voice_translator/common/backend_base.py` | BackendBase ミックスイン | ★ 中心(covers_roles を足す) |
| `src/voice_translator/common/backend_registry.py` | BackendRegistry | 中(検索 API を拡張) |
| `src/voice_translator/common/app_controller.py` | **役割分離後は分割済みの想定**。再開時は分割後のコラボレータを確認 | 中 |
| `src/voice_translator/{layer}/backend.py` | 各レイヤの抽象基底 | 補助(default 実装で OK) |
| `tests/test_pipeline_*.py` | パイプラインの既存テスト一式 | 確認必須 |
| `docs/design/Architecture.html` §3-4 | 5 スレッド / 4 キューの図と表 | 改造時に更新 |
| `docs/design/Class.md` §2 | ステージ/キュー構成、I/F 表 | 改造時に更新 |

### 0-4. 既に決まっている前提(変更しないルール)

- **配布方針**: GPU は bonus、CPU を floor。複合バックエンドが GPU 必須な場合は **opt-in extras**
  (`uv sync --extra ...`)に入れる。default install には混ぜない。([CLAUDE.md](../../../CLAUDE.md) /
  pendList「プロジェクト配布方針」)
- **テスト戦略**: small はモックで CPU 1 本、large は手元で 1 回通す。CI に large は載せない。
- **`UtteranceLedger` の seq_id 集約は維持**: ステージ別 payload は最小化、横断メタは ledger 側。
- **`stop_event` ベースの協調停止**: 再 start で `ledger.clear()` + キュー drain は維持。

### 0-5. 既存の「スキップ縮退」前例(出発点として再利用)

`text_only` モード(`backends.tts == "none"`)で既に実装済の縮退ロジック:

- `PipelineCoordinator.__init__` で `output_mode="text_only"` のとき TTS/Output スレッドを作らない
- `AppController._active_layers()` で TTS/Output レイヤを除外
- `_check_missing_credentials_gate` / `_sync_ready_state` も `_active_layers()` を見る
- Translator スレッド末尾で `on_text_ready` + `ledger.pop` で打ち切り

→ **これを一般化して任意のロールスキップに広げる** のが本計画の最初のマイルストーン。
text_only ハードコードを撤去できる範囲で撤去する。

### 0-6. 役割分離側の影響(進行状況確認の窓口)

- 本計画着手前に [refactor/roles-rebalance](../refactor-roles-rebalance/Plan.md) が進む想定。
- 並行で MVVM 化(`AppControllerResponsibilities(MVVM).html` ベース)も検討中。
- 再開時に確認: AppController は **Facade に縮小済み or Model 群に分散済み** のはず。
  本計画の §3-4 で言う「PipelineCoordinator のステージ組み立てを動的化」は、
  役割分離後の **PipelineRunner**(Model 側のドメインオブジェクト)に置く。
- Phase P-2 の `build_pipeline_plan` 純関数化は、ViewModel から呼ぶ用途にも使えるよう
  Model 側に閉じた形で設計する(GUI 依存ゼロ)。

### 0-7. 未確認の論点(着手前に答えが要る)

[tmp/report3.md](../../../tmp/report3.md) §2-1 に列挙。要点:

- `Role` と `LayerKind` を統合するか / 並走させるか(→ 推奨: 統合)
- 複合バックエンドの timeline 欠損を CSV にどう載せるか(→ 推奨: 列固定 + 空文字)
- SettingsPanel の「(吸収済み)」UI 表記(→ MVVM 化後は ViewModel に判定ロジックを置く)
- `output_mode` 派生を plan ベースに統合するか(→ 推奨: plan に Output が含まれない = text_only)

### 0-8. 関連する保留項目(pendList)

着手時に同時に動かす可能性があるエントリ:

- 「動作中の capture / VAD / ASR / Translator / TTS backend 変更で自動 restart」
  → 静的な plan 構築が安定したあとに、動的 plan 再構築として検討
- 「Linux/Mac の process-kind 列挙」 → Capture/VAD 統合バックエンドが OS 別に来た場合に関連
- 「翻訳/LLM バックエンドの生成パラメータを設定可能にする」 → 複合 ASR+Translator backend の
  パラメータも `backends_config.<name>.*` 経路に乗せる方針が決まれば一緒に処理可能

---

## 1. ゴール

未対応バックエンドの取り込みに備え、**複数のステージを 1 つのバックエンドが束ねて行えるようにする**。
具体例:

| 想定ケース | 統合される範囲 |
|---|---|
| ストリーミング ASR(VAD 機能内蔵) | Capture(の発話区切り役)+ VAD + ASR |
| End-to-End Speech Translation(Whisper translate / SeamlessM4T 等) | ASR + Translator |
| 統合 TTS API(Output を含む) | TTS + Output(API が直接スピーカに流す形) |
| LLM の 1 ショット(ASR テキスト → 翻訳 → TTS) | Translator + TTS(将来) |

副次的に得たい性質:

- **未設定 / スキップするステージはリソースを使わない**(スレッドもキューも作らず、モデルもロードしない)。
  既に `text_only`(TTS=none)で TTS / Output を skip する縮退例があるので、その一般化。
- **「バッファ A 形式 → バッファ B 形式の整形役」**を入れて、固定された payload 型を強制しない。
  例: VAD+ASR 複合バックエンドの出力は `TranscribedPayload` だが、これを `recognized_queue` に直接乗せて
  次段 Translator に流す、という整流。

---

## 2. 現状(2026-06-09 時点)

### 2-1. パイプラインステージとデータの流れ

```
[Input(Capture+VAD)] --captured_queue(RawPayload)-->
[ASR]               --recognized_queue(TranscribedPayload)-->
[Translator]        --translated_queue(TranslatedPayload)-->
[TTS]               --synthesized_queue(SynthesizedPayload)-->
[Output]
```

- スレッド: 5 本(Input / ASR / Translator / TTS / Output)
- キュー: 4 本(`captured_queue` / `recognized_queue` / `translated_queue` / `synthesized_queue`)
- payload は `messages.py` で固定: `RawPayload / TranscribedPayload / TranslatedPayload / SynthesizedPayload`
- backend I/F もステージ別に固定: `vad.process(chunk)` / `asr.transcribe(pcm, hint)` /
  `translator.translate(src_text, src_lang, tgt_lang)` / `tts.synthesize(text, lang)` / `output.play(pcm, sr)`

### 2-2. 既にある縮退(=スキップ機構の前例)

`text_only` モード(`backends.tts == "none"`)で実装済:

- `PipelineCoordinator.__init__` で `output_mode="text_only"` のとき TTS / Output スレッドを作らない
- `AppController._active_layers()` で TTS / Output レイヤを除外
- `_load_models`、`_check_missing_credentials_gate`、`_sync_ready_state` も `_active_layers()` を見る
- Translator スレッドの末尾で `output_mode == "text_only"` なら `on_text_ready` + `ledger.pop` で打ち切り

→ **「ステージをスキップする縮退」自体は既に PipelineCoordinator 内に持っている**。
ただし「TTS/Output だけ」のハードコード分岐で、汎用化されていない。

### 2-3. 制約と前提

- `BackendBase` は状態管理・購読・エラー履歴を提供。各レイヤの抽象基底はそれを継承(`AsrBackend`, `VadBackend`, ...)。
- 配布方針: 「GPU 専用 / CPU 専用バックエンドを並列で持たない」「コードパスは1本に保つ」。
  → 統合 backend を入れた場合も、**既存の分離バックエンドと両立する 1 本の制御パスにする** ことが必要。
- 横断メタは `UtteranceLedger` が seq_id をキーに集約。タイムライン(t_capture / t_asr / ...)
  は各ステージで `mark_time` する固定スキーマ。複合バックエンドで「内側のステージ時刻」が
  得られない場合の扱いを決める必要がある。

---

## 3. 設計の方針(現時点の案)

### 3-1. キー概念: 「ロール」と「ペイロード」を分離

現状は「ステージ = レイヤ = backend = ロール」が同一視されている。

改造後は:

- **ロール(Role)**: パイプライン上の論理タスク。固定 6 種類: `Capture / VAD / ASR / Translator / TTS / Output`。
- **ペイロード(Payload)**: ステージ間で受け渡されるデータ型(現状の 4 種類 + Capture 入口の生 PCM)。
- **バックエンド(Backend)**: 「1 つ以上のロールを束ねて処理する実装」。
  - 単体バックエンド: 1 ロールだけカバー(従来通り)
  - 複合バックエンド: 連続する複数ロールをカバー(例: `{VAD, ASR}` / `{ASR, Translator}` / `{TTS, Output}`)
  - 非連続を束ねる組合せは不可とする(例: `{Capture, ASR}` で VAD 飛ばし、は対象外。スコープ外)

### 3-2. バックエンドへの追加宣言

`BackendBase`(または各レイヤ抽象基底)に以下を追加:

```python
@classmethod
def covers_roles(cls) -> tuple[Role, ...]:
    """このバックエンドがカバーするロール(連続している必要がある)。
    default は 1 ロールのみ(従来の単体 backend と互換)。
    例: SeamlessM4tBackend は (Role.ASR, Role.TRANSLATOR) を返す。
    """

@classmethod
def consumes_payload(cls) -> PayloadKind: ...
@classmethod
def produces_payload(cls) -> PayloadKind: ...
```

ロールに「最終段で出力する payload」を結びつけ、Coordinator がキューを編成できるようにする。

### 3-3. BackendRegistry の拡張

- **どのロールにも登録できる多義性は持たない**。バックエンドは「先頭ロール」で登録する。
  例: `register(Role.ASR, "seamless-m4t", ...)` で覆うのは `{Role.ASR, Role.TRANSLATOR}`。
- `covers_roles()` で後ろのロールも吸収されることを Coordinator 組立時に検出する。
- 後ろのロールに別 backend が設定されていた場合は **「(吸収済み)」表記で UI に出す**(設定ダイアログでは選べるが Start 時は無視される、または disabled に倒す)。

### 3-4. PipelineCoordinator のステージ組み立てを動的化

現状:

```python
threads = [input, asr, translator]
if mode == "audio":
    threads += [tts, output]
```

改造後:

```python
plan = build_pipeline_plan(active_backends)  # backends 設定とロール宣言からステージ計画を作る
# plan = [
#   PipelineStage(role={Capture, VAD}, backend=Soundcard+Silero, in=None, out=RawPayload),
#   PipelineStage(role={ASR, Translator}, backend=SeamlessM4t, in=RawPayload, out=TranslatedPayload),
#   PipelineStage(role={TTS}, backend=..., in=TranslatedPayload, out=SynthesizedPayload),
#   PipelineStage(role={Output}, backend=..., in=SynthesizedPayload, out=None),
# ]
for stage in plan:
    threads.append(make_thread(stage))
```

- スレッド数 = ステージ数(複合バックエンドは 1 スレッドで複数ロールを兼ねる)
- キュー数 = ステージ数 - 1
- payload 型は連続するステージで型整合を取る(後述の整流役で吸収)
- スキップロールに対応する backend はロードしない(`_active_layers()` を `plan` から導出)

### 3-5. 「バッファ整流役」(PayloadAdapter)

ステージ間で payload 型が一致しない場合に、`PipelineMessage` の payload 部を変換する小さなアダプタを差し込む。

```python
class PayloadAdapter(Protocol):
    """前段の出力 payload を次段の入力 payload に整形して渡す。
    通常は同型 → no-op、複合バックエンドが途中の payload を作らない場合のみ介在。"""
    def adapt(self, msg: PipelineMessage) -> PipelineMessage: ...
```

例:

- 複合 VAD+ASR バックエンドが直接 `TranscribedPayload` を返す → `recognized_queue` にそのまま入れる。
- 複合 ASR+Translator バックエンドが直接 `TranslatedPayload` を返す → `translated_queue` にそのまま入れる。
- 単体バックエンド同士なら adapter は no-op(現状と同じ挙動)。

→ ユーザの提案にあった「バッファ A 形式 → バッファ B 形式の整形役」はこの PayloadAdapter で表現する。

### 3-6. UtteranceLedger / timeline の扱い

複合バックエンドでは「内側のロール完了時刻」が直接取れない場合がある。

- **方針**: 複合バックエンドは **入口と出口の時刻だけ** ledger に記録する。内側の細粒度時刻は欠損として扱う。
  - 例: ASR+Translator 複合の場合、`t_asr_start` と `t_translate` だけ記録、`t_asr` / `t_translate_start` は無し。
  - `ProcessTimeLogger.derive_stage_durations` は欠損キーに対して既に「該当 layer をスキップ」する実装。
    そのまま流用できる。
- `_push_recent_durations` は同様にスキップして縮退する。

### 3-7. 既存の text_only モード との関係

- text_only(TTS=none)は **「ロール `TTS` と `Output` を含まない plan」** として再表現される。
- `output_mode` プロパティは「plan に Output が含まれるか」から派生させる(従来の `backends.tts==none` 派生は壊さず別経路でも引ける)。
- `on_text_ready` 通知のタイミング: plan の **最終ステージが Output でない場合** に最終ステージ完了時点で発火、という汎用ルールにする。
- `on_utterance_done` は plan に Output が含まれる場合のみ呼ばれる(既存挙動と同じ)。

---

## 4. やる順番(Phase 案)

### Phase P-1: 抽象の準備(コード変更は最小)

- `common/types.py` に `Role` Enum と `PayloadKind` Enum を追加(既存の LayerKind と並走、当面は併用)。
- `BackendBase` に `covers_roles()` / `consumes_payload()` / `produces_payload()` を default 実装で追加(既存単体 backend は default = 1 ロール、変更不要)。
- `BackendRegistry` に「あるロールを覆う backend を返す」検索 API を追加(従来 API はそのまま温存)。
- **コード上の挙動は変えない**。スキーマだけ整える。

### Phase P-2: PipelineCoordinator のプラン抽出

- `build_pipeline_plan(backends, mode) -> list[PipelineStage]` を Coordinator 外の純関数として切り出す。
- 単体バックエンドだけ使う既存運用では現状と同じ 5 ステージ / 4 キュー構成になることを test で固定。
- text_only モードもこの plan から派生させ、ハードコード分岐を撤去できる範囲で撤去。

### Phase P-3: PayloadAdapter の差し込み

- `PipelineStage` 間に PayloadAdapter を挟む slot を作る。既存ケースは全て no-op adapter。
- ステージ間 payload 型整合のチェック関数を入れて、未対応の組合せは起動拒否(FatalError)。

### Phase P-4: 動的スレッド/キュー組み立て

- 現在の `_input_loop` / `_asr_loop` / ... を「ロール固有の処理関数」と「共通スレッドループ」に分離。
- スレッドループは「入力キュー get → backend 呼び出し → adapter.adapt → 出力キュー put」のテンプレ。
- 複合バックエンドは「入力 → backend.process_combined(...) → 出力」を 1 ステージで行うバリエーション。

### Phase P-5: 複合バックエンドの試験投入(検証ターゲットを 1 つ選ぶ)

- 候補: `WhisperAsrTranslatorBackend`(Whisper の `translate` タスク、ASR+Translator を統合)。
  - 実装が比較的軽い(faster-whisper の task=translate を呼ぶだけ)。
  - ロール: `{Role.ASR, Role.TRANSLATOR}`、入力 `RawPayload`、出力 `TranslatedPayload`。
- 既存の Whisper backend(task=transcribe)と並列に登録して、選択可能にする。
- middle/large テストで「Translator レイヤの backend 選択が無視される」「処理時間バッファに ASR / Translator 両方の欠損が出る」等の挙動を確認。

### Phase P-6: ドキュメント更新

- `Architecture.html` 第 4 章を「ロール / ペイロード / バックエンドの 3 層」に書き直し。
- `Class.md` の I/F 表に `covers_roles()` 等を追記。
- pendList の関連エントリ(複合 TTS / 動的 backend 切替)に「P-1〜P-5 完了で前提が整う」と相互リンク。

---

## 5. リスクと対策

| リスク | 対策 |
|---|---|
| 設計を抽象化しすぎてテストの組合せが爆発する | Phase P-1 〜 P-4 までは「既存 5 ステージ全てが単体 backend」をデフォルトに固定。複合 backend は P-5 で 1 つだけ追加 |
| 既存テスト(`PipelineCoordinator` 直接呼び出し系)が大量に壊れる | P-2 で plan を切り出すときに既存コンストラクタ引数は保持。`build_pipeline_plan` は内部呼び出しのみ |
| 複合バックエンドの timeline / 処理時間表示が不正確 | 欠損キーは UI に「-」と出す方針で揃える(`_push_recent_durations` は既に欠損許容) |
| BackendRegistry の旧 API を使う GUI(SettingsPanel)で「吸収済みレイヤ」の表示が分かりにくい | P-2 完了時点で「(吸収済み)」表記を SettingsPanel に出す(Class.md 連動) |
| `AppController._active_layers()` のハードコードが各所に散らばっている | plan からの派生に統一する(Phase P-2 で同時にやる) |

---

## 6. スコープ外(別ブランチ案件)

- **動作中の backend 切替で自動 restart**: pendList「動作中の capture / VAD / ASR / Translator / TTS backend 変更で自動 restart」エントリ参照。本ブランチは静的なステージ組み立てだけを変える。
- **Capture と VAD の統合**: ストリーミング ASR の検討に近いが、Capture は OS デバイス層なので「VAD 内蔵 ASR」の入口で Capture の `read_chunk` ループを呼ぶ役を残す必要がある。Phase P-5 の 1 例として要検討だが、本計画では「ASR+Translator」を最小サンプルとして優先する。
- **`on_text_ready` の汎用化**: plan ベースの最終ステージで発火するように整える(P-2 内で対応するが、UI 側の挙動は変えない)。

---

## 7. 着手前に詰めたい点(report3.md に詳細)

- `Role` / `PayloadKind` の Enum を `LayerKind` と統合するか並走か
- 複合バックエンドの **入口 payload が `RawPayload` でない** ケース(配列的に Capture からの素の PCM が必要な場合)
- ProcessTimeLogger の出力スキーマ変更を伴うか(列の増減)
- 設定 UI で「(吸収済み)」を出すための SettingsPanel 改修範囲
