# feature/backend-availability テスト計画

すべて small(モック/純関数)。

## BackendRegistry.requires_modules
- [x] 未宣言は空タプル(= 常に導入済み扱い)
- [x] 宣言した import 名がそのまま返る
- [x] 未登録 backend は空タプル

## BackendCatalog.is_backend_available
- [x] 宣言なし → True
- [x] 未登録 backend → True(縮退: 隠さない)
- [x] 導入済みモジュール(標準ライブラリで代用)→ True
- [x] 不在モジュール → False
- [x] dotted 名の親パッケージ不在(ModuleNotFoundError)→ False
- [x] 複数宣言のうち 1 つでも不在 → False

## backend_setup の宣言固定
- [x] opt-in 17 backend の requires_modules が遅延 import 名と一致(golden)
- [x] base 7 backend は宣言なし

## SettingsPanel._available_backend_names(shim)
- [x] 未導入 backend が候補から除外される(順序維持)
- [x] 全導入済みなら全件
- [x] catalog 判定失敗 → 無濾過に縮退
- [x] 全滅 → 無濾過に縮退(空プルダウン防止)
- [x] 未登録レイヤは空のまま(「(未登録)」fallback は呼び出し側)

## 集約 extras(uv 検証、テストコード外)
- [x] `uv lock` 解決成功
- [x] `uv export --extra cuda --extra full` → torch==+cu126 + 全機能パッケージ
- [x] `uv export --extra cpu --extra full` → torch==+cpu
- [x] (破棄根拠)自己参照に cuda を含めると torch が素の PyPI 版に化ける

## 回帰
- [x] 既存 small スイート全件 pass(1,332 件)
