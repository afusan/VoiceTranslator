# prePlan: レイヤーで使うバックエンドやパラメータの調整 — 実装方針

レイヤ内のバックエンド/モデル/パラメータの差し替え機能を実装する前の、論点ごとの
「問題」と「決定」を記録する作業用ドキュメント。

- 検討の議論詳細は [tmp/report11.md](../../tmp/report11.md) を参照
- 全論点が決まったら正式な `docs/design/feature-<name>/Plan.md` に Phase 別の作業計画として落とす

---

## 論点 1: 認証情報(API key 等)の保管方針

**問題**: クラウド/有償 backend を追加すると API key の保管が必要になる。`config.yaml` 平文は GitHub 公開時の事故源で NG。

**決定**:
- **保管先**: 2 段フォールバック方式を採用
  - 第一: **OS keychain**(`keyring` ライブラリ経由)
    - Win = Credential Manager、macOS = Keychain Services、Linux = Secret Service(D-Bus)
    - いずれも **OS の API を直接叩く**(独自フォーマットの暗号ファイルを置くわけではない)ので、OS 間の信頼性差は実用上問題なし
  - 第二: **平文ファイル**(`.gitignore` 必須、開発/CI 用途)
    - **`.env` という名前は使わない** — web スキャナ系の bot が標的にしている(`/.env` への直叩き等)
    - 具体名は実装時に確定(候補: `local.secrets` / `app.secrets` / `secrets.local`)
- **key 不足 / 認証失敗時の挙動**(既存挙動を一部変更):
  - **新ステータスを追加**(仮称 `MISSING_CREDENTIALS`、既存 `NOT_DOWNLOADED` とは別物として区別)
  - 認証/ロード失敗時はこのステータスを表示
  - 該当ステータスがあるレイヤが 1 つでもあれば **「開始」ボタンをマスク**(`is_loaded` の判定に組み込む)
  - **認証情報の入力は各レイヤの `LayerSettingsDialog` 内で行う**(backend の `requires_credentials=True` フラグで入力フィールドを動的に表示)
- **全レイヤのステータス可視化**:
  - **ステータス表示用のテキストボックスを追加**(エラー文を含むサマリを 1 箇所に集約)
  - レイヤ状態が変わったら、各レイヤでエラーを取り直してテキストボックスを更新するイメージ

---

## 論点 2: クラウド backend 利用時の「同意」UX

**問題**: クラウド backend に切替えると音声/テキストが外部 API に送信される。ユーザに意識させずに切替えられるのはプライバシ事故源。バックエンドは「ローカル動作」と「クラウド動作」に明確に二分されるので、`BackendCapabilities.is_cloud: bool` で判別する。

**決定**:
- **方式**: (a) 初回モーダル + (b) 常時バッジ の併用
  - 初回モーダル: 該当 backend を初めて選んだとき同意ダイアログを出す
  - 常時バッジ: クラウド backend に ☁ アイコンを SettingsPanel のプルダウン項目・状態ラベル等で常時表示
- **同意の粒度**: backend 単位(`consents.<backend>: true`)
  - 例: `consents.openai_whisper_api: true` / `consents.deepl_api: true`
- **同意取り消し UI**: 当面は作らない(別 backend に切替えれば実害なし。要望が出てから対応)
- **「今後表示しない」master switch**: `config.yaml` に `consents.suppress_dialogs: true` を追加。有効時は全クラウド backend で同意ダイアログをスキップして即切替
- **同意ダイアログ文言**: 全 backend 共通テンプレートに placeholder 差し込み方式。送信先 / 送信データ種別 / 利用規約 URL を表示
  - `BackendCapabilities` に `service_name: str | None` と `terms_url: str | None` を追加し、ダイアログから参照
- **キャンセル時の挙動**:
  - 操作を**取り消し**(set_setting を呼ばない、ConfigStore は変化なし)
  - SettingsPanel のプルダウン表示値だけを元の値に戻す(視覚的に「変更されてない」を明示)
  - 同意確認は `set_setting` を呼ぶ**前**に挟む(後から呼ぶとロールバック処理が必要になる)
  - 初回起動で「previous」が無いケースは `DEFAULT_CONFIG` の初期値に戻す

---

## 論点 3: 失敗時のリトライ・停止・観測方針

**問題**: クラウド backend のタイムアウト/接続失敗/認証エラー、ローカル backend の I/O 失敗等への対処。パイプラインは直列なので**前段失敗で下流の処理が意味を成さない**前提あり。

**決定**:
- **パイプライン停止条件**: リトライで改善しない失敗が起きたら **パイプライン全体を停止**
  - 前段が失敗した発話を後段に流しても無意味
  - 1 発話だけ SKIP して継続、はやらない(直列パイプラインの特性に従う)
- **リトライ方針**(backend ごとに違う):
  - **ネットワーク経由のテキスト系 API**(クラウド翻訳・クラウド ASR 等): 3 回まで指数バックオフでリトライ → それでも失敗なら停止
  - **それ以外**(ローカル backend、デバイス I/O、認証失敗、ロード失敗 等): リトライせず即停止
  - 判定は backend 自身が `BackendCapabilities.is_retryable_on_error: bool` 等で申告する形が綺麗
  - 既存の `ErrorHandler` の severity(RECOVERABLE / FATAL / SKIP / WARN)とどう組み合わせるかは実装時に整理
- **観測性**(エラーをユーザに見せる経路):
  - **各 backend が直近 N 件のエラーを内部に保持**(N は暫定 5〜10)
  - backend に `get_recent_errors()` のような問い合わせ口を追加
  - UI(論点 1 で決めたステータステキストボックス)が各レイヤから取得して集約表示
  - 表示には**どのレイヤ・どの backend で何が起きたか**を必ず明示
  - エラーごとにダイアログ/ポップアップは出さない(うるさいので)。**ステータス領域への追記のみ**
- **ユーザ設定の自動変更**: しない(`config.yaml` は不変)
- **自動フォールバック**: MVP では実装しない
  - 「クラウド失敗 → 黙ってローカル」は信頼性・透明性の観点で原則やらない
  - 将来どうしても必要になったら `DEFAULT_CONFIG.backends.<layer>` の初期値固定をフォールバック先とする(ユーザ設定済みの previous でも一時的な runtime override でもなく、配布既定値)
  - 当面はステータス領域からユーザが手動で別 backend に切り替える運用
