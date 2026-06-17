"""gui.logic: UI 判断ロジック(純関数)パッケージ。

役割: 「現在の状態 → UI に表示すべき値」の計算だけを行う関数群を集約する。
widget / AppController / ConfigStore には触らない(P1: refactor-ui-3move)。

共通規約:
- 状態を持たない(モジュール変数は定数のみ)。入力は引数、出力は戻り値のみ
- customtkinter を import しない。依存は common 配下の純粋モジュール
  (types / languages)と標準ライブラリのみ。ただし GUI 内の純宣言モジュール
  (layer_settings_schema 等 — widget を import しない宣言的データ定義)への
  依存は許容する(deferred import で循環参照を回避)
- 例外の握りつぶしをしない(入力の正規化・縮退は View 側の責務)
"""
