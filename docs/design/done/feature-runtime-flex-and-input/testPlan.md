# feature/runtime-flex-and-input — テスト方針(メタ)

各 Phase で必要なテスト観点の **要点だけ** を集約。詳細は Phase 起票時の `feature/<name>/testPlan.md` に展開する。

---

## P1: UI セクション分割

| 観点 | 階層 | 要点 |
|---|---|---|
| 開閉永続化 | small | `ui.collapsed.{backends,devices,languages}` の 3 つが ConfigStore に書き戻される |
| 既定値 | small | 起動時、3 セクションともデフォルトは「開」 |
| マイグレーション | small | 既存の `ui.collapsed.settings_panel` キーが残っていても無視 / 破棄(後方互換は不要) |
| レンダリング(目視) | 手動 | 3 セクションの境界が分かる / 共通行(ログ出力先 / 保存ボタン)がセクション外に出る |

---

## P2: 言語の動的変更

| 観点 | 階層 | 要点 |
|---|---|---|
| Coordinator 注入 | small | `set_languages(src, tgt)` で内部値が atomic に差し替わる |
| 次発話への反映 | small | 言語差替え後、新規 `RawPayload` 作成時に新 `src_lang_hint` が乗る / Translator/TTS が新 `tgt_lang` を読む |
| 進行中発話の扱い | small | キューに既にある発話は古い hint のまま完走する(リーク無し) |
| GUI ハンドラ | small | `_on_src_lang_changed` / `_on_tgt_lang_changed` で `is_running` 中なら `set_languages_live` が呼ばれる |
| 縦通し | middle | 動作中に tgt を切り替え → 次発話以降のテキストが新言語になる |

---

## P3: 出力バックエンドなしモード

| 観点 | 階層 | 要点 |
|---|---|---|
| スレッド構成 | small | `output_mode=text_only` のとき TTS/Output スレッドが起動しない |
| バッファ解放 | small | Translator 完了で `on_text_ready` 発火 → `ledger.pop()` でレジャが空になる |
| バックエンド skip | small | TTS / Output レイヤの backend ロードが skip される(`load_models` が触らない) |
| ロード判定 | small | ControlPanel の「全 LOADED」判定が TTS/Output を除外して成立する |
| jsonl 出力 | middle | text_only でも `translations.jsonl` に 1 行追記される(audio モードと record スキーマが揃う) |
| audio モード回帰 | middle | 既存の縦通しが従来通り動く |
| 設定切替 | small | SettingsPanel で text_only に切り替えると TTS/Output 行が「(なし)」表示 |

---

## P4: 入出力デバイスの動的変更

| 観点 | 階層 | 要点 |
|---|---|---|
| 自動 restart | middle | 動作中に `_on_capture_changed`/`_on_output_changed` → stop → start が成功する |
| 通知 | small | NotificationBanner に「切り替えました」メッセージが出る |
| 失敗時 | small | 新デバイスでの start に失敗したら FATAL 経路を通る(プロセスは生存) |
| 並行変更 | small | 連続でデバイスを変えたとき restart が直列化される(同時並行 start を起こさない) |
| 静的(非動作中) | small | 動作中でなければ従来通り設定値だけ更新する(restart しない) |

---

## P5: 入力 backend のデバイス単位への分解

| 観点 | 階層 | 要点 |
|---|---|---|
| backend プルダウン | small | 「入力 backend」プルダウンに登録済み backend 名が並ぶ |
| ソース連動 | small | backend 切替で `list_sources()` が新 backend のものに更新される |
| 設定キー | small | `backends.capture` / `devices.input` の保存先は変えない |
| 既定 backend 復元 | small | `backends.capture` が未設定なら先頭 backend を選ぶ |
| ProcTap backend 未存在時 | small | `ProcTapCaptureBackend` 未登録環境でも UI が壊れない(soundcard だけが並ぶ) |

---

## 共通

- **large テスト**: P3 / P4 / P5 のいずれも実マイク/実出力に依存する範囲はあるが、**MVP 達成済みの縦通しに上乗せ** なので large は手動確認のみ(自動化不要)。
- **回帰**: 各 Phase で既存 small/middle テストが落ちないこと。Phase ごとにブランチを分けて影響範囲を抑える。
