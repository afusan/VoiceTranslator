# Plan: refactor-backend-settings-button-disable-when-empty

## 目標

「設定」ボタンを、そのレイヤ×バックエンド組み合わせで `visible_fields()` が
空リストを返す場合に **disabled** にする。

設定項目の有無は既存の `visible_fields(layer, current_backend)` をそのまま利用し、
判定ロジックは `gui/logic/` の純関数として独立させる。Panel 側は backend 選択変更時に
純関数を呼んでボタン状態を反映するだけにする。

## 非目標

- スキーマそのものへの変更(フィールドの追加・削除)
- 「設定」ボタン以外のボタン状態
- ボタンのツールチップ表示
- i18n 文言の追加(ボタン文言 "設定" は変わらない)

## 現状の確認

`py -m uv run python` で `visible_fields()` を実行した結果:

| layer | backend | visible_fields の結果 |
|-------|---------|----------------------|
| CAPTURE | soundcard | `[pipeline.captured_queue_max_bytes]` — 非空 |
| VAD | silero | `[]` — **空** |
| VAD | webrtcvad | `[aggressiveness, frame_ms]` — 非空 |
| TTS | mms | `[]` — **空** |
| OUTPUT | soundcard | `[pipeline.synthesized_queue_max_bytes]` — 非空 |

`silero` と `mms` が auto_load 一掃の結果、設定項目がゼロになっている。
これらの「設定」ボタンは disabled にする対象。

## 対象範囲

### 新規

| ファイル | 内容 |
|----------|------|
| `src/voice_translator/gui/logic/settings_button.py` | `has_settings(layer, backend) -> bool` 純関数 |
| `tests/test_logic_settings_button.py` | 純関数の small テスト |

### 変更

| ファイル | 内容 |
|----------|------|
| `src/voice_translator/gui/settings_panel.py` | `_build_backends_section` でボタン初期状態を設定 / `_on_backend_change` でボタン状態を再評価 |
| `docs/design/Class.md` | `has_settings` 純関数の役割を追記 |

## 判定ロジックの設計

### 置き場所: `gui/logic/settings_button.py`(新規)

```python
def has_settings(layer: LayerKind, backend: str) -> bool:
    """指定レイヤ×backend に表示すべき設定項目があれば True を返す。

    判定は layer_settings_schema.visible_fields() に委譲する。
    空リストなら設定対象なし → False。
    """
    from voice_translator.gui.layer_settings_schema import visible_fields
    return len(visible_fields(layer, backend)) > 0
```

**設計判断:** 関数名を `has_settings` とし bool を返す形にする。
`visible_fields` の結果長の比較を直接 Panel に書くと、スキーマの
取得方法が変わったときに Panel 側を直す必要が生じるため、
logic 関数で吸収する。

**代替案:** `visible_fields` を Panel から直接呼ぶ(1行で済む)。
→ 却下。CLAUDE.md の UI 規約「判断は logic」に反し、Panel に
  スキーマ依存が直接入る。将来スキーマ API が変わると Panel も変わる。

**代替案2:** スキーマ側に `has_settings()` を追加する。
→ 却下。スキーマは「フィールド宣言」が責務で、
  ボタン表示制御の判断は `gui/logic/` の責務。責務が分かれている。

### 呼び出し規約

`has_settings(layer, backend_internal_name)` の形で呼ぶ。
`backend_internal_name` は `_backend_display_to_internal` 後の内部値(
`"soundcard"`, `"silero"` 等)を渡す。

## Panel 側の配線

### ボタン参照の保持

現状の `_backend_rows` には `[label, option, status_label, cfg_btn]` を保存している(
`settings_panel.py` 244行目)。cfg_btn は index=3。

ボタン状態の更新専用に `_settings_btns: dict[LayerKind, ctk.CTkButton]` を
別途保持することで、型安全かつ明示的に参照できるようにする。

### 初期状態の反映

`_build_backends_section` 内、`cfg_btn` を構築した直後に:

```python
if not has_settings(layer, current_internal):
    cfg_btn.configure(state="disabled")
self._settings_btns[layer] = cfg_btn
```

`_settings_btns` は `_backend_rows` と同じ `_build_backends_section` 冒頭で
`self._settings_btns: dict[LayerKind, ctk.CTkButton] = {}` として初期化する。

### backend 選択変更時の再評価

`_on_backend_change` の末尾(既存の各種連動処理の後)に
`_sync_settings_btn_state(layer, internal_value)` を呼ぶ:

```python
def _sync_settings_btn_state(self, layer: LayerKind, internal: str) -> None:
    btn = self._settings_btns.get(layer)
    if btn is None:
        return
    enabled = has_settings(layer, internal)
    target_state = "normal" if enabled else "disabled"
    try:
        btn.configure(state=target_state)
    except Exception:  # noqa: BLE001 - widget 破棄後の呼び出しは無視
        pass
```

ただし running ロック / absorbed / TTS=(なし) による disable は既存の
`_apply_running_lock_visual` / `_apply_absorbed_visuals` / `_apply_tts_none_visual`
が管理している。これらとの整合について:

- **running ロック**: `_apply_running_lock_visual` が全行を disable → 解除時に
  `_apply_absorbed_visuals` → `_apply_tts_none_visual` を呼ぶ。
  空設定 button の disable はこの復元で上書きされてしまう。

  対処: `_apply_absorbed_visuals` から戻る際の normal 復元(700行目付近)を
  `_sync_settings_btn_state` でフォローする。具体的には、running 解除時の
  `_apply_absorbed_visuals` 末尾の `_apply_tts_none_visual` 呼び出しの**後**に
  `_sync_all_settings_btn_states()` を呼ぶ。

- **absorbed**: 吸収されたレイヤは `cfg_btn` ごと disabled になる。
  吸収解除時は `_apply_absorbed_visuals` が normal に戻す。これも上記と同様に
  `_sync_all_settings_btn_states()` で上書き修正する。

- **TTS=(なし)**: TTS 行の cfg_btn を disabled にするのは `_apply_tts_none_visual` の責務。
  `_sync_settings_btn_state` はこれと干渉しない(TTS=(なし) のとき TTS backend は
  `TTS_NONE_INTERNAL` → `has_settings("none")` は空フィールド → disabled で一致する)。
  Output 行の disable は `_apply_tts_none_visual` が別途行うため干渉しない。

```python
def _sync_all_settings_btn_states(self) -> None:
    """全レイヤの設定ボタン enabled/disabled を現在の backend 選択から再計算する。

    running ロック解除・absorbed 解除後の復元で呼ぶ。
    """
    for layer, btn in self._settings_btns.items():
        internal = str(
            self._controller.get_setting("backends", layer.value, default="")
        )
        enabled = has_settings(layer, internal)
        try:
            btn.configure(state="normal" if enabled else "disabled")
        except Exception:  # noqa: BLE001
            pass
```

### 言語切替の影響

`LAYER_SETTINGS` の各エントリの `label_key` は i18n キーだが、
`visible_fields()` はキー存在有無を判定するだけで **tr() は呼ばない**。
したがって言語切替でフィールドの出現/消滅は起きない → ボタン状態は
言語切替で変わらない → 言語切替イベントへの購読追加は不要。

## 反応すべきイベント種

| イベント | 対応 |
|----------|------|
| backend 選択変更 (`_on_backend_change`) | `_sync_settings_btn_state(layer, new_internal)` を呼ぶ |
| running ロック解除 (`_apply_running_lock_visual(False)`) | `_apply_absorbed_visuals` 後に `_sync_all_settings_btn_states()` を呼ぶ |
| absorbed 解除 (`_apply_absorbed_visuals`) | 上記と同じ箇所で吸収解除ループ後 `_sync_all_settings_btn_states()` を呼ぶ |
| 言語切替 | 対応不要(フィールド出現/消滅なし) |
| TTS=(なし) | `TTS_NONE_INTERNAL` の時は `has_settings` が False → 整合する |

## テスト戦略

### small テスト: `tests/test_logic_settings_button.py`

テスト対象: `has_settings(layer, backend)` の純関数

| テストケース | 期待値 |
|---|---|
| `has_settings(VAD, "silero")` | `False` |
| `has_settings(VAD, "webrtcvad")` | `True` |
| `has_settings(TTS, "mms")` | `False` |
| `has_settings(TTS, "sapi")` | `True` |
| `has_settings(CAPTURE, "soundcard")` | `True` |
| `has_settings(OUTPUT, "soundcard")` | `True` |
| `has_settings(ASR, "faster_whisper")` | `True` |

これらは GUI/controller なしで動く(モック不要)。

### Panel smoke テスト(既存テスト基盤を使った配線確認)

既存の `test_settings_panel_tts_none.py` や `test_settings_panel_running_lock.py` の
パターンに倣い、Panel を実 Tk で構築して widget state を確認する。

対象テスト: `tests/test_settings_panel_settings_btn_state.py`(新規)

| テストケース | 確認内容 |
|---|---|
| VAD=silero 初期 → 設定ボタン disabled | 構築後に widget.cget("state") == "disabled" |
| VAD を webrtcvad に変更 → 設定ボタン normal | `_on_backend_change` 後に state == "normal" |
| TTS=mms 初期 → 設定ボタン disabled | 構築後 state == "disabled" |

ヘッドレス環境では `pytest.skip` する(既存パターンと同様)。

## 影響範囲とリスク

### 機能的影響

- `silero` / `mms` を選択中のユーザは「設定」ボタンが disabled になる。
  設定する対象がないため、ユーザ体験上は問題ない(空ダイアログが開く
  ほうが混乱を招く)。

### 既存テストへの波及

- `test_layer_settings_schema.py` — 変更なし(スキーマは触らない)
- `test_settings_panel_tts_none.py` — TTS=(なし) 時の TTS cfg_btn disabled
  は `_apply_tts_none_visual` が担当するため変更なし。ただし Panel に
  `_settings_btns` が追加されるため、モックが `get_setting("backends", layer.value)`
  を返せる必要がある(既存モックは `""` を返すのでそのまま動く)。
- `test_settings_panel_running_lock.py` — running ロック解除後の `_sync_all_settings_btn_states`
  追加で cfg_btn の state が empty 設定 backend では disabled のままになる。
  running lock テストがボタン状態を確認している場合は要確認。

### リスク

| リスク | 程度 | 対策 |
|--------|------|------|
| running ロック解除後の normal 復元で空設定ボタンが誤って normal に戻る | 中 | `_sync_all_settings_btn_states` を `_apply_tts_none_visual` の後に呼ぶ |
| absorbed 解除後も同様 | 中 | 同上 |
| `_apply_running_lock_visual` / `_apply_tts_none_visual` の呼び出し後に `_sync_all_settings_btn_states` 追加が漏れる | 低 | Panel smoke テストで確認 |
| 既存 running lock テストが cfg_btn の "normal" 復元を期待している | 低 | テスト内容を実装前に確認し、必要なら期待値を修正 |

### 後方互換

設定ボタンの enabled/disabled は UI 状態であり、保存設定に影響しない。
後方互換シムは不要。

## 削除・変更順序

1. `gui/logic/settings_button.py` 新規作成 + `test_logic_settings_button.py` 追加
2. `settings_panel.py` の `_build_backends_section` に `_settings_btns` 初期化と
   初期 disable 適用を追加
3. `_on_backend_change` 末尾に `_sync_settings_btn_state` 呼び出しを追加
4. running ロック解除・absorbed 解除後の `_sync_all_settings_btn_states` 呼び出しを追加
5. Panel smoke テスト `test_settings_panel_settings_btn_state.py` 追加
6. 既存 running lock テストを確認し、必要なら期待値を更新
7. `docs/design/Class.md` に `has_settings` の役割を追記
