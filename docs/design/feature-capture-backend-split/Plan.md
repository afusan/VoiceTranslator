# feature/capture-backend-split — 計画

メタ計画 [feature-runtime-flex-and-input](../feature-runtime-flex-and-input/Plan.md) の **Phase 5**。
入力 backend を「デバイス種類単位」に分解し、将来 `ProcTapCaptureBackend`(per-process キャプチャ)を
追加するだけで GUI に並ぶ構造を整える。

---

## 1. 目的

現状の構造を確認すると、入力に関する UI と内部表現は **既に 2 段** になっている:

| 段 | UI 位置 | ConfigStore キー | 役割 |
|---|---|---|---|
| 上段(backend) | 「バックエンド」セクション → 「音声取得」プルダウン | `backends.capture` | どの `AudioCaptureBackend` 実装を使うか |
| 下段(source) | 「デバイス」セクション → 「入力デバイス」プルダウン | `devices.input` | 上段 backend の `list_sources()` のどれを選ぶか |

ただし **連動が不完全** で、上段(capture backend)を切り替えても下段(source)が自動 refresh されない。
ProcTap backend が追加されたとき、ユーザは「音声取得」を `proctap` に変えても「入力デバイス」に
旧 backend(soundcard)のソースが残ったままに見え、混乱する。

本ブランチではこの **連動の穴を塞ぐ**(動的な refresh)。さらに、ProcTap backend を追加する手順を
ドキュメントで明示し、構造的にすぐ並べられる状態を担保する。

---

## 2. スコープ

### in
- `SettingsPanel._populate_devices_into_dropdowns` を **capture / output 別々に refresh できる形** に分割:
  - `_refresh_capture_sources_dropdown()`
  - `_refresh_output_devices_dropdown()`
  - 既存呼び出し箇所(`__init__` / `_on_reload` / 「デバイス再列挙」ボタン)は両方を順に呼ぶ。
- `_on_backend_change` で **CAPTURE backend 切替時** に `_refresh_capture_sources_dropdown()` を呼ぶ。
- 「(取得失敗: ...)」表示の選び戻しが backend 切替時にも安全に動くように整理。
- ProcTap backend(`ProcTapCaptureBackend`)を追加する手順を `backend_setup.py` の docstring と
  Plan.md に明示。本ブランチでは実装はしない。
- 既存テスト(`test_settings_panel_*`)が変わらず通ること。新規テストで refresh 連動を担保。

### out
- **ProcTap backend 本体の実装は対象外**(pendList の「ProcTap backend 実装」エントリ参照)。
- **動作中の CAPTURE backend 変更で自動 restart** は対象外。backend 切替はロード処理が走るため、
  P4 のデバイス変更 restart とは別の問題(動作中切替の体験設計が必要)。pendList に新規起票。
- SettingsPanel の UI レイアウト変更(「入力デバイス」を「バックエンド」セクションに移動する等)は
  対象外。「バックエンド」と「デバイス」の対応関係は manual.md で明示する。

---

## 3. 変更ファイル

| ファイル | 変更内容 |
|---|---|
| `src/voice_translator/gui/settings_panel.py` | `_populate_devices_into_dropdowns` を `_refresh_capture_sources_dropdown` / `_refresh_output_devices_dropdown` に分割。CAPTURE backend 切替時に capture 側を再列挙 |
| `src/voice_translator/common/backend_setup.py` | docstring に「新規 capture backend の追加方法」を追記(`registry.register(LayerKind.CAPTURE, "proctap", ProcTapCaptureBackend, capabilities=...)` の例) |
| `docs/design/Class.md` | SettingsPanel の説明に refresh ヘルパを記載 |
| `docs/manual.md` | 「音声取得」と「入力デバイス」の対応関係を補足 |
| `docs/design/pendList.md` | 「動作中の capture backend 変更で自動 restart」を新規起票 |
| `tests/test_capture_backend_split.py` | 新規。refresh 連動 / 既存値保持 / backend 切替時の自動 refresh |

---

## 4. 設計上のポイント

### 4-1. 既存構造の確認

`AppController.list_capture_sources()` は `_create(LayerKind.CAPTURE)` を呼んで、その時点で
ConfigStore に書かれている `backends.capture` の backend を新規生成し、`list_sources()` を返す。
つまり **「現在選ばれている capture backend のソース」** を返す動作は既に正しい。

問題は SettingsPanel が `_on_backend_change(CAPTURE, value)` で **何もしていない** こと。
新 backend 名で ConfigStore は更新されるが、「入力デバイス」プルダウンは旧 backend のソースのまま。

### 4-2. refresh タイミング

- backend 切替 → `_on_backend_change(CAPTURE, value)` で `_refresh_capture_sources_dropdown()` を呼ぶ。
- 既存値の保持: 新 backend のソース一覧に旧 source_id が含まれていれば維持。含まれていなければ
  先頭ソースに fallback(`_populate_devices_into_dropdowns` の既存ロジックを踏襲)。
- output 側は触らない(独立)。

### 4-3. ProcTap 連携の追加手順

`ProcTapCaptureBackend` を作って `backend_setup.py` に下記のように register するだけで GUI に並ぶ:

```python
from voice_translator.capture.proctap_backend import ProcTapCaptureBackend

registry.register(
    LayerKind.CAPTURE,
    "proctap",
    lambda: ProcTapCaptureBackend(...),
    backend_cls=ProcTapCaptureBackend,
    capabilities=BackendCapabilities(
        is_cloud=False,
        requires_credentials=False,
        notes="ProcTap (WASAPI Process Loopback)。per-process キャプチャ。",
    ),
)
```

- ProcTap が提供する `list_sources()` は「アプリ単位」(プロセス名 / PID)を返せばよい。
- `source_id` は ProcTap 内で一意な識別子(PID 文字列 or プロセス名)で OK。`devices.input` に
  そのまま保存される。
- backend 切替時に SettingsPanel が `_refresh_capture_sources_dropdown` を呼んで自動的に
  ProcTap のソースが並ぶ。

### 4-4. 動的 backend 切替(動作中)は対象外

CAPTURE backend を動作中に変えると:
1. `set_setting` で旧 backend を evict → 新 backend をロード(別スレッド)
2. ロード完了後、Coordinator は依然として古い backend を握っている → restart が必要

これは「動作中 backend 変更で自動 restart」の問題で、本ブランチでは扱わない。新 pendList エントリ
「動作中の capture backend 変更で自動 restart」を起票し、需要が出てから対応。

---

## 5. テスト戦略

### small (自動 / `tests/test_capture_backend_split.py`)

| 観点 | 確認内容 |
|---|---|
| capture / output 別 refresh が独立 | `_refresh_capture_sources_dropdown` を呼んでも output 側に影響しない / 逆も |
| CAPTURE backend 切替時に refresh 発火 | `_on_backend_change(CAPTURE, new_name)` で `list_capture_sources` が新 backend ベースで再列挙 |
| 既存値保持 | 新 backend のソース一覧に旧 source_id が含まれていれば選択を維持 |
| 非対応値で fallback | 旧 source_id が新 backend に無ければ先頭ソースに fallback、`devices.input` を更新 |
| 取得失敗時 | `list_capture_sources` が例外 → 「(取得失敗: ...)」表示で UI が壊れない |

### 手動

| 観点 | 手順 |
|---|---|
| 単 backend 環境 | soundcard のみで起動 → 既存通り動く(回帰なし) |
| 複数 backend 環境 | (ProcTap 実装後) capture プルダウンで `soundcard` / `proctap` を切替 → ソース一覧が即時更新 |

---

## 6. 確認手順

1. `py -m uv run pytest tests/test_capture_backend_split.py tests/test_settings_panel_lang.py tests/test_settings_panel_sections.py tests/test_settings_panel_tts_none.py` が緑。
2. `py -m uv run pytest -q --ignore=tests/test_google_cloud_tts_large.py` で small 全体が緑。
3. 実機(現状 soundcard のみ): 起動 → 「音声取得」プルダウンに `soundcard` だけ並ぶ → 通常動作。
   将来 ProcTap が追加されたら自動的にプルダウンに並ぶ。
