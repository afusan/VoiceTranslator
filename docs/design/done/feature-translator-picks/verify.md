# feature/translator-picks 動作確認手順

本ブランチで追加した 4 つの Translator 追加(NLLB-200 3.3B / DeepL API /
OpenAI GPT-4o-mini / Anthropic Claude Haiku)と、親ブランチの出力言語連動
(`feature/translator-lang-support`)の動作確認手順をまとめる。
ASR 側の `feature/asr-picks` の verify.md と同じ運用。

---

## 0. 事前準備

```powershell
# 基本(MVP のみ)
py -m uv sync --extra cpu     # GPU なし環境
py -m uv sync --extra cuda    # NVIDIA GPU 環境

# 検証する Translator backend ごとに extras を追加
py -m uv sync --extra cpu --extra translator-deepl       # DeepL API
py -m uv sync --extra cpu --extra translator-openai-api  # OpenAI GPT
py -m uv sync --extra cpu --extra translator-anthropic   # Anthropic Claude
```

GUI 起動:
```powershell
py -m voice_translator
```

`local.secrets`(プロジェクトルート、`.gitignore` 済み)に認証情報:
```json
{
  "deepl":            { "api_key": "..." },
  "openai_gpt":       { "api_key": "sk-..." },
  "anthropic_claude": { "api_key": "sk-ant-..." }
}
```

---

## 1. NLLB-200 3.3B(ローカル、既存 backend のモデル拡張)

### 動作確認
1. アプリ起動
2. 設定の Translator が `nllb200` のとき、Translator 設定ダイアログを開く
3. **「NLLB-200 モデル」ドロップダウン** に
   `distilled-600M / distilled-1.3B / 1.3B / 3.3B` の 4 つが並ぶ
4. `facebook/nllb-200-3.3B` を選択 → 中央「↻ ロード」で反映
5. 初回は ~13GB の DL → LOADED
6. 動作開始 → 翻訳が返る

### 期待動作
- `notes` に GPU 推奨が書かれている(ダイアログには別途案内なし、CPU でも動くが遅い)
- ロード中はステータスバッジが `DOWNLOADING → LOADING → LOADED`

### 既知の注意点
- 3.3B は VRAM ~10GB、CPU だと 1 発話 10 秒以上かかることもある
- 既存テストで `_RECOMMENDED_MODELS` の数が 3 → 4 に増えた点だけ要確認

---

## 2. DeepL API(クラウド、Free/Pro 自動判定)

### 動作確認
1. extras を入れて起動
2. 設定で Translator を `deepl` に切替 → 同意ダイアログ → 同意
3. **出力言語プルダウンが DeepL 対応言語**(en / ja / zh / de / fr …)に切り替わる
4. Translator の「設定」→ API key 入力 → 「テスト」で OK
   - 末尾 `:fx` の Free key と 末尾なしの Pro key を試して、メッセージで判別される
5. 中央「↻ ロード」→ LOADED
6. 動作開始 → 訳文が返る

### 期待動作
- 401/403 = 「API Key が無効」
- 456 = 「クォータ超過」(Fatal、再試行されない)
- 429/5xx = 自動リトライ
- src_lang=auto のときは DeepL に言語自動検出させる(source_lang を送らない)
- 出力言語が DeepL 非対応(例: thai)の Translator から DeepL に切替 → 「日本語に変更しました」通知

### 自動テスト
- small: `py -m uv run pytest tests/test_deepl.py`
- 契約: `py -m uv run pytest tests/test_credential_flow.py::TestDeepLApiCredentials`
- large(実 API): `py -m uv run pytest -m large tests/test_deepl_large.py`

---

## 3. OpenAI GPT-4o-mini(クラウド、LLM 翻訳)

### 動作確認
1. extras を入れて起動
2. 設定で Translator を `openai_gpt` に切替 → 同意ダイアログ → 同意
3. **出力言語プルダウンが LLM 主要言語**(en / ja / zh / fr / es …)に切り替わる
4. 「設定」→ API key 入力 → 「テスト」(OpenAI `/v1/models` で疎通)
5. 「OpenAI GPT: モデル」フィールドで `gpt-4o-mini` / `gpt-4o` 等切替可能
6. 中央「↻ ロード」→ LOADED → 動作開始

### 期待動作
- 401/403 = Fatal、429/5xx = 自動リトライ
- LLM が `"Translation: ..."` や `"訳: ..."` を付けたら backend 内で除去
- temperature=0.2 で生成の揺らぎを抑制

### 既知の注意点
- ASR の OpenAI Whisper API と **同じ API key を別保存** している
  (将来共有化は別ブランチで検討)
- LLM 翻訳は速度よりも品質寄り。レイテンシは 1〜3 秒程度

### 自動テスト
- small: `py -m uv run pytest tests/test_openai_gpt.py`
- 契約: `py -m uv run pytest tests/test_credential_flow.py::TestOpenAIGptTranslatorCredentials`
- large(実 API): `py -m uv run pytest -m large tests/test_openai_gpt_large.py`

---

## 4. Anthropic Claude Haiku(クラウド、LLM 翻訳)

### 動作確認
1. extras を入れて起動
2. 設定で Translator を `anthropic_claude` に切替 → 同意ダイアログ → 同意
3. **出力言語プルダウンが LLM 主要言語**に切り替わる
4. 「設定」→ API key 入力 → 「テスト」
   - Anthropic はモデル一覧 API が無いため、`max_tokens=1` の最小 Messages 呼び出しで疎通確認
5. 「Anthropic Claude: モデル」フィールドで `claude-haiku-4-5-20251001` 等切替可能
6. 中央「↻ ロード」→ LOADED → 動作開始

### 期待動作
- 401/403 = Fatal、429/5xx = 自動リトライ
- System prompt + user message の構造で翻訳指示
- Claude 出力に prefix が混ざる時は除去

### 自動テスト
- small: `py -m uv run pytest tests/test_anthropic_claude.py`
- 契約: `py -m uv run pytest tests/test_credential_flow.py::TestAnthropicClaudeApiCredentials`
- large(実 API): `py -m uv run pytest -m large tests/test_anthropic_claude_large.py`

---

## 5. 横断確認(親ブランチ feature/translator-lang-support の機能)

### 出力言語プルダウン連動
- Translator backend を切り替えるたびに **出力言語プルダウンが backend 対応言語に再構築**
- 直前の言語が新 backend で非対応 → 「日本語 > 英語 > 先頭」優先で自動 fallback + 通知バナー
- 例:
  - `nllb200`(対応 70+ 言語、`th` 選択中)→ `deepl` に切替 → DeepL は `th` 非対応 →
    日本語に fallback、「出力言語を th から ja に変更しました」通知
  - 戻すと `th` を選び直せる

### 全体テスト
```powershell
py -m uv run pytest                    # small すべて
py -m uv run pytest -m large           # large(認証済み backend だけ通る)
```

---

## 6. マージ前チェックリスト
- [ ] 4 backend それぞれで「動作確認」を一通り実施
- [ ] 出力言語プルダウンの自動 fallback + 通知バナーが出る
- [ ] `py -m uv run pytest` が failure 0
- [ ] `local.secrets` を用意した backend は `-m large` で通る
- [ ] CLAUDE.md ルール準拠(マージは `--no-ff`、リモートに触らない)
