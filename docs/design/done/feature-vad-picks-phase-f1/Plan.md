# Phase F1: VAD バックエンド3件追加 (Plan)

`docs/design/feature-backend-mgmt/backendCandidates.html` の「2. VAD」セクションで ✓ を付けた
3 バックエンドを実装する。Phase E-2 で整備した認証フロー / 状態管理 / モデル切替を
**実機 backend で叩く**ことが目的。

## ターゲット backend

| backend          | 認証               | 形態             | 採用意図 (backendCandidates.html より) |
|------------------|--------------------|------------------|----------------------------------------|
| webrtcvad        | 不要               | ローカル軽量     | silero が動かない環境 (onnxruntime 不在等) のフォールバック。無認証ローカル最軽量枠 |
| pyannote.audio   | HuggingFace token  | ローカル重量NN   | 「重いがニューラル」軸の hw_info 適合度検証材料 |
| pvcobra          | アクセスキー       | ローカル+認証    | 「ローカル動作だが認証が要る」パターン (クラウドとは別軸) |

## 設計判断

### A) 依存パッケージは optional 化
`pyproject.toml` の `[project.optional-dependencies]` に **`vad-extra`** を追加。

- 既定 `uv sync --extra cpu` では入らない。利用者が `uv sync --extra cpu --extra vad-extra` を打つ。
- 配布方針「CPU を floor / 誰でも持っていける」は維持: 必須依存はそのまま、追加 backend は opt-in。
- 各 backend module は **遅延 import + 失敗時 FatalError**(silero と同じ流儀)。

選択理由: pyannote.audio が transformers/lightning/scipy/sklearn 等を引き連れるので、未利用者の
インストール時間とディスク使用量を圧迫しない。webrtcvad も統一して optional に寄せる。

→ pendList に「将来 webrtcvad だけ必須に上げる選択肢」を残す(silero の代替フォールバックなので
本来は必須側が筋)。

### B) backend 単体の責務 (役割声明)

各 backend クラス冒頭の docstring に 1〜2 行で役割を書く(CLAUDE.md「クラスの役割を決めて表明」)。
`docs/design/Class.md` のクラス一覧にも 1 行追加。

- `WebRtcVadBackend`: webrtcvad のフレーム判定を集約して `VadSegment` を切り出す
- `PyannoteVadBackend`: pyannote.audio の VAD pipeline をバッファ越しに駆動して `VadSegment` を返す
- `PvcobraVadBackend`: Picovoice Cobra の voice probability を閾値判定して `VadSegment` を切り出す

`VadSegment` インタフェースは silero と同一 (R-3)。状態管理は `BackendBase`(`ModelStatus`/`subscribe`)を継承。

### C) start/end 検出ロジックの共通化はしない (今回は)

webrtcvad / pvcobra は「フレームごとに 0/1 (or 確率)」を返す型なので、`連続 N フレーム発話 → start`
の検出は似た形になる。しかし:

- pyannote はバッチ向けで全く違うコードパス
- 共通化を急ぐと「N=2 でも問題ないのか」みたいな調整パラメータが backend ごとに違ってくる

→ 今回は **各 backend に内蔵**。重複が痛くなったら次回リファクタで `FrameThresholdSegmenter`
のような共通クラスに抽出する。pendList にメモ。

### D) Picovoice Cobra の認証フロー

Phase E-2 で整備した `credential_spec()` / `verify_credentials()` をそのまま使う:

- `credential_spec()` → `[CredentialField("access_key", "Picovoice Access Key", secret=True, ...)]`
- `verify_credentials({"access_key": "..."})` → 実際に `pvcobra.create()` を試して例外なら `ok=False`

ローカル backend なのに認証要る、という珍しいパターンの動作確認になる。

### E) pyannote.audio の HuggingFace token

`pyannote/voice-activity-detection` モデルは gated(ユーザ規約同意必須)。token 必須前提とする。

- capability hint: `requires_credentials=True`
- `credential_spec()` → `[CredentialField("hf_token", "HuggingFace Token", secret=True, ...)]`
- `verify_credentials({"hf_token": "..."})` → モデルをロードできるかで成否(初回は重いので、軽量
  API 呼び出しに差し替えるオプションも検討。pendList 行き)

→ `is_cloud=False`(ローカル動作)だが `requires_credentials=True`。capability の組み合わせとして
新パターン。Phase E-2 のテストで本パターンは契約として既にカバーしているが、実 backend で動かせる
ようになる(`tests/test_credential_flow.py` の Part 2 雛形を一部置き換え可能)。

### F) モデル切り替えテストの戦略

ユーザ要件:
> アプリとしては、モデルの切り替えが適切に行われるテストを忘れずに追加してください。
> （構造上適当に破棄されるならすべてのパターンを試す必要はない。）

`AppController.set_setting("backends", "vad", name)` は既存実装で:
1. `_evict_backend_locked(layer)` でキャッシュ削除 + subscribe 解除
2. `_emit_status(layer, INIT)`
3. バックグラウンドで `_safe_load_layer` を起動

→ **構造上、4 backend どれに切り替えても同じ evict 経路を通る**。
→ 代表として「silero ↔ webrtcvad ↔ pyannote ↔ pvcobra の遷移マトリクス全部」ではなく、
   **「旧 backend が解放される」「新 backend が ロードされる」「subscribe が新側に張り替わる」**
   の 3 点を 1〜2 ケースで検証すれば足りる。

具体的には:
- silero → webrtcvad → pyannote → pvcobra の 1 系列の遷移で、各時点で `_backends[VAD]` の
  型と subscription が入れ替わっていることを assert
- pvcobra に切り替えたとき、credential 未入力なら start_pipeline が gate される
  (これは `test_credential_flow.py` の既存契約で守られているが、新 backend で再現できることを
  追加 1 ケースで確認)

## 実装手順

1. `pyproject.toml` に `[project.optional-dependencies].vad-extra` を追加
2. `src/voice_translator/vad/webrtc_backend.py` 実装 + small テスト
3. `src/voice_translator/vad/pyannote_backend.py` 実装 + small テスト
4. `src/voice_translator/vad/pvcobra_backend.py` 実装 + small テスト
5. `src/voice_translator/common/backend_setup.py` に登録(config 反映)
6. `tests/test_backend_setup.py` に新 backend 登録ケース追加
7. `tests/test_vad_switching.py` を新規追加(モデル切替の構造テスト)
8. `src/voice_translator/gui/layer_settings_schema.py` の VAD レイヤに各 backend のパラメータ
   フィールドを追加
9. `docs/design/Class.md` に 3 クラス追加
10. `docs/design/pendList.md` に下記を追記:
    - 「webrtcvad を必須依存に上げるか」検討
    - pyannote の `verify_credentials` を軽量化(HF API のみで疎通確認)
    - フレーム判定系 backend (webrtcvad / pvcobra) の start/end 検出ロジック共通化検討

## テスト項目

`testPlan.md` に分離。

## 完了条件

- small テスト全部 pass
- 3 backend いずれかを本物 import → 起動できる(手元 large テストとして実機検証は別途、`uv sync
  --extra vad-extra` 後に)
- backend を GUI 詳細ダイアログから切り替えた時、認証情報フローが pvcobra/pyannote で発火する
  (UI 手動確認、Phase E-2 で既に枠組みは出来ている)
