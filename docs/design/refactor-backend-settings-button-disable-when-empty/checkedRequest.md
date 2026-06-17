# checkedRequest

## 目標

各バックエンドに紐づく「設定」ボタンを、そのバックエンドに**変更対象の設定項目が
存在しない場合は無効(disabled)**にする。

直前の `auto_load` 一掃により、設定項目が空になった backend(soundcard 等)が
発生している。空のダイアログを開ける状態のままだと利用者を混乱させるため、
ボタンを無効化して「設定する対象が無いことが視覚的に伝わる」状態にする。

判定は backend カタログ/スキーマから「その backend が露出している設定項目の集合」を
取って空集合かどうかで決める。ハードコードした backend 名リストでは持たない。

## スコープ

- バックエンド選択 UI(Layer ごとの選択肢/Panel)上の「設定」ボタンの enabled / disabled 制御
- enabled 判定を `gui/logic/` 配下の状態を持たない純関数として置く(`tr` 不要・モック不要)
- 言語/レイヤ切替・backend 選択変更時に判定が追随すること

## 非目標

- 設定スキーマそのものの変更(項目追加・削除)
- 「設定」ボタン以外のボタン状態(Start / ↻ 等)
- ボタンに代わるダイアログ側のエンプティ表示(無効化で十分)
- ツールチップで理由を表示するなどの拡張(必要なら後続タスク)

## 規約参照

- [CLAUDE.md](../../../CLAUDE.md) — UI 規約(判定は logic、widget は塗るだけ)
- [docs/design/Architecture.html](../../Architecture.html) §9 — UI 実装の規約
- [docs/design/Class.md](../../Class.md) — クラス役割
- [docs/design/refactor-backend-dialog-auto-load-cleanup/Plan.md](../refactor-backend-dialog-auto-load-cleanup/Plan.md) —
  直前タスクの削除範囲(設定項目空の backend がここで生まれている)

## 起点ヒント

- 直前タスクで `gui/layer_settings_schema.py` の `_auto_load_toggle` 呼び出しを 21 箇所
  削除済み。各 backend の項目集合を返す関数がここにあるはず。
- 「設定」ボタンの実装位置は `gui/` 配下の backend 選択 Panel(layer_panel など)。
- `gui/logic/` には「状態 → 表示すべき値」を計算する純関数群が既に集約されている。

## 想定される影響範囲

- `gui/logic/` に enabled 判定の純関数を追加(または既存関数の拡張)
- backend 選択 Panel が AppController からのイベントを購読して再評価
- スキーマ側の項目集合取得 API(無ければ追加)
- small テスト: logic 関数の正/負(項目あり/無し)
- i18n: 文言追加は基本不要。必要ならツールチップ等は非目標
