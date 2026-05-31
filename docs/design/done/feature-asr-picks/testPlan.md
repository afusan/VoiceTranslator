# feature/asr-picks テスト計画

[Plan.md](Plan.md) 参照。各 backend について small / large の 2 階層で検証する。

## 共通方針
- **small**(モック / I/O なし / < 1 秒): backend クラスの内部処理。HTTP / WebSocket / モデルロードはモック
- **large**(実モデル / 実 API、手動実行): 認証情報 / モデルが揃っていれば実呼び出しで 1 件以上の結果が返ることを確認
  - **2026-05-30 方針**: `local.secrets` に token が用意された backend は必ず添える
  - `tests/test_<backend>_large.py` 命名で、token / モデル未配備なら skip

## backend 別テスト項目

### 1. openai-whisper
| 階層 | 項目 |
|---|---|
| small | モック `whisper.load_model` → backend インスタンス化、device 解決(cpu/cuda/mps fallback) |
| small | `transcribe()` がモック model.transcribe に PCM と language を正しく渡す |
| small | `supported_input_languages()` が Whisper 99 言語を返す |
| small | `supports_auto_detect()` が True |
| small | `capabilities()` が `requires_credentials=False / is_cloud=False` |
| small | `list_recommended_models()` が tiny〜large-v3 を返す |
| large | 実モデル DL 済み前提で 1 発話 transcribe → text が返る |

### 2. OpenAI Whisper API
| 階層 | 項目 |
|---|---|
| small | `credential_spec()` が `[CredentialField(key_name="api_key", secret=True)]` |
| small | `verify_credentials()` のモック HTTP 応答 200 で OK、401 で NG、ネット例外で NG |
| small | `transcribe()` のモック HTTP 応答(verbose_json)から text と language を抽出 |
| small | 25MB 超 PCM を渡したら明示エラー(FatalError) |
| small | language="auto" のときはリクエストから language 引数を省略 |
| small | `supported_input_languages()` / `supports_auto_detect()` の値検証 |
| middle | `tests/test_credential_flow.py` に OpenAI Whisper の項目追加(モック keyring + モック HTTP) |
| large | 実 API key で `verify_credentials` 成功 → backend 構築 → 1 発話 transcribe |

### 3. Google Cloud STT
| 階層 | 項目 |
|---|---|
| small | `credential_spec()` が `CredentialField(field_type="file", ...)` を含む(file_picker schema 初出) |
| small | `verify_credentials()` のモック `SpeechClient.__init__` で OK / NG が分岐 |
| small | `transcribe()` のモック `recognize` 応答から text/lang を抽出 |
| small | JSON ファイルが存在しない / 形式不正のエラーメッセージ |
| small | ISO 639-1 → BCP-47 変換(`"en"` → `"en-US"` 等)の代表ケース |
| small | `supports_auto_detect()` が False |
| middle | `tests/test_credential_flow.py` に Google STT の項目追加(file_picker パス指定) |
| middle | `LayerSettingsDialog` で `field_type="file"` のとき `tkinter.filedialog.askopenfilename` が呼ばれる(モック) |
| large | 実 JSON で `verify_credentials` 成功 → backend 構築 → 1 発話 transcribe |

### 4. Deepgram
| 階層 | 項目 |
|---|---|
| small | `credential_spec()` が `[CredentialField(key_name="api_key", secret=True)]` |
| small | `verify_credentials()` のモック HTTP 応答から OK / NG を判定 |
| small | `transcribe()` がモック `PrerecordedClient` に PCM を渡し、レスポンスから text/lang を抽出 |
| small | 接続失敗 / タイムアウトのリカバリ(RecoverableError を投げる) |
| small | `supported_input_languages()` / `supports_auto_detect()` の値検証 |
| middle | `tests/test_credential_flow.py` に Deepgram の項目追加(HTTP モック) |
| large | 実 API key で `verify_credentials` 成功 → backend 構築 → 1 発話 transcribe |

## 横断テスト
- **BackendRegistry**: 4 件全てが `register(...)` 経由で取得でき、`list_backends(LayerKind.ASR)` に並ぶ
- **layer_settings_schema**: 各 backend を選択するとそれぞれの設定項目に動的に切り替わる(`applies_when_backend`)
- **AppController 起動 gate**: cloud 3 件は credential 未設定で `MISSING_CREDENTIALS` 状態 → 開始ボタン disable
- **言語連動(親ブランチ機能の動作確認)**: ASR backend 切替で入力言語プルダウンが各 backend の `supported_input_languages` に追従し、auto 非対応の Google STT を選んだ時に auto がメニューから消えること

## file_picker schema 拡張(LayerSettingsDialog)
- 既存 `CredentialField.field_type` は `"text" / "password"` の 2 種類のみ
- 本ブランチで `"file"` を追加。LayerSettingsDialog 側で:
  - `tkinter.filedialog.askopenfilename` を開く参照ボタンを表示
  - 選択されたパスを保存(`AppController.set_credential` に絶対パス文字列を渡す)
  - パスのバリデーション(存在チェック / 拡張子: `.json` を推奨)
- small テスト: `field_type="file"` のとき参照ボタンが出る、選択結果が credential に保存される

## 既存テストへの影響
- `tests/test_credential_flow.py` の 4 backend(OpenAI Whisper / DeepL / OpenAI TTS / Anthropic Claude / AWS Transcribe / Google Cloud STT)の skip 行は、本ブランチで実装される項目について解除される
- `tests/test_pipeline*.py` は ASR I/F 不変(transcribe シグネチャ変更なし)なので影響なし
- 親ブランチ `feature/asr-lang-support` の `_FALLBACK_INPUT_LANGS` は引き続き fallback として使われる(変更不要)

## カバレッジ目標
- 各新規 backend ファイル: 80% 以上
- 全体: small で failure 0 を維持
