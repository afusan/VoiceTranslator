# feature/asr-lang-support 計画

## 目的
ASR backend が増えても入力言語選択肢を **backend の対応言語に合わせて自動連動** させる。
後続ブランチ `feature/asr-picks` の前提整備。

## 現状の課題
- `SettingsPanel._LANG_CHOICES` は **固定 list**(17 言語)で、どの ASR backend を選んでも同じ。
- faster-whisper(現 MVP)は 99 言語対応だが UI には 17 言語しか出ない。
- 今後追加する backend:
  - openai-whisper / Whisper API: 99 言語
  - Google Cloud STT: 100+ 言語
  - Deepgram Nova-3: 36 言語
  → UI 側で固定 list を維持すると、対応外言語を選んでエラーが出る/対応言語を見落とす両方の事故が起きる。

## 設計方針

### 1. 言語コード共通テーブル(`common/languages.py` 新設)
MVP では `SettingsPanel._LANG_CHOICES` という固定リスト 17 件と、コードのみ表示(`en`)で済ませていた。これを正規化する。

- ISO 639-1 全体のうち実用される 100 言語程度を **`LANGUAGE_NAMES: dict[str, str]`** で一元管理(`"en": "English"` 等)
- `format_language(code) -> str`: UI 表示用に `"en (English)"` を返す(コード単体表示は廃止)
- backend 側はコードリスト(`["en", "ja", ...]`)だけを返す。表示変換は UI 層で 1 箇所に集約

これで「`_LANG_CHOICES` を 17 言語固定」というハードコードは消す。

### 2. I/F 追加(`AsrBackend` 基底)
```python
class AsrBackend(BackendBase, ABC):
    @classmethod
    @abstractmethod
    def supported_input_languages(cls) -> list[str]:
        """対応する入力言語(ISO 639-1)。"auto" は含めない名目リスト。"""

    @classmethod
    def supports_auto_detect(cls) -> bool:
        """言語自動検出に対応するか(= "auto" を選ばせてよいか)。"""
        return False
```

- `supported_input_languages` は **abstract**(全 backend が正規に実装する責務)
- `supports_auto_detect` のみ default `False`(検出機能を持たない backend が普通なので)
- **クラスメソッドにする理由**: UI が backend 名から問い合わせる時点で backend をロード済みとは限らない(設定ダイアログを開いただけで load を走らせたくない)。

本ブランチで扱う 4 backend は全て「モデルやサービス仕様で対応言語が確定」しているのでクラスメソッドで十分。

### 3. AppController に問い合わせ口
```python
def get_supported_input_languages(self, backend_name: str) -> list[str]: ...
def supports_auto_detect(self, backend_name: str) -> bool: ...
```
Registry 経由で backend クラスを引いてクラスメソッドを呼ぶ。未登録 backend は空リスト / False を返す(防御)。

### 4. SettingsPanel の連動
- **入力言語プルダウンの選択肢を、現在の ASR backend に応じて再構築**
- `_on_backend_change(ASR, new_name)` の中で `_refresh_input_language_choices(new_name)` を呼ぶ
- 起動時の初期化でも同じく呼ぶ
- 表示は `"en (English)"` 形式、内部値は `"en"`(StringVar の get/set でコードのみを扱う)
- `auto` 対応 backend なら選択肢の **先頭に `auto (自動検出)`** を入れる

#### fallback と通知
既存設定値が新 backend で非対応のときの挙動:
- **auto 対応 backend なら `auto` に戻す**(自動検出させるのが安全側)
- **auto 非対応なら backend のデフォルト**(対応言語リストの先頭、または英語があれば英語)に戻す
- **必ず通知バナーで明示**: 「入力言語を `<旧>` から `<新>` に変更しました(`<backend>` が `<旧>` に対応していないため)」
- CLAUDE.md「ユーザ設定を勝手に変更しない」原則の例外扱い。「backend 切替の副作用として言語が変わる」のは UI 操作の自然な帰結なので、確認ダイアログは出さず通知のみで進める

### 5. 既存 faster-whisper の対応
- `FasterWhisperAsrBackend.supported_input_languages` を Whisper の公式リスト(99 言語、ISO 639-1)で返す
- `supports_auto_detect` は True
- 99 言語のコードは Whisper のソース(`whisper.tokenizer.LANGUAGES`)を参照して確定

### 6. テストモック
- `tests/test_pipeline.py` 等で使っている `FakeAsr` も新 I/F(abstract)を満たすよう更新
- UI 連動テスト用には複数モック ASR(auto 対応 / 非対応 / 単一言語のみ)を用意して fallback ロジックを検証

## 着手順序
1. **言語コード共通テーブル** `common/languages.py`(`LANGUAGE_NAMES` + `format_language`)
2. **AsrBackend I/F 追加**(abstract / クラスメソッド)
3. **faster-whisper の実装** + `Fake*` テストモックの更新
4. **AppController に問い合わせ口追加** + small テスト
5. **SettingsPanel の連動 UI 実装**(再構築 + 表示形式変更 + auto 配置)
6. **fallback と通知バナー連携**(設定値が非対応のときの挙動)
7. **手元 GUI 確認** → コミット → マージ

## 既存設計への影響
- `AsrBackend` 基底に default 実装つきメソッド追加 → 既存実装(faster-whisper のみ)は touched するが、I/F を満たすのみ
- ConfigStore の `languages.src` のキー自体は変更なし(値の意味は同じ ISO 639-1)
- 翻訳 backend は影響なし(出力言語は別レイヤ)

## 対象外(後続ブランチへ)
- ASR backend の追加(`feature/asr-picks` 本体)
- 出力言語(Translator)側の連動(Translator backend ごとに対応言語が違う問題)
- 表示名の日本語化(`en (English)` を `en (英語)` にする等)— 本ブランチは英語名で統一
- 「対応言語がインスタンス生成後に決まる backend」を入れる場合の抽象化(クラスメソッドでは表現できないケース)
