# feature/asr-picks 動作確認手順

本ブランチで追加した 4 つの ASR backend(`openai_whisper` / `openai_whisper_api` /
`google_stt` / `deepgram`)と、共通基盤(`CredentialField.field_type="file"`)の
動作確認手順をまとめる。マージ前に手元で 1 通り通すこと。

GUI からの確認が主軸。実 API 呼び出しを伴う検証は `local.secrets` に認証情報を
書いた上で `pytest -m large` でも回せる。

---

## 0. 事前準備

```powershell
# 既定の uv 同期(MVP backend のみ動かす場合はこれだけで OK)
py -m uv sync --extra cpu     # GPU なし環境
py -m uv sync --extra cuda    # NVIDIA GPU 環境

# 検証する backend ごとに extras を追加
py -m uv sync --extra cpu --extra asr-whisper-official  # openai-whisper(公式)
py -m uv sync --extra cpu --extra asr-openai-api        # OpenAI Whisper API
py -m uv sync --extra cpu --extra asr-google-stt        # Google Cloud STT
py -m uv sync --extra cpu --extra asr-deepgram          # Deepgram
```

GUI 起動:
```powershell
py -m voice_translator
```

`local.secrets` の例(プロジェクトルート、`.gitignore` 済み):
```json
{
  "openai_whisper_api": { "api_key": "sk-..." },
  "google_stt":         { "credentials_path": "C:/path/to/sa.json" },
  "deepgram":           { "api_key": "..." }
}
```

---

## 1. openai-whisper(公式、ローカル)

### 動作確認
1. extras を入れた状態でアプリ起動
2. 設定パネルの ASR ドロップダウンで `openai_whisper` を選択
3. **入力言語プルダウンが Whisper 99 言語**(auto + en/ja/zh/… )に切り替わる
4. ASR レイヤの「設定」ボタンを開くと **「Whisper モデル(公式)」ドロップダウン**(tiny〜large-v3)が出る
5. 中央「↻ ロード」を押す → 初回はモデル DL(~ 数百 MB)、状態が `LOADED` に
6. 動作開始 → マイクから発話 → 翻訳テキストが履歴に出る

### 期待動作
- モデルロード中はステータスが `DOWNLOADING / LOADING` を経て `LOADED`
- CPU でも動作(`device=auto` で CPU/cuda/mps を自動選択)
- 言語自動検出が効く(auto 選択時)

### 既知の注意点
- faster-whisper より重い(処理時間 1.5〜2 倍が目安)。性能だけ見るなら faster-whisper を使う
- 初回 DL は `~/.cache/whisper/<model>.pt` に保存される

### 自動テスト
- small: `py -m uv run pytest tests/test_openai_whisper.py`
- large(モデル DL + 実推論): `py -m uv run pytest -m large tests/test_openai_whisper_large.py`

---

## 2. OpenAI Whisper API(クラウド)

### 動作確認
1. extras を入れた状態でアプリ起動
2. 設定パネルで ASR を `openai_whisper_api` に切り替える
3. **初回は同意ダイアログ**(クラウドへ音声を送る同意)が出る → 同意
4. **入力言語プルダウンが Whisper 99 言語**(auto + en/ja/zh/…)に切り替わる
5. ASR の「設定」ボタン → 認証情報入力ダイアログで **API key を入力 → 「テスト」で OK**
6. 中央「↻ ロード」→ 状態が `LOADED`
7. 動作開始 → 発話 → 翻訳が返る

### 期待動作
- API key 未設定なら `MISSING_CREDENTIALS` → 開始ボタン disable
- 「テスト」失敗時はメッセージが赤で表示され、設定は保存されない
- 25MB 超の発話で明示エラー(ステータスに `[起動失敗]` 等)
- 401/403 はバナーに「認証エラー」、429/5xx は自動リトライ(ログに warning)

### 既知の注意点
- API レスポンスの language は英語名(`"english"`)なので backend 内で ISO 639-1
  (`"en"`)に正規化している
- ネットワーク往復のレイテンシが乗る(発話長 + 数秒)

### 自動テスト
- small: `py -m uv run pytest tests/test_openai_whisper_api.py`
- 契約テスト: `py -m uv run pytest tests/test_credential_flow.py::TestOpenAIWhisperApiCredentials`
- large(実 API 疎通): `py -m uv run pytest -m large tests/test_openai_whisper_api_large.py`

---

## 3. Google Cloud STT(クラウド、サービスアカウント JSON)

### 動作確認
1. extras を入れた状態でアプリ起動
2. 設定パネルで ASR を `google_stt` に切り替える
3. 初回は同意ダイアログ → 同意
4. **入力言語プルダウンが Google STT 対応の主要言語**(en/ja/zh/ko/es/…)に切り替わる
   - **auto は出ない**(Google STT は detect_language が別 API、本ブランチ未対応)
5. ASR の「設定」ボタン → 認証情報入力ダイアログに **「サービスアカウント JSON」**
   フィールド + **「参照…」ボタン** が出る
6. 「参照…」を押すと **ファイル選択ダイアログ**(JSON フィルタ)が開く → 鍵 JSON を選択
7. 「テスト」→ 認証 OK
8. ASR の「設定」ボタン内に **「Google STT: default 言語(auto 時)」** フィールドあり
   (デフォルト `en`)
9. 中央「↻ ロード」→ 状態が `LOADED`
10. 動作開始 → 発話 → 翻訳が返る

### 期待動作
- JSON ファイルが存在しない / 形式不正なら「テスト」失敗
- 入力言語に `auto` を選んでも本 backend では default 言語で送られる
  (`languages.src=auto` のときは `default_language` が使われる)
- ISO 639-1 → BCP-47 変換が backend 内で完結(UI は `"ja"` を渡せば backend が `"ja-JP"` にする)

### 既知の注意点
- 「auto」を選んでも本当の言語自動検出はされない(default 言語で投げるだけ)
- Speech-to-Text API の有効化が必要(Google Cloud Console で)

### 自動テスト
- small: `py -m uv run pytest tests/test_google_stt.py`
- 契約テスト: `py -m uv run pytest tests/test_credential_flow.py::TestGoogleCloudSttCredentials`
- file_picker schema: `py -m uv run pytest tests/test_credential_field_file.py`
- large(実 API 疎通): `py -m uv run pytest -m large tests/test_google_stt_large.py`

---

## 4. Deepgram Nova-3(クラウド)

### 動作確認
1. extras を入れた状態でアプリ起動
2. 設定パネルで ASR を `deepgram` に切り替える
3. 初回は同意ダイアログ → 同意
4. **入力言語プルダウンが Nova-3 対応言語**(auto + en/ja/zh/…)に切り替わる
5. ASR の「設定」ボタン → API key を入力 → 「テスト」で OK
6. ASR 設定内に **「Deepgram: モデル」** フィールド(デフォルト `nova-3`)
7. 中央「↻ ロード」→ `LOADED`
8. 動作開始 → 発話 → 翻訳が返る

### 期待動作
- auto 選択時は detect_language=True で API に投げる(レスポンスから検出言語を取得)
- 明示言語選択時は language=ISO 639-1 を渡す
- 認証エラー(401 系)で FatalError、ネット系/5xx で RecoverableError(自動リトライ)

### 既知の注意点
- 本ブランチは **prerecorded 短期接続**(1 発話を送って同期で結果を待つ)を使用。
  WebSocket による真のストリーミング(逐次中間結果)は別ブランチ対象外
- レイテンシは prerecorded のため発話完了 + 数秒

### 自動テスト
- small: `py -m uv run pytest tests/test_deepgram.py`
- large(実 API 疎通): `py -m uv run pytest -m large tests/test_deepgram_large.py`

---

## 5. 横断確認

### 言語プルダウン連動(親ブランチ feature/asr-lang-support の機能)
- ASR backend を切り替えるたびに入力言語プルダウンが backend 対応言語に再構築される
- 直前に選んでいた言語が新 backend で非対応なら通知バナーで「入力言語を A → B に変更しました」
- 例:
  - `openai_whisper`(`auto`) → `google_stt` に切替 → `auto` 非対応なので「`auto → en` に変更」通知が出る
  - 戻すと再び `auto` が選べる

### registry / 設定の確認
- 設定ダイアログを開くだけで重い backend (whisper/torch) がロードされないこと
  (`supported_input_languages` はクラスメソッドなので未ロードで答えられる)
- 設定保存 → 中央「↻ ロード」で各 backend が evict → 再ロードされる
- 起動時に保存済みの ASR backend が復元される(言語プルダウンも追従する)

### 全体テスト
```powershell
# small すべて(failure 0 のこと)
py -m uv run pytest

# 大物のみ(認証情報・extras が揃っている backend だけ通る、それ以外は skip)
py -m uv run pytest -m large
```

---

## 6. マージ前チェックリスト
- [ ] 4 backend それぞれで上記「動作確認」を一通り実施
- [ ] 言語プルダウンの自動 fallback + 通知バナーが出る
- [ ] `py -m uv run pytest` が failure 0
- [ ] `local.secrets` を用意した backend は `-m large` で通る
- [ ] CLAUDE.md ルール準拠(マージは `--no-ff`、リモートに触らない)
