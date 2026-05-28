# Plan: refactor/pipeline-5thread

Process スレッドが重すぎた問題への抜本対処。
5スレッド構成 + ステージ別データ型 + 中央管理 (UtteranceLedger) に再構築する。

調査・設計の経緯: [tmp/report8.html](../../../tmp/report8.html)、
shortcutList A-1 / Phase 5「案C」の合流。

---

## 目的

- Process スレッドの直列ボトルネックを解消(ASR/翻訳/TTS を独立スレッド化)
- データ受け渡しを「次段に必要な情報だけ」に絞る
- 経過時間・テキスト等のメタ情報を **UtteranceLedger** で中央管理し、payload を最小化
- バックエンド I/F をプリミティブ化(Utterance 依存をなくす)
- 全発話に **seq_id** を発行してログ間の対応を取れるようにする

---

## 確定事項(ユーザ承認済)

| 項目 | 決定 |
|------|------|
| スレッド構成 | Input / ASR / Translator / TTS / Output の5本 |
| キュー構成 | captured_queue(5) / recognized_queue(10) / translated_queue(10) / synthesized_queue(5) |
| キューあふれ | 最古を捨てる + WARN ログ + on_dropped 通知(現状継承) |
| payload | プリミティブ(pcm/text等) + seq_id |
| メタ管理 | UtteranceLedger(中央) で timeline/言語/テキスト/状態 を集約 |
| ログ書込 | 各段で text-log(直書き)、jsonl は最終段(Output完了)で ledger から集約 |
| バックエンド I/F | プリミティブ化(transcribe(pcm, hint)→(text, lang) 等) |
| TextLogger | write_src / write_tgt に分離 |
| 停止処理 | stop_event ベース、各段ループ先頭でチェック、新規取得しない |
| 再スタート時 | 全キュー + ledger を drain |
| 進め方 | 段階移行(R-1〜R-4)、各段で pytest 全パス維持 |
| マージ | 全 Phase 完了後に master へ `--no-ff` |

---

## スコープ

### IN
- 新規モジュール: `common/messages.py`, `common/ledger.py`, `common/sequence.py`
- 各バックエンド ABC + 実装の I/F 変更
- `pipeline.py` の 5スレッド版書き換え
- `app_controller.py` の連携更新
- `logger.py`(TextLogger を src/tgt 分離)
- 既存テスト全件の追従、新規テスト追加
- `Architecture.html` の更新
- `Class.md` / `manual.md` の更新

### OUT
- 翻訳精度の改善(別ブランチで)
- TTS 差し替え(Phase 2 で別作業)
- GUI からの動的パラメータ調整(別作業)

---

## 実装ステップ(段階移行)

### Phase R-1: 型と中央管理の追加(既存コードは変更なし)
- `common/messages.py`: `PipelineMessage` 封筒 + payload 型(RawPayload / TranscribedPayload / TranslatedPayload / SynthesizedPayload)
- `common/ledger.py`: `UtteranceLedger`(スレッドセーフ dict ベース)
- `common/sequence.py`: `SequenceGenerator`(atomic counter)
- 単体テスト
- 既存コードは無傷で pytest 全パス

### Phase R-2: バックエンド I/F のプリミティブ化
- 各 `*_backend.py` の `backend.py`(ABC)を新シグネチャに変更
  - `AsrBackend.transcribe(pcm, hint) -> (text, lang)`
  - `TranslatorBackend.translate(src_text, src_lang, tgt_lang) -> str`
  - `TtsBackend.synthesize(text, lang) -> (pcm, samplerate)`
  - `AudioOutputBackend.play(pcm, samplerate) -> None`
- 各実装(FasterWhisper / NLLB / SAPI / Soundcard 等)を新I/Fに合わせる
- 既存 `PipelineCoordinator` は当面 adapter で Utterance ベースのまま動かす
- テストはバックエンド単体ぶん新I/Fに更新

### Phase R-3: PipelineCoordinator 書き換え
- 5スレッド・4キュー・ledger 連携の本体に置き換え
- 停止シーケンス: stop_event → 各スレッドが次の get で抜ける、ledger を pop で完了報告
- 再スタート時: 全キュー drain + ledger clear
- `Utterance` クラスは「内部標準データ型」の地位を retire(後方互換が不要なら削除)
- adapter 撤去
- `app_controller.py` の連携更新(on_dropped/on_done のシグネチャに seq_id 追加など)
- TextLogger を `write_src(seq_id, text, lang)` / `write_tgt(seq_id, text, lang)` に分離
- 関連テストすべて新構成に更新

### Phase R-4: ドキュメント反映
- `Class.md` を 5スレッド版・ledger・新I/F に追従
- `manual.md` の動作説明を更新
- shortcutList の A-1 を「解消済」マーク
- pendList に「案C(完全並列化)」が解消されたことを記録

---

## 完了条件 (Definition of Done)

- [ ] R-1 完了時: 新モジュールが追加され、pytest 全パス(既存テストは影響なし)
- [ ] R-2 完了時: バックエンドが新I/F、pytest 全パス
- [ ] R-3 完了時: PipelineCoordinator が5スレッド版、停止シーケンス動作、pytest 全パス
- [ ] R-4 完了時: ドキュメント反映
- [ ] 実機で縦通し動作確認(英語 YouTube → 日本語 TTS)
- [ ] app.log / jsonl / soundsrc.txt / translated.txt すべてに seq_id 付与で対応が取れる
- [ ] 停止 → 再開 でキュー残骸を引きずらない

---

## 関連
- 設計レポート: [tmp/report8.html](../../../tmp/report8.html)
- MVP端折りリスト: [tmp/shortcutList.md](../../../tmp/shortcutList.md)(A-1 本格対処)
- 現状のレイヤ図: [Architecture.html](../Architecture.html)(本作業で更新)
- 全体タスク: [TaskList.md](../TaskList.md)
- テスト項目: [testPlan.md](testPlan.md)
