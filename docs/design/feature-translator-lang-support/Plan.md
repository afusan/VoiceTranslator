# feature/translator-lang-support 計画

## 目的
Translator backend ごとに対応出力言語が違う問題に対し、UI の **出力言語(tgt)
プルダウンを backend に追従** させる。後続ブランチ `feature/translator-picks`
の前提整備。`feature/asr-lang-support` と対称構造。

## 現状の課題
- `SettingsPanel._TGT_LANG_CHOICES` は固定 17 言語。Translator backend を増やすと
  「DeepL は対応してるが NLLB は未マッピング」「LLM は何でも翻訳できる」等の差を
  UI で表現できない
- 既存 `Nllb200TranslatorBackend.ISO_TO_NLLB` は 21 言語のみ(NLLB-200 本体は 200 言語対応)。
  この差をユーザに見せられていない

## 設計方針

### 1. I/F 追加(`TranslatorBackend` 基底)
```python
class TranslatorBackend(BackendBase, ABC):
    @classmethod
    @abstractmethod
    def supported_target_languages(cls) -> list[str]:
        """対応する出力言語(ISO 639-1)の名目リスト。"""

    @classmethod
    def supported_source_languages(cls) -> list[str]:
        """対応する入力言語(ISO 639-1)。
        default 実装は target と同じ(対称な backend が多いため)。
        """
        return cls.supported_target_languages()
```

- `supported_target_languages` は **abstract**(全 backend が正規に実装)
- `supported_source_languages` は default で target と同じ(対称想定)
- ASR の `supported_input_languages` と命名対称
- **クラスメソッドにする理由**: ASR と同じ(設定ダイアログを開いただけで load を走らせない)

### 2. AppController に問い合わせ口
```python
def get_supported_target_languages(self, backend_name: str) -> list[str]: ...
```
Registry 経由で backend クラスを引いてクラスメソッドを呼ぶ。未登録は空リスト。

### 3. SettingsPanel の連動
- **出力言語(tgt)プルダウンを Translator backend に応じて再構築**
- `_on_backend_change(TRANSLATOR, new_name)` の中で `_refresh_target_language_choices(new_name)` を呼ぶ
- 起動時の初期化でも同様
- 表示は `"en (English)"` 形式、内部値は `"en"`(共通言語テーブル経由)
- **`auto` は出力言語側では出さない**(翻訳の出力先が「自動」は意味を持たない)
- 既存設定値が新 backend で非対応のときの fallback:
  - 「日本語があれば日本語」を優先
  - 無ければ「英語があれば英語」
  - 両方無ければ先頭言語
  - 通知バナーで「出力言語を A → B に変更しました(<backend> が A に対応していないため)」

### 4. 既存 Nllb200 の対応
- `Nllb200TranslatorBackend.supported_target_languages` を返す
- 中身は **`ISO_TO_NLLB` を拡張**(現状 21 言語 → 主要 50 言語程度に追加)
  - NLLB-200 本体は 200 言語対応だが、UI で並べて意味があるレベル(BCP-47 のメジャー言語)に絞る
  - 追加は `<lang>_<script>` 形式で手動マッピング
- `supported_source_languages` は default(target と同じ)で OK

### 5. テストモック
- `tests/test_pipeline.py` の `FakeTranslator` も新 I/F(abstract)を満たす
- UI 連動テスト用には複数モック Translator(対応言語が違う)を用意して fallback ロジックを検証

## 着手順序
1. **I/F 追加**(`translator/backend.py` + abstract / クラスメソッド)
2. **Nllb200 の実装** + `ISO_TO_NLLB` 拡張
3. **`FakeTranslator` 等テストモックの更新**
4. **AppController に問い合わせ口追加** + small テスト
5. **SettingsPanel の連動 UI 実装** + fallback + 通知
6. **手元 GUI 確認** → コミット → 次ブランチへ

## 既存設計への影響
- `TranslatorBackend` 基底に abstract method 追加 → 既存実装(Nllb200 のみ)は要対応
- ConfigStore のキー(`languages.tgt`)自体は変更なし
- ASR 側の対応言語連動は本ブランチでは変更しない

## 対象外(後続ブランチへ)
- Translator backend の追加(`feature/translator-picks` 本体)
- 入力言語(ASR)側と Translator 側の連動(ASR で auto 検出 → 検出結果が
  Translator で非対応 → fallback、というシナリオは複雑なので分離)
- 「対応言語がインスタンス生成後に決まる backend」を入れる場合の抽象化
