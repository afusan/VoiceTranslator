# feature/asr-picks 計画

## 目的
ASR の選択肢を 4 つ追加し、既存 backend 管理基盤(Phase A〜E)が複数 backend で
実地に回ることを確認する。
backend 横断資料は [append/backendCandidates.html](../append/backendCandidates.html)。
親ブランチ `feature/asr-lang-support` で導入した対応言語 I/F を各 backend で実装する。

## 追加対象(4 件)

| # | backend | 形態 | 認証 | auto | 新規に検証する側面 |
|---|---|---|---|---|---|
| 1 | **openai-whisper(公式)** | ローカル | 不要 | あり | PyTorch 直の Whisper(faster-whisper と並走) |
| 2 | **OpenAI Whisper API** | クラウド | API key 1 つ | あり | Phase F の本命候補(契約テスト雛形あり) |
| 3 | **Google Cloud STT** | クラウド | サービスアカウント JSON | 簡易には**なし** | file_picker schema が credential_spec で新規 |
| 4 | **Deepgram (Nova-3)** | クラウド | API key 1 つ + WebSocket | あり | ストリーミング ASR を同期 transcribe に被せる検討 |

「auto」列は `supports_auto_detect` の返り値。Google STT は detect_language API が別呼び出しで重いので本ブランチでは False(明示言語指定のみ)で実装する。

## 着手順序(複雑度の低い順)
1. **openai-whisper** — ローカル / 無認証 / 同期 transcribe(faster-whisper と並走)
2. **OpenAI Whisper API** — クラウド / 単一 API key、契約テスト雛形あり
3. **Google Cloud STT** — file_picker schema が credential_spec で初出
4. **Deepgram** — WebSocket を同期 I/F に被せる(短期接続パターン)

各 backend ごとに「実装 → small/large テスト → コミット」を 1 サイクルとし、レビューポイントを挟む。

## 各 backend の実装ポイント

### 1. openai-whisper
- `whisper.load_model(name, device)` でロード(name: tiny/base/small/medium/large-v3)
- `model.transcribe(pcm, language=...)` 同期返り(`text`, `language` を含む dict)
- `supported_input_languages` は faster-whisper と同じ Whisper 99 言語(共通定数を共有)
- `supports_auto_detect = True`
- device 解決は torch ベース(`common/device.py` の流用)
- list_recommended_models は faster-whisper と同等構成

### 2. OpenAI Whisper API
- `https://api.openai.com/v1/audio/transcriptions`(multipart, audio + model + language? + response_format=verbose_json)
- credential_spec: `[CredentialField(key_name="api_key", secret=True, ...)]`
- verify_credentials: `/v1/models` で疎通確認(401/403 → NG)
- 25MB/req 制限 → 大きい PCM は明示エラー(自動分割は今回スコープ外)
- レスポンスから `text` と `language` を取り出す
- supported_input_languages = Whisper 99 言語、`supports_auto_detect = True`

### 3. Google Cloud STT
- 認証: サービスアカウント JSON ファイルパスを `GOOGLE_APPLICATION_CREDENTIALS` で渡すパターン
- credential_spec に `CredentialField(field_type="file", ...)` を**新規追加**(本ブランチで `field_type` の選択肢として `"file"` を追加)
- LayerSettingsDialog でファイル選択ダイアログ対応(`tkinter.filedialog.askopenfilename`)
- verify_credentials: `google.cloud.speech_v1.SpeechClient` の初期化試行
- `recognize(config, audio)` 同期呼び出し
- supported_input_languages = Google STT の対応言語サブセット
- **supports_auto_detect = False**(detect_language は別 API、本ブランチでは扱わない)

### 4. Deepgram
- WebSocket は **「1 発話の PCM を短期接続で送って結果を待つ」運用** で同期 transcribe に被せる
- ライブラリ: `deepgram-sdk` の `PrerecordedClient`(同期 API)を優先(WS は将来検討)
- credential_spec: `[CredentialField(key_name="api_key", secret=True)]`
- verify_credentials: `/v1/projects` 等で疎通確認
- supported_input_languages = Nova-3 対応 36 言語、`supports_auto_detect = True`(言語自動検出オプションあり)

## 既存基盤との関係
- **BackendRegistry**: 各 backend を `register(..., capabilities=..., backend_cls=...)` で登録。cloud は `is_cloud=True` で同意ダイアログ自動表示(Phase D)
- **credential_spec / verify_credentials**: cloud 3 件は実装必須。Google は `field_type="file"` を新規追加
- **契約テスト**(`tests/test_credential_flow.py`): cloud 3 件はテンプレに沿って項目追加。既存 skip 行が解除される想定
- **large テスト方針**(2026-05-30): cloud 3 件は `local.secrets` 必須の large テストを添える
- **layer_settings_schema**: 各 backend の固有設定(モデル名、サンプリングレート等)を schema に追加
- **対応言語 I/F**: 親ブランチで追加。各 backend は `supported_input_languages()` を必ず実装

## 設計上の検討事項
- **Deepgram の同期被せ**: 既存 ASR I/F は `transcribe(pcm, src_lang_hint) → (text, lang)` 同期返り。
  Deepgram の prerecorded API はリクエストごとに PCM を渡せるので、本ブランチではそれを使う(WebSocket は次ブランチ送り)。
- **Google STT の言語コードフォーマット**: 一部 `en-US` のような BCP-47 が要求される。ISO 639-1 → BCP-47 への変換を backend 内で吸収(`"en"` → `"en-US"` 等)。
- **OpenAI API の language 引数**: `language` は ISO 639-1 で渡せる(Whisper 互換)。auto のときは省略。
- **file_picker schema**: `LayerSettingsDialog` 側でファイル選択ボタンを `field_type="file"` に応じて出す。既存 `"text" / "password"` と共存。

## ドキュメント
- 本 Plan.md / testPlan.md は本ブランチ専用フォルダに置く
- マージ後は本フォルダごと `done/feature-asr-picks/` に移動

## 対象外(後続ブランチへ)
- Deepgram の真のストリーミング ASR(逐次中間結果を ledger に流す)
- OpenAI Whisper API の自動分割(25MB 超 PCM を chunk して並列リクエスト)
- Google STT の言語自動検出(`detect_language` の 2 段呼び出し)
- Translator 側の対応言語連動
- 全 backend で「auto」を統一サポートするアプリ側言語検出層
