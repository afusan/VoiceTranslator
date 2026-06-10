# P2: event-unify — 通知経路の一本化(処方箋)

作成: 2026-06-10。ブランチ: `refactor/ui-phase2-event-unify`(P1 ブランチから派生。
マージは全 Phase 完了後にまとめて行うユーザ方針のため、スタックブランチ運用)。
上位: [../Roadmap.md](../Roadmap.md) / 契約: [../behavioral-contract.md](../behavioral-contract.md)

---

## 1. 目的と完了条件

**目的**: 3 系統併存している通知経路(`set_callbacks` / Subscription / 逆参照+poll)を
**Subscription 1 本**に統一し、Panel 間の直接依存を断つ。

**完了条件**:
1. `py -m uv run pytest` 全 pass
2. `set_callbacks` / `SettingsPanel.set_control_panel` / ControlPanel→SettingsPanel 転送が存在しない
3. 動作中デバイス変更の restart が AppController の `set_setting` 反応系に移管されている
   (契約 §3.11 の意図的変更 ❌ を記録)
4. 契約 §2.8 / §11.5 / §13(13.2〜13.6)の確認記録
5. 新規 `try/except Exception` の追加ゼロ

## 2. 着手前判断点の決定(Roadmap §4)

| 判断点 | 決定 | 理由 |
|---|---|---|
| 3 秒 poll の扱い | **即廃止せず、30 秒に伸ばして役割を縮小** | ErrorHandler が通知するのは FATAL / WARN のみで、**RECOVERABLE / SKIP のリトライ失敗は backend のエラー履歴に記録されるだけでイベント化されない**(`pipeline._call_with_retry` → `record_error`)。poll を消すとこれらが「最近のエラー」欄に出るのが他イベント任せになる。30 秒 poll は「イベント化されていないエラー履歴の遅延表示」専用として残す。完全廃止は backend エラーのイベント化(BackendBase 拡張)とセットで P4 以降の検討事項 |
| restart 発火条件の意味拡張(§13.6) | **受け入れ** | 動作中に devices.* が書き変わったら理由を問わず restart(再列挙 fallback 含む)。実デバイスが変わったのに旧デバイスで動き続ける方が事故 |

## 3. 設計

### 3.1 AppController: 汎用 listener 機構(既存 Subscription の適用拡大)

`_ui_status_listeners` を汎用化: `_ui_listeners: dict[int, tuple[event: str, callback]]` +
`_emit(event, *args, **kwargs)`。`Subscription` / `_remove_listener` 機構はそのまま使う。

公開 API(すべて `-> Subscription`):

| メソッド | イベント | callback シグネチャ |
|---|---|---|
| `add_status_listener(cb)` | status | `(layer: LayerKind, status: ModelStatus)`(既存と同一) |
| `add_text_ready_listener(cb)` | text_ready | `(record: dict)` |
| `add_utterance_done_listener(cb)` | utterance_done | `(record: dict)` |
| `add_fatal_listener(cb)` | fatal | `(message, *, exc, stage, seq_id, suppressed)` |
| `add_warn_listener(cb)` | warn | 同上 |
| `add_settings_listener(cb)` | settings | `(keys: tuple[str, ...])` — `set_setting` のキー(値は含めない) |
| `add_restart_listener(cb)` | restart | `(event: PipelineRestartEvent)` |

**削除**: `set_callbacks` と `_on_*` callback フィールド一式(挙動が消えるのではなく
経路が置き換わる。互換層テストは削除し、コミットメッセージで明示)。
ErrorHandler への注入は `on_fatal=self._emit_fatal / on_warn=self._emit_warn`(emit への薄い橋)。

**注意(スレッド)**: `_emit` は呼び出し元スレッド(Loader / Coordinator / vt_restart)で
listener を呼ぶ。UI 側 listener は従来どおり `widget.after(0, ...)` で marshalling する規約を維持。

### 3.2 共通型(common/types.py)

```python
@dataclass(frozen=True)
class PipelineRestartEvent:
    phase: str        # "started" | "completed" | "failed"
    device_key: str   # 契機となった devices キー("input" | "output")
    message: str = "" # failed 時の理由
```

### 3.3 set_setting の反応系に devices を追加 + settings イベント

- `("devices", "input"|"output", value)` かつ `is_running` → `_restart_for_device_change(key)`:
  started を emit → `restart_pipeline_async(on_restarted=completed emit, on_failed=failed emit)`。
  多重 restart は既存の「既に再開中です」が failed イベントとして流れる。
- すべての `set_setting` の最後に `_emit("settings", keys)`(ControlPanel が devices.* を
  購読して ready 再計算 → 逆参照①の代替。契約 §11.5 の遷移は維持)。

### 3.4 SettingsPanel

- **削除**: `set_control_panel` / `_control_panel` / `_controller_is_running` /
  `_trigger_device_restart` / `_on_restart_completed` / `_apply_restart_completed` /
  `_on_restart_failed` / `_apply_restart_failed`、および
  `_on_capture_changed` / `_on_output_changed` / `_on_capture_select_clicked` 内の restart 呼び出し
  (デバイス変更ハンドラは `set_setting` を書くだけになる)
- **追加**: `__init__` で自ら購読
  - `controller.add_status_listener(self.on_status_change)`(従来は ControlPanel が転送していた)
  - `controller.add_restart_listener(self._on_restart_event)` → `after(0)` で
    `_apply_restart_event(event)`: started=show_info(duration_ms=0) / completed=dismiss /
    failed=show_error。banner 文言は `gui/logic/restart_messages.py`(新設)に置き、
    **現行文言と一字一句同一**にする

### 3.5 ControlPanel

- `set_callbacks` 呼び出しと `settings_panel` コンストラクタ引数・転送を削除し、
  §3.1 の listener 6 本を `__init__` で購読(`self._subscriptions` に保持)
- `_on_settings_changed_from_thread(keys)`: `keys[0] == "devices"` のとき
  `after(0, self._sync_ready_state)`(PID 選択完了 → Start enable / 出力選択 → test enable)
- `refresh_ready_state()` 公開窓は**削除**(呼び出し元の逆参照が消えるため。挙動の置換は
  settings イベント経由。契約 §13.2)
- poll: `_STATUS_REFRESH_INTERVAL_MS = 30_000` に変更し、docstring を
  「イベント化されていない backend エラー履歴(RECOVERABLE/SKIP)の遅延表示専用」に更新

### 3.6 MainWindow

- `ControlPanel(self, controller, banner=...)`(settings_panel 引数廃止)
- `set_control_panel` 注入を削除。listener 登録は各 Panel が自身で行うため MainWindow の
  結線コードは減る(banner の before 注入は維持)

## 4. テスト方針(詳細は testPlan.md)

- 旧 `set_callbacks` 互換テストは削除(経路ごと廃止のため。シナリオは listener 版で温存)
- AppController: 各 add_*_listener の emit / unsubscribe、devices 変更 → restart イベント列、
  settings イベント発火
- SettingsPanel: restart イベント → banner 反映 / デバイス変更ハンドラが restart を呼ばないこと
- ControlPanel: settings イベント → ready 再計算 / 転送が無いこと
- GUI スタブ controller 2 箇所に listener API を追加

## 5. ガードレール

P1 Plan §5 と同一(テストを弱めない / 新規 broad except 禁止 / Plan 外 API 変更時は停止 /
表示文言の不変 / マージしない)。
