# feature/backend-availability 作業計画

起票: 2026-06-11 / 親: master

## 背景(ドッグフーディング相談 → tmp/report8.md)

backend の種類が増え、extras 未導入の backend を選んでロードすると Not Downloaded に
なる(実体は依存パッケージ未インストール)。ユーザ決定:
- ③ プルダウンには**導入済み backend だけを列挙する**(「(未導入)表示+無効化」案は
  ctk の項目単位 disable 非対応もあり不採用。発見性は manual の extras 案内で担保)
- ② 全部のせの**集約 extras** を用意する(起動バッチはサポートしない方針)

## 実装

### ③ 未導入 backend の非列挙
- `BackendRegistry.register(..., requires_modules=("httpx",))`: opt-in extras backend が
  必要とする import 名を登録時に宣言(base 依存のみの backend は宣言不要)。
- `BackendCatalog.is_backend_available(layer, name)`: 宣言を `importlib.util.find_spec`
  で**実 import せずに**判定。縮退規約: 宣言なし / 未登録 / 判定不能 → True
  (誤判定で隠すより、選んでロード失敗 + エラー案内に倒す)。dotted 名の親不在
  (ModuleNotFoundError)は False。
- `SettingsPanel._available_backend_names(layer)`: 候補構築時にフィルタ。
  判定失敗・全滅時は無濾過に縮退(空のプルダウンを出さない)。
- 宣言一覧(17 backend)はテストで固定(`tests/test_backend_setup.py`)。
  宣言漏れ = 未導入でも列挙される(従来挙動に縮退)、過剰宣言 = 導入済みでも隠れる。
- 選択中 backend が未導入の場合: 候補には出ないが選択値の表示は維持され、
  ロードすれば従来どおり Not Downloaded + エラー案内(挙動変更なし)。

### ② 集約 extras(全部のせプリセット)
- pyproject に自己参照 extras `full` を定義(機能系 extras 13 個の束)。
  - `py -m uv sync --extra cpu --extra full`(CPU 全部のせ)
  - `py -m uv sync --extra cuda --extra full`(CUDA 全部のせ)
- **cpu / cuda は full に含めない**: 自己参照 extras 経由で活性化すると
  `[tool.uv.sources]` の torch index 選択(+cpu / +cu126)が効かず、素の PyPI torch に
  化けることを `uv export` で確認した(`full-cuda` 案を破棄した理由)。演算系は
  従来どおり直接指定し、`uv export --extra cuda --extra full` で `torch==+cu126` に
  なることを検証済み。
- 既存の setup_all_*.bat は様子見(ユーザ判断で将来削除)のため触らない。

## 対象外
- モデル未DL(パッケージはあるがモデルキャッシュが無い)の事前判定 — ロードまで
  分からない。必要になったら cache_check 機構の拡張で対応(報告済みの +半日級)。
