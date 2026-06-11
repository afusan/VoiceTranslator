# feature/translator-picks 計画

## 目的
Translator backend を 4 件追加し、既存基盤(対応言語 I/F、credential フロー、
同意ダイアログ)が複数 backend で実地に回ることを確認する。
backend 横断資料: [append/backendCandidates.html](../append/backendCandidates.html)。

## 追加対象(4 件)

| # | backend | 形態 | 認証 | 新規に検証する側面 |
|---|---|---|---|---|
| 1 | **NLLB-200 3.3B** | ローカル | 不要 | 既存 backend の大型モデル追加(GPU 推奨レベル) |
| 2 | **DeepL API** | クラウド | API key 1 つ | 日本語品質トップ。Free/Pro 両エンドポイント対応 |
| 3 | **OpenAI GPT-4o-mini** | クラウド | API key 1 つ | LLM 翻訳の代表枠。プロンプト設計の検証 |
| 4 | **Anthropic Claude (Haiku)** | クラウド | API key 1 つ | LLM 翻訳の比較対象。指示追従の安定性 |

## 着手順序
1. **NLLB-200 3.3B**(既存 `_RECOMMENDED_MODELS` に 1 行追加するだけ)
2. **DeepL API**(契約テスト雛形あり)
3. **OpenAI GPT-4o-mini**(httpx 共有、ASR の OpenAI API と同じ key を共有しうる)
4. **Anthropic Claude Haiku**(GPT と同構造、比較材料)

## 各 backend の実装ポイント

### 1. NLLB-200 3.3B
- `Nllb200TranslatorBackend._RECOMMENDED_MODELS` に `facebook/nllb-200-3.3B` を追加
- RAM ~13GB / VRAM ~10GB(GPU 必須レベル、`notes` で警告)
- 既存 `__init__(model_name=...)` で受けるので新実装は不要

### 2. DeepL API
- エンドポイント:
  - Free: `https://api-free.deepl.com/v2/translate`
  - Pro:  `https://api.deepl.com/v2/translate`
  - API key の suffix `:fx` で Free を判定する慣行を利用
- credential_spec: `[CredentialField(key_name="api_key", secret=True)]`
- verify_credentials: `/usage` エンドポイントで疎通(無料/有料を自動判定)
- DeepL 言語コードは `EN` / `JA` / `ZH-HANS` 等(大文字、一部独自)→ ISO 639-1 → DeepL 形式に変換
- `supported_target_languages`: DeepL 公式の対応言語(EN-US/EN-GB の区別はせず単一 EN として返す)

### 3. OpenAI GPT-4o-mini
- エンドポイント: `https://api.openai.com/v1/chat/completions`
- credential_spec: `[CredentialField(key_name="api_key", secret=True)]`
- verify_credentials: `/v1/models` で疎通(ASR の OpenAI Whisper API と同じ)
- プロンプト: 翻訳指示を system message に書き、user message に src_text を入れる。
  ```
  system: "You are a translator. Translate from {src_lang} to {tgt_lang}. Output ONLY the translation, no explanation."
  user:   "<src_text>"
  ```
- レスポンスから `choices[0].message.content` を取り出す
- `supported_target_languages`: 共通言語テーブルの主要言語(LLM はほぼ何でも翻訳できる)
- **API key 共有の検討**: ASR の OpenAI Whisper API と同じ key を使う可能性。本ブランチではシンプルに backend ごとに別 key(`openai_gpt_translator.api_key`)で保存し、共有は将来検討

### 4. Anthropic Claude (Haiku)
- エンドポイント: `https://api.anthropic.com/v1/messages`
- credential_spec: `[CredentialField(key_name="api_key", secret=True)]`
- verify_credentials: messages API に小さなテストメッセージを送る(モデル一覧 API は無いため)
- プロンプト: GPT と同じ構造
- model 名: `claude-haiku-4-5-20251001`(2026 時点の最新 Haiku)
- レスポンスから `content[0].text` を取り出す
- `supported_target_languages`: 共通言語テーブルの主要言語

## 既存基盤との関係
- **BackendRegistry**: capabilities 付きで register(クラウドは is_cloud=True で同意ダイアログ)
- **credential_spec / verify_credentials**: cloud 3 件で実装
- **契約テスト**: `test_credential_flow.py` の DeepL / Anthropic Claude / OpenAI(GPT 用に新規追加)
- **large テスト**: cloud 3 件は `local.secrets` に api_key 必須
- **対応言語 I/F**: 親ブランチで導入。各 backend は `supported_target_languages` を実装
- **layer_settings_schema**: 各 backend のモデル選択を追加

## 設計上の検討事項
- **DeepL の言語コード**: ISO 639-1 → DeepL 形式変換(`"en"` → `"EN"`、`"zh"` → `"ZH"` 等)
- **LLM 翻訳の品質ばらつき**: 結果が時々訳文以外の説明を含むことがある(プロンプトで明示 + 後処理で除去)
- **API key 共有**: ASR/Translator で OpenAI を両方使うとき同じ key を使いたい。本ブランチでは
  backend ごとに別 key 保存(運用シンプル)、共有は将来要件

## ドキュメント
- 本 Plan.md / testPlan.md は本ブランチ専用フォルダに置く
- マージ後は本フォルダごと `done/feature-translator-picks/` に移動
- 動作確認手順は `verify.md` に集約(ASR と同じ運用)

## 対象外(後続ブランチへ)
- DeepL Pro/Free の自動判定の精緻化(本ブランチは key suffix で判定)
- LLM 翻訳のストリーミング応答(本ブランチは同期)
- API key 共有(複数 backend で同じ key を使う運用)
- OpenAI GPT/Claude の context length / 料金監視
