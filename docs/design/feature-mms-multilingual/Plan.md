# feature-mms-multilingual 作業計画(多言語対応の拡張)

起票: 2026-06-14 / 親: master / ブランチ: `feature/mms-multilingual`

## 進捗(コンテキストをクリアしても再開できるよう、ここを更新する)

- [x] **Phase 1-a: MMS-TTS backend 本体**(2026-06-15)。`tts/mms_backend.py` を追加。
      言語単位の LRU 遅延ロード + `prefetch_language()` + `synthesize()`。`backend_setup` 登録
      (`requires_modules=("transformers",)` → 常に列挙)、extras `tts-mms`(uroman)を `full` に追加。
      small テスト(`tests/test_mms_backend.py`)+ large 実ロードテスト(`tests/test_mms_tts_large.py`、
      `facebook/mms-tts-eng` で DL→合成を通過確認済み)。対応言語は 639-1 で表現できる高信頼の
      初期集合 14 言語(`_ISO1_TO_MMS`)に限定。
- [x] **Phase 1-b: prefetch 配線**(2026-06-15)。`AppController._maybe_prefetch_tts_language()` を
      追加。`set_setting("languages","tgt",…)` の反応系(出力言語変更)と TTS レイヤのロード完了の
      2 契機で、`prefetch_language` を持つ TTS backend に対しバックグラウンドで出力言語を事前確保。
      未ロード/能力なし backend は no-op、失敗は握る(synthesize 時の同期ロードへ縮退)。
      `tests/test_app_controller.py::TestTtsLanguagePrefetch`。
- [ ] **横断課題: 言語コード 639-3 拡張**(本丸、未着手)。低資源言語(639-1 を持たない)を
      開放するため `common/languages.py` を 639-3 まで拡張し、NLLB/MMS のコードを正規化。
      これが終わると `_ISO1_TO_MMS` の縛り(14 言語)が外れアフリカ系言語が載る。
- [ ] **Phase 2: 翻訳との AND 連携**(横断課題に依存)。
- [ ] **Phase 3: 言語選択フィルタリング**(着手時に方式決定)。
- [ ] **Phase 4: ドキュメント/コマンド回りの最終確認**。

> 再開手順: この Plan と `相談記録_低資源言語対応と音声出力.md` を読む → 上の未チェック項目の
> 先頭から着手。実装の現物は `tts/mms_backend.py`(テンプレは `tts/piper_backend.py`)。

## 背景・狙い

将来このアプリを**日本語話者以外**(特にアフリカ等の低資源言語圏)でも使える形にしたい
という長期目標(相談記録: [相談記録_低資源言語対応と音声出力.md](相談記録_低資源言語対応と音声出力.md))。
現状の棚卸しで分かったこと:

- **翻訳(NLLB-200)は元から強い**。Meta が低資源言語向けに作ったモデルで、本アプリでも
  既に ~80 言語を申告済み(`translator/nllb200_backend.py: ISO_TO_NLLB`)。
- **「日本語/英語中心」に見える本当の原因は翻訳ではなく、次の 2 か所の蓋**:
  1. **TTS の対応言語が狭い**。出力言語プルダウンは「翻訳 ∩ TTS」の積
     (`gui/logic/language_choices.py: restrict_to_tts`)で作られるため、SAPI / Piper の
     狭さが積を潰している。`_TGT_LANG_CHOICES`(16)は fallback に過ぎず、UI 側に
     ハードな 16 言語上限がある訳ではない。
  2. **言語コード体系が ISO 639-1 限定**(`common/languages.py`)。NLLB/MMS は 639-1 を
     持たない言語(639-3 のみ。多くのアフリカ系言語が該当)に届くが、現状テーブルが
     それらを表現できず、申告にも UI にも載らない。

→ 改善は「**喋れる言語を増やす(TTS)**」+「**言語コード体系を 639-3 まで開く**」+
「**増えた候補を選びやすくする(フィルタ)**」の組合せ。既存の AND ロジック・申告機構・
遅延ロード基盤がそのまま土台になる。

## 関連する既存設計(土台)

- `TtsBackend`(`tts/backend.py`): `synthesize(text, tgt_lang)` + classmethod
  `supported_output_languages()`(未ロードで問い合わせ可)。MMS backend はこれを実装する。
- `PiperTtsBackend` が良いテンプレート(HF から voice を DL、`ModelStatus.DOWNLOADING`
  を出す、`tts-piper` extras の遅延 import)。**ただし Piper は構築時に 1 voice ロード**。
  MMS は**言語ごとにモデルが違う**ため「言語単位の遅延ロード」が要る(後述 Phase 1)。
- AND ロジック `restrict_to_tts` / fallback `compute_tgt_selection` は実装済み。
  MMS と NLLB が同じ ISO コードで申告すれば**そのまま積が広がる**(コード整合が鍵)。
- 集約 extras `full` / 未導入 backend の非列挙 / lazy ロード / 認証同意ダイアログは実装済みで、
  MMS 追加にそのまま乗る。

---

## Phase 1: MMS-TTS バックエンドのサポート(言語は遅延ロード)

### 目的
Meta MMS-TTS を TTS backend として 1 つ追加し、1,100+ 言語の読み上げを可能にする。
言語パックは**起動後オンデマンドで取得・ロード**する。

### スコープ
- `tts/mms_backend.py`(仮): transformers `VitsModel` ベース。**追加ライブラリは実質不要**
  (transformers/torch は base 依存)。extras は uroman 等の前処理が要れば最小限。
- **言語単位の遅延ロード**: backend 内部に `dict[lang, VitsModel]` のキャッシュを持ち、
  `from_pretrained("facebook/mms-tts-<lang>")` で未取得なら自動 DL(HF が
  `~/.cache/huggingface` にキャッシュ。「言語パックのインストール」はこの 1 行)。
  - **DL を発話スレッドで起こさない**: 会話中の初回発話で 100〜150MB の DL が走ると
    パイプラインが固まる。**出力言語の選択を契機に裏で事前確保**する配線にする
    (settings イベント → バックグラウンドで `DOWNLOADING → LOADED`)。
  - メモリ上限: 1 言語ロードで 0.5〜1GB。内部キャッシュは LRU で 1〜2 言語に制限。
- `supported_output_languages()`: MMS の対応言語のうち**言語テーブルで表現可能なもの**を返す
  (Phase の途中で言語コード拡張と整合させる。下記「横断課題」参照)。
- `backend_setup` 登録 + `requires_modules` 宣言 + 同意/ライセンス表示
  (**MMS は CC-BY-NC 4.0 = 非商用**。NLLB と同じ扱いで README/LICENSE/同意ダイアログに明示)。
- ステータス: `ModelStatus.DOWNLOADING` と `dl_size_hint` の既存枠を流用。レイヤ状態は
  言語単位に割らず、必要なら集約テキストに言語名を併記。

### 横断課題(Phase 1〜2 共通の本丸): 言語コード体系
- 現状 `common/languages.py` は ISO 639-1 限定(`auto` 含む ~110)。低資源言語の多くは
  639-1 を持たない(639-3 のみ)。**言語テーブルを 639-3 まで拡張**し、NLLB(`ISO_TO_NLLB`)・
  MMS の言語コードを 1 つの内部表現に正規化する設計を決める。
  - 決めること: 内部コードを何にするか(639-3 主体 / BCP-47 風)、表示名の出典、
    NLLB の `*_Latn` 等スクリプト付きコードとの対応、既存 639-1 設定の後方互換。
  - これが Phase 1 の `supported_output_languages` と Phase 2 の AND 整合の前提になる。

### 規模感
backend 本体 1〜2 日 + 言語コード拡張は分量次第(本丸。半日〜数日)。

### 未決
- uroman 等の前処理依存の有無(対象言語のスクリプト次第)。
- voice 品質のばらつき(低資源ほど棒読み寄り)— 受容範囲か実機確認。

---

## Phase 2: 翻訳言語の選択と Phase 1 の連携

### 目的
MMS-TTS を選んだとき、その対応言語が出力言語として選べるようにする。
**既存の「翻訳 ∩ TTS」AND 処理はそのまま**活かす(新しい仕組みは作らない)。

### スコープ
- MMS と NLLB が**同じ内部言語コードで申告**するようコードを揃える(Phase 1 の正規化に依存)。
  揃えば `restrict_to_tts` の積が自動的に広がり、UI 改修は最小で済む。
- NLLB 側の `ISO_TO_NLLB` を必要に応じて拡張(200 言語のうち MMS と重なる低資源言語を追加)。
- `_TGT_LANG_CHOICES`(fallback)と `_FALLBACK_INPUT_LANGS` の見直し(積が広がる前提で
  fallback の役割を再確認。日本語主用途の `ja > en` fallback 規則は維持か再検討)。
- 入力言語(src)側も MMS は無関係だが、ASR(Whisper)の対応言語と言語テーブル拡張の
  整合を確認(Whisper も 639-3 のみの言語がある)。

### 規模感
コード整合が取れていれば半日級(AND が既製品のため)。整合が崩れていると Phase 1 に巻き戻る。

### 未決
- 翻訳が対応するが TTS(MMS)が非対応、の言語の扱い(現状の警告縮退のままでよいか)。

---

## Phase 3: 言語選択のフィルタリング機能

### 目的
候補が 200+ に増えるとプルダウンが使い物にならないため、選びやすくする。
**詳細はこの Phase に着手してから詰める**(ユーザ方針)。

### 検討の種(着手時に詰める)
- 方式候補: 検索可能な入力付きリスト / 「よく使う言語 + その他」の 2 段 / 地域グルーピング /
  最近使った言語の記憶。
- 既存 UI 規約(判断は `gui/logic/` の純関数、widget は塗るだけ)に沿う。customtkinter の
  OptionMenu は検索非対応なので、別ウィジェット(Combobox / 専用ダイアログ)の要否を判断。
- src / tgt 双方に効かせるか、tgt のみか。

### 規模感
方式次第(未確定)。

---

## Phase 4: ドキュメント・コマンド回りの対応漏れ確認

### 目的
各 Phase でも都度直すが、横断で漏れを最終確認する(念のための掃除)。

### チェック対象
- `README.md` / `docs/manual.md`: 対応言語の説明、MMS の導入手順(extras)、非商用注意。
- `LICENSE.md`: MMS(CC-BY-NC)+ 関連ライブラリの追記。
- 集約 extras `full` / setup スクリプト相当に MMS extras を含めるか。
- `pyproject.toml` の extras 定義と `requires_modules` 宣言の整合
  (`tests/test_backend_setup.py` の宣言固定テストに追従)。
- `common/languages.py` 拡張に伴う既存テスト・golden(status_summary 等)の追従。
- 設定キー(`backends_config.mms.*` 等)の既定値補完。

---

## 横断メモ
- 配布方針(CPU floor / ローカル / 無料)に MMS は合致(CPU 動作・オフライン・追加ライブラリ最小)。
  ただし**モデルが非商用**な点は NLLB と同じ制約として扱う。
- 段階導入可能: Phase 1 だけでも「MMS を選べば喋れる言語が増える」効果は出る。
  Phase 2 で翻訳との連携が締まり、Phase 3 で実用的な選択 UX になる。
- 着手時はブランチ `feature/mms-multilingual` を切り、Phase ごとにコミット。
