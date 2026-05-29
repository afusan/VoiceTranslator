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
