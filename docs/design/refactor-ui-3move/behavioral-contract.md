# Behavioral Contract — UI リファクタリングを通して保持すべきユーザ可視のふるまい

作成: 2026-06-09(MVVM 再構築計画用)/ 2026-06-10 復帰: 停止した MVVM 計画
(`tmp/stopped-mvvm-plan/`)から `refactor-ui-3move` 用に再調整。
**実装の置き場は変わるが「ユーザから見えるふるまい」は維持する**ための契約一覧。

## 使い方

- 各 Phase の完了時、本リストの該当章(Roadmap.md §3 の対応表)を **手動チェック** する
  (各項目に対応するシナリオを実機で 1 回踏む)
- 「変える」ことが意図的に決まった項目は ❌ マーク + 該当 Phase で文言を変更
- 「自動テストで担保している」項目は ✅ マーク + 対応テストファイル名を併記
- 手で踏んで確認した項目は 🧪 + 確認日

各項目は **1 行で表せるふるまい**。長くなる場合は分割する。

---

## 1. バックエンド選択

| # | ふるまい | 関連ファイル |
|---|---|---|
| 1.1 | **TTS = (なし)** を選ぶと Output 行がグレーアウト + Output レイヤの設定ボタン disable | `settings_panel.py:_apply_tts_none_visual` |
| 1.2 | TTS = (なし) のとき認証 gate / ロード対象から TTS・Output が除外される(クラウド TTS 認証なしで Start 可) | `app_controller.py:_active_layers` |
| 1.3 | TTS = (なし) のとき Translator 完了の時点で UI 履歴に出る(音声合成なし) | `pipeline.py:_translator_loop` text_only 分岐 |
| 1.4 | クラウド backend(`is_cloud=True`)を選ぶと ConsentDialog が出る(初回 / 未同意のとき) | `settings_panel.py:_gate_cloud_consent` |
| 1.5 | キャンセルすると元の選択に戻る(設定値も変えない) | 同上 |
| 1.6 | ASR backend を切り替えると入力言語プルダウンが新 backend の対応言語に再構築される | `settings_panel.py:_refresh_input_language_choices` |
| 1.7 | 現在の入力言語が新 ASR で非対応のときは自動 fallback(`auto` 対応 → auto、非対応 → 先頭) + 通知バナーで明示 | 同上 + `_notify_lang_fallback` |
| 1.8 | Translator backend を切り替えると出力言語プルダウンが再構築される | `settings_panel.py:_refresh_target_language_choices` |
| 1.9 | 現在の出力言語が新 Translator で非対応のときは ja > en > 先頭の順で fallback + 通知バナー | 同上 |
| 1.10 | TTS backend が現在の tgt 言語に非対応のとき警告バナーを出す(**ユーザ選択は変更しない**) | `settings_panel.py:_check_tts_output_lang_compatibility` |
| 1.11 | CAPTURE backend を切り替えると `capture_kind` に応じて入力 UI が切り替わる(プルダウン ↔ プロセス選択ボタン) | `settings_panel.py:_refresh_capture_sources_dropdown` |

---

## 2. モデルステータス表示

| # | ふるまい | 関連ファイル |
|---|---|---|
| 2.1 | アプリ起動直後、全レイヤのステータスは **INIT**(gray) | `app_controller.py:get_all_model_statuses` 初期値 |
| 2.2 | ↻ ロード押下で各レイヤが `INIT → (DOWNLOADING) → LOADING → LOADED` の遷移をする(色: gray → amber → amber → green) | `app_controller.py:_load_layer_locked` |
| 2.3 | キャッシュ済モデルは DOWNLOADING をスキップして LOADING → LOADED | backend 側 |
| 2.4 | クラウド backend で認証情報が無い / verified=False のとき **MISSING_CREDENTIALS**(red) | backend 側 + `app_controller.py:_check_missing_credentials_gate` |
| 2.5 | DOWNLOADING 中はサイズヒント `(~XGB)` を併記 | `app_controller.py:_dl_size_hint` |
| 2.6 | LOADED 状態のとき、device 概念を持つレイヤ(ASR/Translator)は `Loaded (cuda)` のように device 名を併記 | `settings_panel.py:_format_status_text` |
| 2.7 | ロード失敗時は **NOT_DOWNLOADED**(red) で停止 | `app_controller.py:_load_layer_locked` except 分岐 |
| 2.8 | レイヤ状態変化は複数 UI コンポーネント(SettingsPanel / ControlPanel)に通知される(multi-listener) | `app_controller.py:add_status_listener` |

---

## 3. Start / Stop / 動作中

| # | ふるまい | 関連ファイル |
|---|---|---|
| 3.1 | 全レイヤ準備完了で「▶ 開始」(緑相当の normal) | `control_panel.py:_sync_ready_state` |
| 3.2 | MISSING_CREDENTIALS のレイヤがあると **「認証情報未設定」(disable)** | 同上 |
| 3.3 | DOWNLOADING 中は **「モデル DL 中…」(disable)** | 同上 |
| 3.4 | PROCESS kind capture + PID 未選択は **「プロセス未選択」(disable)** | 同上 |
| 3.5 | INIT / NOT_DOWNLOADED 残存時は **「停止中(押下時にロードします)」表示** + ボタンは normal | 同上 |
| 3.6 | LOADING 残存時は **「停止中(ロード中)」表示** + ボタンは normal | 同上 |
| 3.7 | Start 押下 → DeviceValidator + 認証 gate を同期で先行検証(失敗時は即時例外) | `app_controller.py:start_pipeline_async` |
| 3.8 | バックグラウンドでロード(未ロード分)→ Coordinator 起動 → `on_started` で UI 反映 | 同上 |
| 3.9 | 動作中は「■ 停止」(normal) + 中央ロードボタン / 出力テストボタンは「(動作中)」で disable | `control_panel.py:_apply_loader_started` |
| 3.10 | Stop 押下 → 「停止中…」(disable) → 完了で「停止中」(idle)に戻る | `control_panel.py:_do_stop` |
| 3.11 | 動作中に入出力デバイスを変えると自動 restart + 「再開中…」バナー | `settings_panel.py:_trigger_device_restart` ※ **P2 で意図的変更予定**(§13.6) |
| 3.12 | 動作中に言語設定を変えると **次の発話から反映**(キュー内の発話は古い言語のまま) | `app_controller.py:set_setting` + `pipeline.py:set_languages` |
| 3.13 | 起動失敗 / 致命的エラーは NotificationBanner + status_label + status textbox の **3 段表示** | `control_panel.py:_do_start_async` 失敗分岐 |

---

## 4. 翻訳結果の表示

| # | ふるまい | 関連ファイル |
|---|---|---|
| 4.1 | 翻訳完了で履歴ボックスに `#seq [src → tgt] src_text \n   → tgt_text` 形式で表示 | `control_panel.py:_apply_text_ready` |
| 4.2 | audio モード: TTS 完了の時点(音より前)で履歴表示 | `pipeline.py:_tts_loop` 内 `on_text_ready` |
| 4.3 | text_only モード: Translator 完了の時点で履歴表示(ledger も同時に pop) | `pipeline.py:_translator_loop` text_only 分岐 |
| 4.4 | 履歴は 50 行で打ち切り、新着で末尾に追加 + スクロール末尾追従 | `control_panel.py:_append_history` |
| 4.5 | クリアボタンで履歴を空にできる | `control_panel.py:_on_clear_history` |

---

## 5. レイテンシ表示

| # | ふるまい | 関連ファイル |
|---|---|---|
| 5.1 | 平均レイテンシは `t_vad_end → t_playback_start` の区間で計算(発話自体の長さは含めない) | `control_panel.py:_apply_utterance` |
| 5.2 | 直近 10 件の移動平均、表示は「平均レイテンシ: 1.20 秒(直近 N 件)」 | 同上 |
| 5.3 | text_only モードでは t_playback_start が無いので平均レイテンシは更新されない(現状の挙動) | 同上 |
| 5.4 | 履歴クリアで平均レイテンシ表示もリセット(`平均レイテンシ: -`) | `control_panel.py:_on_clear_history` |

---

## 6. アクセラレータ表示

| # | ふるまい | 関連ファイル |
|---|---|---|
| 6.1 | いずれかのレイヤが GPU(cuda / mps)報告中なら **「演算: GPU (cuda)」(緑)** | `control_panel.py:_refresh_accel_label` |
| 6.2 | 全レイヤが CPU 報告のとき **「演算: CPU のみ」(琥珀)** | 同上 |
| 6.3 | レイヤがまだロードされていないとき **「演算: -(モデル準備中)」(slate)** | 同上 |
| 6.4 | text_only モードでは TTS / Output レイヤの device 報告は無視 | 同上 |

---

## 7. ステータス集約テキスト

| # | ふるまい | 関連ファイル |
|---|---|---|
| 7.1 | 各レイヤの現状態(色付き)+ 直近 backend エラー(最大 5 件)を 1 ブロックで表示 | `app_controller.py:get_status_summary` ※ P1 で整形は `gui/logic/status_summary.py` へ移動(**表示文字列は不変**) |
| 7.2 | GUI 操作起源イベント(起動失敗 / 致命的エラー / 出力テスト結果等)を「操作イベント」セクションに新しい順 5 件 | `control_panel.py:_append_status_event` |
| 7.3 | 「操作イベントをクリア」ボタンで GUI 操作イベントのみクリア(backend エラーは残る = 次回 refresh で復活) | `control_panel.py:_on_clear_status_events` |
| 7.4 | セクションは折り畳み可能 + 開閉状態は ConfigStore に永続化 | `control_panel.py:_on_status_toggle` |

---

## 8. 認証フロー(クラウド backend)

| # | ふるまい | 関連ファイル |
|---|---|---|
| 8.1 | LayerSettingsDialog から「認証」ボタンで CredentialDialog を開ける | `layer_settings_dialog.py` |
| 8.2 | API key を入力 → 「テスト」で backend の `verify_credentials` を呼ぶ → 成功で `verified=True` を永続化 | `app_controller.py:verify_and_save_credentials` ※ P3 で `credentials_service.py` へ移動 |
| 8.3 | 検証成功時、該当レイヤが MISSING_CREDENTIALS なら新しい認証情報で reload する | 同上 末尾 |
| 8.4 | key の再入力で verified フラグは自動的に False に戻る | `app_controller.py:set_credential` |
| 8.5 | 401 / サブスク切れ等を観測したら `invalidate_verification` で次回 Start を gate | backend 例外ハンドラ + `app_controller.py:invalidate_verification` |
| 8.6 | サービスアカウント JSON 等のファイル指定は CredentialField の field_type="file" でファイル選択ダイアログ表示 | `credential_dialog.py` |

---

## 9. 出力テスト(🔊 出力テスト)

| # | ふるまい | 関連ファイル |
|---|---|---|
| 9.1 | text_only モードのとき **「🔊 (TTS なし)」(disable)** | `control_panel.py:_sync_test_button_state` |
| 9.2 | 出力デバイス未選択のとき **「🔊 出力未選択」(disable)** | 同上 |
| 9.3 | 動作中のとき **「🔊 (動作中)」(disable)** | 同上 |
| 9.4 | 押下で TTS → Output 経路を別スレッドで叩く + ボタンは「再生中…」(disable) | `control_panel.py:_on_test_output_clicked` |
| 9.5 | 再生テキストは固定で「テスト音声」 | `control_panel.py:_TEST_PLAYBACK_TEXT` |
| 9.6 | 成功 / 失敗いずれもステータステキストの「操作イベント」に記録 | 同上 成功 / 失敗ハンドラ |

---

## 10. 設定の永続化 / 初期化

| # | ふるまい | 関連ファイル | 備考 |
|---|---|---|---|
| 10.1 | 設定変更は ConfigStore に即時書き込み、ファイル保存は「設定を保存」ボタンで明示的に行う | `config_store.py:save` | **現状維持**。auto-persist 化(保存ボタン撤去)は pendList 起票(2026-06-10)で保留 |
| 10.2 | 「設定を再読込」ボタンで外部編集の取り込み(全 backend キャッシュ破棄 + INIT に戻る) | `app_controller.py:load_settings` | 維持 |
| 10.3 | 「デバイス再列挙」ボタンで入出力プルダウンを再構築 | `settings_panel.py:_populate_devices_into_dropdowns` | 維持 |
| 10.4 | PROCESS kind capture の `devices.input` は **save しない**(セッション間で持ち越さない) | `app_controller.py:_strip_volatile_inputs_before_save` | 維持 |
| 10.5 | アプリ起動時、`auto_load=True` 指定の backend だけ先行ロード(その他は INIT のまま) | `main_window.py` + `app_controller.py:load_auto_load_layers_async` | 維持 |
| 10.6 | 各セクション(SettingsPanel 3 つ + ControlPanel status)の折り畳み状態は永続化される | `settings_panel.py:_persist_collapsed` + `control_panel.py:_on_status_toggle` | 維持 |

---

## 11. プロセス選択(CAPTURE = PROCESS kind)

| # | ふるまい | 関連ファイル |
|---|---|---|
| 11.1 | PROCESS kind backend を選ぶと入力 UI が「プロセス選択…」ボタンに切替 | `settings_panel.py:_show_process_select_ui` |
| 11.2 | ボタン押下で ProcessSelectDialog が開く + プロセス一覧が列挙される | `process_select_dialog.py` |
| 11.3 | プロセスを選んで ▶ 試聴開始でレベルメータが動く(5fps poll) | `process_select_dialog.py` + `process_enumerator.py:_PeakWorker` |
| 11.4 | OK で PID を `devices.input` に保存 + ボタンラベルが「PID 1234 ▼」に | `settings_panel.py:_on_capture_select_clicked` |
| 11.5 | PID 未選択時は Start ボタン disable / 選択完了で即座に Start enable に遷移 | `control_panel.py:refresh_ready_state` ※ P2 で経路が逆参照 → 購読に変わる(遷移自体は維持) |

---

## 12. エラーハンドリング(致命 / 警告)

| # | ふるまい | 関連ファイル |
|---|---|---|
| 12.1 | FatalError → パイプライン停止 + status textbox に表示 + status_label「停止中(エラー)」 | `control_panel.py:_apply_fatal` |
| 12.2 | WARN → UI には出さない(app.log のみ) | `control_panel.py:_apply_warn`(現状: return のみ) |
| 12.3 | 同種 (stage, exception type) の連続エラーは NotificationThrottle で集約 → 通知に `(+N件抑制)` 表記 | `notification_throttle.py` |
| 12.4 | 起動失敗(同期検証 / 非同期 loader)は NotificationBanner + status_label + status textbox の **3 段表示** | `control_panel.py:_do_start_async` / `_apply_loader_failed` |
| 12.5 | キューあふれで発話が捨てられたら WARN ログ + on_dropped 通知(UI には出さない) | `pipeline.py:_put_with_drop` + `app_controller.py:_handle_dropped` |

---

## 13. 置き換え予定のふるまい(P2: event-unify で実施)

これらは **「現在はあるが P2 完了後は無くなる / 変わる」** ふるまい。P2 完了時に確認:

| # | 旧ふるまい | 置き換え |
|---|---|---|
| 13.1 | 「設定を保存」ボタン | **撤去しない**(MVVM 案からの変更)。auto-persist 化は pendList で保留 |
| 13.2 | SettingsPanel が ControlPanel の `refresh_ready_state()` を直接呼ぶ | 撤去。AppController の状態変化イベント購読経由の自動更新 |
| 13.3 | ControlPanel の 3 秒周期 `_schedule_status_refresh` poll | 撤去。push 駆動(判断点: 保険として 30 秒間隔で 1 Phase 残す選択肢あり → Roadmap §4) |
| 13.4 | AppController.set_callbacks(on_status_change=...) の single callback 互換層 | 撤去。`add_status_listener`(Subscription)に統一。text_ready / utterance_done / fatal / warn も同型の listener 登録へ |
| 13.5 | (MVVM 案: Subscription を Observable に置き換え) | **逆転**: Subscription を標準として維持・適用拡大する。Observable 基盤は作らない |
| 13.6 | 動作中デバイス変更の restart は SettingsPanel のドロップダウンハンドラだけが発火する | AppController の `set_setting("devices", …)` 反応系へ移管。**意味拡張**: 動作中のデバイス再列挙 fallback 書き込みでも restart が走るようになる(契約 §3.11 を書き換え) |

---

## 14. 確認の運用

各 Phase の完了 PR で本ファイルを開き、以下を行う:

1. その Phase で **新たに自動テストで担保された項目** に ✅ + テストファイル名を併記
2. 該当 Phase で **意図的に変更したふるまい** に ❌ + 経緯のコミットへのリンク
3. 該当 Phase で **手で 1 回踏んで確認したふるまい** に 🧪 + 確認日
4. 想定外のふるまい変化に気付いたら新規行を追加

最終 Phase(P3、P4 を実施する場合は P4)時点で、Roadmap §3 対応表に挙がった全項目が
✅ / ❌ / 🧪 のいずれかになっていることを完了条件にする。

---

## 15. チェック記録

### P1: logic-extract(2026-06-10)

P1 で新たに自動テスト化(✅)された項目:

| 項目 | テスト |
|---|---|
| §1.6〜1.9(言語候補の再構築・fallback) | `tests/test_logic_language_choices.py`(判断)+ `tests/test_settings_panel_lang.py`(配線) |
| §1.10(TTS 互換警告、ユーザ選択は変更しない) | 同上(`tts_warning_needed` + 配線) |
| §3.1〜3.6(開始ボタンの状態遷移と優先順位) | `tests/test_logic_ready_state.py`(§3.4 は `tests/test_capture_process_source_lifecycle.py` でも実 widget 検証) |
| §6.1〜6.4(アクセラレータ表示) | `tests/test_logic_accel_summary.py` |
| §7.1〜7.2(ステータス集約テキストの形式) | `tests/test_logic_status_summary.py`(**golden**: 表示文字列を byte 単位で固定)+ `tests/test_app_controller.py` |
| §9.1〜9.3(出力テストボタンの disable 条件) | `tests/test_logic_ready_state.py` + `tests/test_control_panel_test_output.py`(実 widget) |

🧪 手動チェック(実機 GUI 操作: §1.6〜1.11 / §3.1〜3.6 / §6 / §7 / §9 を 1 回ずつ踏む)は
**未実施 — ユーザのドッグフーディングでの確認待ち**。実 widget を使った自動テスト
(panel 系)が配線を検証済みのため、リスクは表示の見た目崩れ等に限られる。
