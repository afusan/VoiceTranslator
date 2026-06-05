# feature/proctap-process-list — 計画

ProcTap 取り込みの **段階 3**。`ProcTapCaptureBackend.list_sources()` を pycaw 経由の本実装に
切り替え、プロセス選択 UI(別ダイアログ + レベルメータ試聴)を提供する。

検討経緯と判断は `tmp/report1.md`(段階 3 着手前の論点整理)参照。本書はその確定版を要約。

---

## 1. スコープ

### in
- `pyproject.toml`: 既存 `capture-proctap` extras に `pycaw` / `psutil` 追加
- `src/voice_translator/capture/process_enumerator.py` 新規:
  - `enumerate_active_processes() -> list[CaptureSource]`
  - pycaw `AudioUtilities.GetAllSessions()` → `AudioSessionState.Active` フィルタ
  - PID 単位 dedupe(同 PID 内に複数 session があっても 1 件に集約)
  - `psutil.Process(pid).name()` でプロセス名補完、失敗時 `"unknown"` フォールバック
- `src/voice_translator/capture/proctap_backend.py`:
  - `list_sources()` を `process_enumerator.enumerate_active_processes()` 呼び出しに差し替え
- `src/voice_translator/gui/process_select_dialog.py` 新規:
  - PID テーブル + ↻ 更新ボタン
  - ▶ 試聴開始 / ■ 停止トグル
  - レベルメータ(`CTkProgressBar`、pycaw `IAudioMeterInformation.GetPeakValue()` を 30fps poll)
  - OK / Cancel
- `src/voice_translator/gui/settings_panel.py`:
  - capture backend の `capture_kind == PROCESS` のとき、source プルダウンを「プロセス選択…」
    ボタンに置き換え。押下で `ProcessSelectDialog` を開く。OK で選択 PID を AppController に設定
- `src/voice_translator/gui/control_panel.py` (`_sync_ready_state`):
  - 「capture_kind == PROCESS かつ source 未選択」分岐追加:
    Start ボタン disable + ラベル「プロセス未選択」
- `ConfigStore` / 起動時 load 経路:
  - `capture_kind == PROCESS` の source は **保存しない**(A-7 確定方針)
  - 起動時に値が残っていても **空扱い**(セーフティ)
- テスト:
  - small: `test_process_enumerator.py`(pycaw / psutil を monkeypatch 完全置換)
  - small: `test_process_select_dialog.py`(列挙更新 / 試聴トグル / OK・Cancel ロジック)
  - small: `test_settings_panel.py` 追記(kind による UI 切替)
  - small: `test_control_panel.py` 追記(未選択時 disable)
  - large: 既存 `test_proctap_backend_large` を拡張(`list_sources()` が 0 件以上返す)

### out(本ブランチではやらない)
- 動的列挙更新(設定ダイアログを開いたままプロセス起動/終了に追従) → pendList で 段階 4 候補
- **プロセス名・exe path での PID 永続化 → 完全にやらない**(A-7 確定)
- Linux/Mac の process-kind 列挙 → pendList 起票のみ
- ControlPanel への常駐レベルメータ → 採用せず(別ダイアログ案を採用、B-1 参照)

---

## 2. 設計上のポイント

### 2-1. 試聴経路は本番パイプラインと独立(B-2 確定)

本番の `ProcessAudioCapture`(WASAPI Process Loopback)は使わない。
代わりに pycaw の `IAudioMeterInformation.GetPeakValue()` を 30fps poll する:

```
[ダイアログ]
  PID 選択 → ▶ 試聴開始 押下
  → process_enumerator から取得した IAudioMeterInformation を保持
  → after(33ms) ループで GetPeakValue() を読む
  → CTkProgressBar に decay 込みで反映
```

理由:
- WASAPI ストリームを開かないので超軽量(数 μs / 呼び出し)
- 本番パイプライン起動前に試せる(本番起動するとプロセス選択 UI からは抜ける想定)
- ON/OFF を明示トグルで切り替え可能 → 負荷懸念の論点(B-2)を解決

### 2-2. process_enumerator の責務

- WASAPI セッション列挙ロジックを 1 モジュールに閉じ込める
- ProcTap 固有ではなく Windows プラットフォーム共通機能として位置付け
- 戻り値は `list[CaptureSource]`(`source_id=str(pid)`, `display_name=f"{name} ({pid})"`,
  `kind=CaptureKind.PROCESS`)
- pycaw 呼び出しは `_list_sessions()` の 1 関数に隔離 → テスト時 monkeypatch 完全置換

### 2-3. PID 永続化しない(A-7 確定)

- `capture_kind == PROCESS` の source は ConfigStore に **保存しない**
- 起動時に古い値が残っていても、capture backend の kind を見て **空扱いに正規化**
- これにより「再起動で PID が無効化される」問題は構造的に発生しない
- ユーザは毎回プロセス選択ダイアログから選び直す = シンプル

### 2-4. 未選択時の Start ボタン挙動

`ControlPanel._sync_ready_state` に新規分岐:

```python
if capture_kind == PROCESS and source_id is None/"":
    toggle_btn.disable, text="プロセス未選択"
    status_label = "プロセスを選択してください"
```

優先順位: MISSING_CREDENTIALS > DOWNLOADING > PROCESS 未選択 > 通常 LOADED/INIT 分岐。

### 2-5. UI 切替(SettingsPanel)

`capture_kind == DEVICE` のとき(soundcard):
- 現状のまま source プルダウン

`capture_kind == PROCESS` のとき(proctap):
- 「プロセス選択…」ボタン
- ボタンラベル: 未選択時 `"プロセス選択…"`、選択済み時 `"chrome.exe (1234) ▼"` のように現在値を表示
- ボタン押下 → `ProcessSelectDialog` → OK で AppController.set_input_source(pid)

---

## 3. 変更ファイル

| ファイル | 変更内容 |
|---|---|
| `pyproject.toml` | extras `capture-proctap` に `pycaw>=20240210` / `psutil>=5.9` 追加 |
| `src/voice_translator/capture/process_enumerator.py` | 新規(WASAPI セッション列挙 + dedupe + プロセス名補完) |
| `src/voice_translator/capture/proctap_backend.py` | `list_sources()` を本実装に差し替え |
| `src/voice_translator/gui/process_select_dialog.py` | 新規(PID 選択ダイアログ + 試聴メータ) |
| `src/voice_translator/gui/settings_panel.py` | kind=PROCESS で「プロセス選択」ボタン UI |
| `src/voice_translator/gui/control_panel.py` | 未選択時 Start disable |
| `src/voice_translator/common/config_store.py` or 起動 load 経路 | PROCESS source の保存抑制 + 起動時の空正規化 |
| `tests/test_process_enumerator.py` | 新規 small |
| `tests/test_process_select_dialog.py` | 新規 small |
| `tests/test_settings_panel.py` | kind 切替分岐の small 追加 |
| `tests/test_control_panel.py` | 未選択時 disable の small 追加 |
| `tests/test_proctap_backend.py` | `list_sources` 期待値更新(空→ enumerator 経由)、large 拡張 |
| `docs/design/Class.md` | ProcessEnumerator / ProcessSelectDialog 追記 |
| `docs/manual.md` | プロセス選択ダイアログの操作手順 |
| `docs/design/pendList.md` | 段階 3 ✅完了 / Linux/Mac 起票 / 永続化不要を確定 |
| `docs/design/feature-runtime-flex-and-input/Plan.md` | P6-3 完了マーク(あれば) |

---

## 4. 確認手順

1. `py -m uv sync --extra cpu --extra capture-proctap` で pycaw / psutil インストール
2. `py -m uv run python -c "from pycaw.pycaw import AudioUtilities; print(len(AudioUtilities.GetAllSessions()))"` で動作確認
3. `py -m uv run pytest tests/test_process_enumerator.py tests/test_process_select_dialog.py -q` → small pass
4. `py -m uv run pytest -q --ignore=tests/test_google_cloud_tts_large.py` → 全 small pass(回帰なし)
5. `py -m uv run pytest tests/test_proctap_backend.py -m large -q` → large pass
6. 実機: `config.yaml` の `backends.capture` を `proctap` にして起動 → SettingsPanel で「プロセス選択…」 → ダイアログ表示 → Spotify / YouTube タブ等を選択 → 試聴で動く → OK → 本番起動 → 翻訳ループが流れる

---

## 5. やらないこと(段階 3 範囲外)

| 項目 | 扱い |
|---|---|
| 動的列挙更新(プロセス起動/終了の追従) | pendList で 段階 4 候補 |
| プロセス名/exe path での PID 永続化 | **完全にやらない**(A-7 確定。再起動で都度選択) |
| Linux/Mac の process-kind 列挙 | pendList 起票のみ |
| ControlPanel への常駐レベルメータ | 採用しない(別ダイアログ案で十分) |
| 音声 echo(取り込み音をスピーカ再生) | TTS と被るためやらない |
