# セキュリティ認識の検討: 「外部から任意コード実行は不可」は妥当か

- **調査日**: 2026-06-10
- **対象コード**: `d0f70ef12b8926786a82d35d1ddc81b959a11b30` (branch: `refactor/ui-phase3-controller-slim`)
- **対象範囲**: `src/` 全体 + `pyproject.toml`

## 結論

**認識はおおむね妥当。** 「外部の攻撃者が、ユーザの操作なしにネットワーク越しに任意コードを実行する」経路は現状のコードに存在しない。ただし「外部からの攻撃が原理的に不可能」ではなく、**残るリスクはすべてサプライチェーン(導入物の信頼)側**にある。以下に根拠と残存リスクを整理する。

## 根拠: 受動的(inbound)攻撃面がゼロ

外部から攻撃するには「外から届く入口」が必要だが、本アプリには無い。

| 確認項目 | 結果 |
|---|---|
| 待ち受けポート(socket / bind / listen / HTTP server / WebSocket server) | **なし**。GUI デスクトップアプリで、サーバ機能を一切持たない |
| 動的コード実行(`eval` / `exec` / `pickle.load` / `os.system` / `subprocess` / `shell=True`) | **なし**(`runner_proc_list.py` の `__import__` は固定文字列で問題なし) |
| 設定ファイル読込 | `yaml.safe_load` のみ([config_store.py:215](../../src/voice_translator/common/config_store.py))。YAML 経由のオブジェクト注入は不可 |
| 認証情報 | keyring または JSON の `local.secrets`。JSON パースのみでコード実行経路なし |
| `trust_remote_code` | **未使用**。HF リポジトリ側の Python コードが実行される経路なし |
| API エンドポイント | 全 backend でモジュール定数のハードコード HTTPS(OpenAI / Anthropic / DeepL / Deepgram / ElevenLabs / HF)。設定から URL を差し替えられないため、設定改竄による接続先すり替えも不可。httpx は既定で TLS 検証あり |

入力面も「OS のオーディオストリーム(生 PCM)→ numpy 配列」であり、複雑なファイルフォーマットのパーサを外部入力に晒していない。

## 残存リスク(= 認識の but 書き)

いずれも「外部から能動的に攻撃される」のではなく「**自分が取り込むものに悪意があった場合**」の話。重要度順:

### 1. サプライチェーン(最大のリスク)
- **PyPI パッケージ**: `uv sync` で入る依存(torch / transformers / httpx / proc-tap 等)が侵害されれば、インストール時・import 時に任意コード実行になる。これはあらゆるローカルアプリ共通で、`uv.lock` によるバージョン+ハッシュ固定が現実的な緩和策(すでに運用している)。
- **HuggingFace モデル**: `from_pretrained` / `hf_hub_download` に **revision 固定がない**ため、リポジトリ(facebook/nllb-200, pyannote, piper voices 等)が侵害されると改竄ファイルを掴む。ただし:
  - `trust_remote_code` 不使用 → リポ同梱 Python は実行されない
  - `torch>=2.8` 固定 → `torch.load` は既定 `weights_only=True` で pickle 任意コード実行を遮断
  - faster-whisper(CTranslate2)/ Piper(ONNX)はデータフォーマットで、コード実行には onnxruntime 等のパーサ脆弱性が別途必要
  - → **「改竄モデル = 即コード実行」ではない**が、ゼロではない(パーサ脆弱性次第)
- silero-vad は pip パッケージ同梱モデルで DL なし(pip 側のリスクに帰着)。

### 2. 信頼できないのは「コード」ではなく「コンテンツ」
- ループバック録音は**任意のアプリ・配信の音声**を拾う。その文字起こしがクラウド翻訳(Claude/GPT backend)に渡るため、悪意ある音声によるプロンプトインジェクションは理屈上可能。ただし翻訳 backend にツール実行能力はなく、**被害は「変な訳文が出る」止まり**。コード実行には繋がらない。
- クラウド backend 利用時は音声・テキストが外部 API に出る(これは仕様であり同意ダイアログで扱っている範囲)。

### 3. ローカル前提の境界
- `local.secrets` 平文 fallback や `config.yaml` は、**同一マシン上のマルウェア**からは読み書きできる。ただしローカルが侵害された時点でアプリに関係なく全てが危険なので、「外部からのハッキング」の脅威モデルには含めなくてよい(keyring 優先の現設計で妥当)。

## この認識が崩れる条件(再評価トリガ)

今後こうなったら見直すこと:
1. **待ち受けを持つ機能を足したとき**: 例)TTS を VOICEVOX に差し替えると、VOICEVOX ENGINE が localhost に HTTP サーバを立てる。本アプリはクライアント側のままだが、localhost サーバはブラウザ経由の攻撃(DNS rebinding / CSRF)の対象になり得る。アプリ自身にリモコン API・WebSocket 等を足す場合も同様。
2. **`trust_remote_code=True` が必要なモデルを採用したとき**(HF リポジトリのコードを実行することになる)。
3. **設定で API エンドポイント URL を差し替え可能にしたとき**(接続先すり替えの口になる)。
4. **音声以外のファイル入力(動画・字幕・プラグイン等)を受け付けるようになったとき**。

## 推奨(任意・小粒)

- HF モデル取得に `revision="<commit hash>"` を固定する(NLLB / pyannote / Piper)。サプライチェーン緩和としては費用対効果が最も高い。対応しない場合は pendList に起票しておく価値あり。
- 配布(GitHub 公開)時は「uv.lock からの `uv sync` を正規手順とする」を README に明記(ロック外のバージョンを掴ませない)。
