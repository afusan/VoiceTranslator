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
