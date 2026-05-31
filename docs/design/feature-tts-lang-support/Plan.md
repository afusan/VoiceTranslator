# feature/tts-lang-support 計画

## 目的
TTS backend ごとに対応する読み上げ言語が違う問題に対し、UI の **出力言語(tgt)
プルダウンに対する TTS 対応チェック + 警告通知** を実装する。後続ブランチ
`feature/tts-picks`(Piper / ElevenLabs / OpenAI TTS / Google Cloud TTS の追加)
の前提整備。`feature/asr-lang-support` と `feature/translator-lang-support` の対称構造。

## 現状の課題
- TTS は MVP では SAPI のみで、選択肢が無いため対応言語の差を扱う必要がなかった
- `feature/tts-picks` で Piper(voice ごとに対応言語が違う)/ ElevenLabs(multilingual model)
  / OpenAI TTS(主要言語のみ)/ Google Cloud TTS(豊富)が追加されると、
  「ユーザが選んだ Translator 出力言語を、現在の TTS バックエンドが読み上げられるか」
  を UI で示せないと、実行時に黙って fallback voice が走ったり空音が出たりする
- `TtsBackend` 基底に対応言語を宣言する手段が無い

## 設計方針

### 1. I/F 追加(`TtsBackend` 基底)
```python
class TtsBackend(BackendBase, ABC):
    @classmethod
    @abstractmethod
    def supported_output_languages(cls) -> list[str]:
        """対応する読み上げ言語(ISO 639-1)の名目リスト。
        - クラスメソッド: UI が backend 名から問い合わせる時点で backend を
          ロード済みとは限らない(設定ダイアログを開いただけで重い import を
          引きずらないため)
        - `"auto"` は含めない(読み上げ言語に「自動」は意味を持たない)
        - 空リストを返した場合 UI は「未知 = 警告しない」として扱う
        """
```

- ASR の `supported_input_languages` / Translator の `supported_target_languages` と命名対称
- **abstract**(全 backend が宣言する)
- SAPI のように OS 依存の場合は「保守的に Windows 標準 voice の言語(ja+en)」を宣言

### 2. AppController に問い合わせ口
```python
def get_supported_output_languages(self, backend_name: str) -> list[str]: ...
```
Registry 経由で TTS backend クラスを引いてクラスメソッドを呼ぶ。未登録 / 例外時は空リスト。

### 3. SettingsPanel の連動
ASR/Translator と異なり、TTS は「読み取り側 = 結果への制約」なので、ユーザ選択(tgt_lang)
を勝手に変えない。代わりに **警告バナーで明示**する。

呼び出し箇所:
- TTS backend 切替時 → 現在の tgt_lang が新 TTS で対応外 → warning
- tgt_lang 切替時 → 現在の TTS で対応外 → warning
- Translator backend 切替時に tgt_lang が fallback で変わった後 → 新 tgt_lang が TTS で対応外 → warning

メソッド: `_check_tts_output_lang_compatibility(*, notify_fallback: bool)`
- 内部で TTS と tgt_lang の状態を見て、対応外なら warning バナー
- `notify_fallback=False` は起動時の初期化用(起動直後にいきなりバナーを出さない)
- 警告文: 「TTS バックエンド X は読み上げ言語 Y(言語名)に対応していません。Translator 出力言語を
  変えるか、別の TTS バックエンドに切り替えてください」

**tgt_lang や TTS を勝手に変えない理由**: ASR/Translator では「言語自動 fallback は通知バナーで
例外明示」を採用したが、TTS の場合は因果関係が遠い(TTS の都合で翻訳出力を変えるのは不自然)。
警告に留めて、ユーザに選択を委ねる。

### 4. 既存 SAPI の対応
- `SapiTtsBackend.supported_output_languages()` を実装
- 中身は `["ja", "en"]` 固定(Windows 10/11 標準 voice 前提、保守的)
- SAPI には voice 列挙時に言語コードを取れないケースが多く、動的検出は信頼性が低い
- 「他言語 voice を追加インストールしている環境でも対応外と表示される」リスクは受容
  (誤った緑表示よりも保守的な警告の方が安全)

### 5. テストモック
- `tests/test_pipeline.py` の `FakeTts(TtsBackend)` に新 I/F を実装
- `tests/test_pipeline_e2e.py` の `SilentTts(TtsBackend)` に同上

## 着手順序
1. **I/F 追加**(`tts/backend.py` に abstract classmethod)
2. **SapiTtsBackend の実装**(`["ja", "en"]` 固定)
3. **テストモック(FakeTts / SilentTts)の更新**
4. **AppController に問い合わせ口追加** + small テスト
5. **SettingsPanel の警告連動実装** + テスト
6. **手元 GUI 確認** → コミット → 次ブランチへ

## 既存設計への影響
- `TtsBackend` 基底に abstract method 追加 → 既存実装(SAPI のみ)+ テストモック 2 個は要対応
- ConfigStore のキー(`languages.tgt`)自体は変更なし
- backend_setup の `register` 呼び出しは変更不要(`backend_cls` は既に渡している)
- ASR / Translator 側の言語連動は本ブランチでは変更しない

## 対象外(後続ブランチへ)
- TTS backend の追加(`feature/tts-picks` 本体)
- 音声クローニング対応(pendList [⏳保留 2026-05-31] / 別ブランチ `feature/tts-voice-cloning`)
- Translator → TTS の連動(本ブランチでは「警告」のみ。自動で TTS を切り替えたり、
  対応言語の積集合制約をかけたりはしない)
