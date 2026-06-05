# feature/dynamic-devices — 計画

メタ計画 [feature-runtime-flex-and-input](../feature-runtime-flex-and-input/Plan.md) の **Phase 4**。
動作中(▶ 開始 中)に SettingsPanel で入出力デバイスを変えたら、自動的に **停止 → 再開** して
新デバイスに切り替える。

---

## 1. 目的

ドッグフーディング中に頻発するシナリオ:
- 配信視聴中、ヘッドホンを外して別の出力デバイスに音を切り替えたい。
- 入力ソースを別のスピーカ(別アプリのループバック)に切り替えたい。

現状は **設定変更 → 「設定を保存」 → 一度停止 → ▶ 開始** という多段操作が必要。
これを「設定パネルでデバイスを変えるだけ」で自動再開するように改善する。

---

## 2. 方針(メタ Plan に追記されたユーザ質問への回答)

### Q1: 「発話中に切り替えた場合の挙動は、停止 or バッファ再生後に次から新デバイス、で合っているか?」

**A**: 停止→再開方式を採用するため、**現在再生中の音声バッファは捨てて即停止 → 新デバイスで再開** する。
「バッファ再生後に切り替え」(graceful)は理想だが、Output スレッドだけ独立に停止する仕組みが必要で
複雑度が跳ねるため、まずはシンプルな停止→再開で運用感を確認する。

graceful 切替は pendList の「動作中デバイス変更時の graceful 切替」として起票済み。
体感ラグが許容できない場合に着手する想定。

### Q2: 「`AppController.restart_pipeline_async()` のイメージは?」

**A**: 既存の `stop_pipeline()`(同期)+ `start_pipeline()`(同期)を、**バックグラウンドスレッドで
順に呼ぶラッパー**。実装はおおよそ:

```python
def restart_pipeline_async(self, *, on_restarted, on_failed):
    if not self.is_running:
        on_restarted()
        return
    def _target():
        try:
            self.stop_pipeline()
            self.start_pipeline()
        except Exception as e:
            on_failed(str(e))
            return
        on_restarted()
    threading.Thread(target=_target, name="vt_restart", daemon=True).start()
```

理由: `start_pipeline_async` を中で呼ぶと Loader スレッドがネストし、callback の呼び出しタイミングが
分かりにくくなる。**1 スレッド内で stop → start を直列実行** する方が責務が明確。

---

## 3. スコープ

### in
- `AppController.restart_pipeline_async(on_restarted, on_failed)` を追加。
- `SettingsPanel._on_capture_changed` / `_on_output_changed` で `is_running` 中なら restart を発火。
- `NotificationBanner` で「(入力/出力)デバイスを切り替えました(再開中…)」を **永続表示**
  (`duration_ms=0`)し、再開完了時に `dismiss()`、失敗時は `show_error` で上書き。
- 連続デバイス変更 / 動作中でない時の no-op / 停止失敗時の挙動をテストで担保。

### out
- 無停止 swap(capture/output backend だけ差し替え)は対象外 → pendList 「graceful 切替」。
- バックエンド変更(`backends.*`)に対する自動 restart は対象外(従来通り次回 Start で反映)。
- `restart_pipeline_async` の callback で UI スレッドへ marshalling する責務は **呼び出し側**
  (SettingsPanel)が `after(0, ...)` で行う(AppController は呼び出しスレッド上で callback)。

---

## 4. 変更ファイル

| ファイル | 変更内容 |
|---|---|
| `src/voice_translator/common/app_controller.py` | `restart_pipeline_async(on_restarted, on_failed)` を追加。`is_running` でない時は即 `on_restarted()`。停止/開始失敗時は `on_failed(msg)` |
| `src/voice_translator/gui/settings_panel.py` | `_on_capture_changed` / `_on_output_changed` で `is_running` 中なら `_trigger_device_restart(kind)` を呼ぶ。`_trigger_device_restart` は banner を出して restart を起動。完了/失敗で banner を更新 |
| `tests/test_dynamic_devices.py` | 新規。restart_pipeline_async の挙動 / SettingsPanel ハンドラの connectivity |
| `docs/design/Class.md` | `AppController` の表に `restart_pipeline_async` を追加 |

---

## 5. 設計上のポイント

### 5-1. スレッドモデル

`restart_pipeline_async` は **新規の `vt_restart` スレッド**を立てて、その中で:
1. `stop_pipeline()` 同期実行(Coordinator.stop + 全スレッド join)
2. 失敗なら `on_failed`
3. 成功なら続けて `start_pipeline()` 同期実行(DeviceValidator + load_models + _start_coord)
4. 失敗なら `on_failed`、成功なら `on_restarted`

Loader スレッドのネストを避けるため、`start_pipeline_async` ではなく **同期版** `start_pipeline` を
呼ぶ。

### 5-2. UI スレッドへの marshalling

`on_restarted` / `on_failed` は `vt_restart` スレッド上で呼ばれる。tkinter ウィジェットを触る前に
SettingsPanel 側で `self.after(0, ...)` でメインスレッドへ戻す。

### 5-3. 連続デバイス変更

ユーザがプルダウンを連続で変えると複数の restart が並走する可能性がある。AppController 側で
**多重起動を防御**:
- `restart_pipeline_async` 内で「すでに restart スレッドが走っていたら skip」する。
- スキップした場合は `on_failed("既に再開中です")` を呼ぶ(UI バナー表示で進捗が見える)。

### 5-4. 「動作中でない時」の挙動

`is_running == False` で `restart_pipeline_async` が呼ばれた場合は no-op(`on_restarted` を即呼ぶ)。
理由: ConfigStore には既に新デバイス ID が書き戻されているので、次回 Start で反映される。バナーも
出さない(SettingsPanel 側で `is_running` を確認してから restart を発火する設計)。

### 5-5. デバイス検証(DeviceValidator)失敗時

新しいデバイスの組合せが「入力=出力」になっている場合、`start_pipeline` 内の `DeviceValidator.validate`
が `FatalError` を投げる。これは `on_failed` で `show_error` バナーが出る。ユーザは設定を直してから
手動で ▶ 開始 を押す。

---

## 6. テスト戦略

### small (自動)

| 観点 | 確認内容 |
|---|---|
| 動作中で restart 成功 | stop_pipeline → start_pipeline が順に呼ばれ on_restarted が呼ばれる |
| 動作中でない時 | 即 `on_restarted()` 呼出 / stop/start は呼ばれない |
| stop 失敗 | on_failed が呼ばれ、start は試みない |
| start 失敗(DeviceValidator 等) | on_failed が呼ばれる |
| 多重起動防御 | 走行中に再度呼ぶと on_failed("既に再開中です") |
| SettingsPanel の連携 | `_on_capture_changed` で `is_running` 中なら `restart_pipeline_async` が呼ばれる |
| バナー表示 | restart 起動時に show_info / 完了で dismiss / 失敗で show_error |

### middle (自動)

| 観点 | 確認内容 |
|---|---|
| 縦通し restart | WAV を流して動作中に capture を切替 → 新 capture が start され発話が継続する |

### 手動

- 実機で配信を流し、出力デバイスを変更すると 1〜2 秒の中断後に新デバイスで再生再開すること。
- 入力デバイスを「入力=出力」になる組合せに変えたとき、エラーバナーが出て元の動作中状態に戻ること
  (パイプラインは止まる)。

---

## 7. 確認手順

1. `py -m uv run pytest tests/test_dynamic_devices.py tests/test_app_controller.py tests/test_pipeline_e2e.py` が緑。
2. `py -m uv run pytest -q --ignore=tests/test_google_cloud_tts_large.py` で small 全体が緑。
3. 実機で「動作中 → 入出力デバイス切替 → 自動再開」を確認(中断 1〜2 秒程度)。
