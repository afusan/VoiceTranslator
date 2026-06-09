# P1: logic-extract — UI 判断ロジックの抽出(処方箋)

作成: 2026-06-10。ブランチ: `refactor/ui-phase1-logic-extract`。
上位: [../Roadmap.md](../Roadmap.md) / 契約: [../behavioral-contract.md](../behavioral-contract.md)

本 Plan は**実装を委譲できる処方箋形式**で書く。関数シグネチャ・移行元・禁止事項を確定済みのため、
実装中に設計判断が必要になったら**作業を止めて報告**すること(勝手に判断しない)。

---

## 1. 目的と完了条件

**目的**: SettingsPanel / ControlPanel / AppController に同居している「UI 判断ロジック」
(状態 → 表示すべき値 の計算)を、`gui/logic/` の**状態を持たない純関数**に抽出する。

**完了条件**:
1. `py -m uv run pytest` 全 pass(small)
2. **ユーザ可視のふるまい変更ゼロ**(表示文字列・色・有効/無効の遷移まで現状と同一。
   ステータス集約テキストは golden テストで文字列一致を担保)
3. `gui/logic/` の各関数に対する small テストが新設され、旧 shim 方式テスト
   (`MagicMock(spec=...)` + bound method)が logic 直テストに置き換わっている
4. 契約 §1.6〜1.11 / §3.1〜3.6 / §6 / §7 / §9 の手チェック(🧪 記録)
5. 触ったファイルの `except Exception`(モック対策由来)が削減されている(新規追加ゼロ)

**ふるまい変更ゼロの定義**: ConfigStore への書き込みタイミング・通知バナーの文言・
プルダウン候補の順序・ボタン文言、すべて現状維持。

---

## 2. やらないこと(P2 / P3 のスコープ)

- `set_callbacks` / Subscription / Panel 間逆参照 / 3 秒 poll に**触らない**(P2)
- AppController の API 変更は §3.5 の 2 点のみ(他のメソッド移動は P3)
- widget 構成・レイアウトの変更(P4)
- 新規依存の追加 / 新しい `try/except Exception` の追加

---

## 3. 新規モジュールと API 仕様

新設: `src/voice_translator/gui/logic/`(`__init__.py` は re-export 無しの空でよい)

**共通規約**(全モジュールの docstring 冒頭に明記すること):
- 役割: 「UI に表示すべき値を計算して返す」。**widget / controller / ConfigStore に触らない**
- 状態を持たない(モジュール変数は定数のみ)。入力は引数、出力は戻り値のみ
- customtkinter を import しない(`common.types` と標準ライブラリのみ依存可)

### 3.1 `gui/logic/palette.py` — 色定数

```python
"""UI 配色の定数表。役割: ModelStatus・アクセラレータ表示の色を一元管理する。"""
STATUS_COLORS: dict[ModelStatus, str]   # settings_panel.py の _STATUS_COLORS を移動
STATUS_COLOR_DEFAULT = "#64748b"
ACCEL_GREEN = "#16a34a"; ACCEL_AMBER = "#d97706"; ACCEL_SLATE = "#94a3b8"
DISABLED_TEXT = "#475569"               # _apply_tts_none_visual のグレーアウト色
```

### 3.2 `gui/logic/ready_state.py` — ボタン/ラベル状態の計算

```python
@dataclass(frozen=True)
class WidgetSpec:
    text: str
    enabled: bool           # True → state="normal" / False → "disabled"

@dataclass(frozen=True)
class ReadyState:
    toggle: WidgetSpec      # 開始/停止トグルボタン
    status_text: str        # status_label の文言
    load: WidgetSpec        # ↻ ロードボタン
    test: WidgetSpec        # 🔊 出力テストボタン

def filter_active_statuses(
    statuses: Mapping[LayerKind, ModelStatus], output_mode: str,
) -> dict[LayerKind, ModelStatus]:
    """text_only なら TTS / OUTPUT を除外した dict を返す。"""

def compute_ready_state(
    statuses: Mapping[LayerKind, ModelStatus],   # 全レイヤ分(フィルタ前)
    *,
    output_mode: str,            # "audio" | "text_only"
    capture_kind: CaptureKind,   # 現在の capture backend の kind
    has_input_source: bool,      # devices.input が非空
    has_output_device: bool,     # devices.output が非空
) -> ReadyState:
    """idle 状態のときの 3 ボタン + ラベルの表示を一括計算する。"""
```

**移行元(挙動を 1:1 で再現すること)**:

| 移行元(control_panel.py) | 行範囲目安 | 対応 |
|---|---|---|
| `_sync_ready_state` の分岐ラダー | 527〜579 | → `toggle` + `status_text`。優先順: MISSING_CREDENTIALS > DOWNLOADING > (PROCESS kind かつ input 空) > 通常(INIT/NOT_DOWNLOADED → 「停止中(押下時にロードします)」/ LOADING → 「停止中(ロード中)」/ それ以外 → 「停止中」) |
| `_sync_load_button_state` | 581〜602 | → `load`。全 LOADED → 「ロード済み」disable / LOADING あり → 「ロード中…」disable / 空 or その他 → 「↻ ロード」normal |
| `_sync_test_button_state` | 604〜632 | → `test`。text_only → 「🔊 (TTS なし)」disable / output 空 → 「🔊 出力未選択」disable / それ以外 → 「🔊 出力テスト」normal |
| `_capture_source_required_but_empty` | 634〜658 | → `capture_kind == CaptureKind.PROCESS and not has_input_source` に縮約 |
| `_active_layer_statuses` | 660〜672 | → `filter_active_statuses` |

**View 側に残すもの**: `_state != "idle"` ガード(View の状態機械)/ controller からの入力収集
(`output_mode`・`get_capture_kind`・`get_setting`)/ `ReadyState` を widget に塗る適用部
(`btn.configure(text=…, state=…)` のヘルパ 1 つ)。
controller への問い合わせ失敗時の縮退(現状の `except Exception` → audio 扱い等)は
**View 側の入力収集に残してよい**(logic には正常値だけ渡す)。

### 3.3 `gui/logic/language_choices.py` — 言語プルダウンの計算

```python
@dataclass(frozen=True)
class LanguageSelection:
    codes: list[str]            # プルダウン候補(順序確定済み、表示変換前のコード)
    selected: str               # 選択すべきコード
    fallback_from: str | None   # fallback が起きたときの元コード。起きなければ None

def compute_src_selection(
    supported: list[str], *, supports_auto: bool, current: str,
    fallback_pool: list[str],
) -> LanguageSelection:
    """ASR の入力言語候補。supported 空 → fallback_pool。sorted(set) 後、auto 対応なら先頭に
    "auto"。current が候補に無ければ "auto" 優先 → 先頭、で fallback。"""

def compute_tgt_selection(
    supported: list[str], *, current: str, fallback_pool: list[str],
) -> LanguageSelection:
    """Translator の出力言語候補。"auto" は除外。current が候補に無ければ
    ja > en > 先頭 の順で fallback。"""

def tts_warning_needed(
    *, tts_backend: str, supported: list[str], current_tgt: str,
    none_internal: str = "none",
) -> bool:
    """TTS 非対応言語の警告要否。backend 空/none → False、supported 空 → False、
    current_tgt が supported に含まれる → False、それ以外 → True。"""

def format_src_fallback_message(old_code: str, new_code: str, backend_name: str) -> str
def format_tgt_fallback_message(old_code: str, new_code: str, backend_name: str) -> str
def format_tts_warning_message(tgt_code: str, backend_name: str) -> str
```

**移行元(settings_panel.py)**: `_refresh_input_language_choices`(567〜606)/
`_refresh_target_language_choices`(631〜672)/ `_check_tts_output_lang_compatibility`(677〜703)/
`_notify_lang_fallback`・`_notify_tgt_lang_fallback`・`_notify_tts_unsupported_lang` の文言部
(608〜627, 705〜733)。メッセージ文言は**現状と一字一句同じ**にする(`format_language` 使用含む)。

**View 側に残すもの**: controller への supported 問い合わせ / `format_language` での表示変換と
dropdown.configure / `set_setting("languages", …)` の書き込み(fallback_from が非 None のとき)/
banner 表示(notify_fallback フラグの判断含む)。
**注意**: tgt fallback 後に TTS 互換チェックを連鎖させる現挙動(`_refresh_target_language_choices`
末尾)は View 側に残す。

### 3.4 `gui/logic/backend_display.py` — backend 名の表示↔内部値変換

```python
TTS_NONE_DISPLAY = "(なし)"
TTS_NONE_INTERNAL = "none"     # AppController.TTS_NONE と一致させる(コメントで明記)
CAPTURE_KIND_LABELS: dict[CaptureKind, str]   # settings_panel.py の _CAPTURE_KIND_LABELS を移動

def tts_display_to_internal(display: str) -> str
def tts_internal_to_display(internal: str) -> str
def capture_display_to_internal(display: str) -> str      # 末尾カッコ抽出(現挙動どおり)
def capture_internal_to_display(internal: str, kind: CaptureKind | None) -> str
    # kind が None / 未知 → internal をそのまま返す(防衛挙動も移管)
def backend_display_to_internal(layer: LayerKind, display: str) -> str
def backend_internal_to_display(
    layer: LayerKind, internal: str, *, capture_kind: CaptureKind | None = None,
) -> str
```

**移行元(settings_panel.py)**: モジュールレベル関数 `_tts_display_to_internal` 等(79〜107)と
`_render_backend_choices` / `_backend_internal_to_display` / `_backend_display_to_internal` /
`_capture_internal_to_display`(241〜291)。kind の取得(controller 問い合わせ)は View 側に残し、
logic へは取得済みの `CaptureKind | None` を渡す。

### 3.5 `gui/logic/status_summary.py` — ステータス集約テキストの整形

```python
@dataclass(frozen=True)
class LayerStatusLine:
    layer: LayerKind
    backend_name: str
    status: ModelStatus
    dl_size_hint: str       # "(~2.9GB)" 形式 or ""(先頭スペース含む現状形式に注意)

def format_status_summary(
    lines: Sequence[LayerStatusLine],
    errors: Sequence[tuple[LayerKind, ErrorRecord]],   # timestamp 降順ソート済みを渡す
    gui_events: Sequence[str],                          # 古い→新しい順で渡す(現 deque のまま)
    *, max_errors: int = 5, max_events: int = 5,
) -> str:
    """現在の AppController.get_status_summary + ControlPanel._refresh_status_text の
    文字列合成を 1 関数に統合。出力は現状と byte 単位で同一にする(golden テスト対象)。"""
```

**AppController 側の変更(本 Phase で唯一の API 変更)**:
1. **追加** `get_status_snapshot() -> tuple[list[LayerStatusLine], list[tuple[LayerKind, ErrorRecord]]]`
   — 現 `get_status_summary` の**データ収集部**(backend 名・状態・`_dl_size_hint`・
   `_collect_recent_errors`)をそのまま使い、整形せずに返す。`LayerStatusLine` は
   `gui.logic.status_summary` から import **しない**こと(common → gui の依存は禁止)。
   dataclass は `common/types.py` に置き、gui/logic からは common.types を参照する。
2. **削除** `get_status_summary()` — 整形は UI の役割のため `format_status_summary` へ移動。
   既存テスト(`test_app_controller.py` の該当 6 件)は「snapshot のデータ検証」+
   「formatter の文字列検証」に書き換える(シナリオは温存、削除しない)。

**ControlPanel 側**: `_refresh_status_text` は
`snapshot = controller.get_status_snapshot()` → `format_status_summary(…, self._gui_event_log)` →
textbox 貼り付け、に変更。操作イベントの「新しい順 5 件」加工は formatter 内に移す。

### 3.6 `gui/logic/accel_summary.py` — アクセラレータ表示の集約

```python
def summarize_accel(
    devices: Mapping[LayerKind, str | None],   # 各レイヤの device 報告(未ロードは None)
    *, output_mode: str,
) -> tuple[str, str]:
    """(表示文言, 色) を返す。GPU(cuda/mps)あり → 緑 / CPU のみ → 琥珀 / 不明 → slate。
    text_only では TTS / OUTPUT の device を無視。"""
```

**移行元**: `control_panel.py:_refresh_accel_label`(674〜711)の判定部。
device 文字列の正規化(`lower()`)も logic 側へ。View は devices 収集と label.configure のみ。

---

## 4. 実装手順(コミット単位の目安)

1. **commit 1**: `gui/logic/` 全 6 モジュール + 新規テスト 5 ファイル(§testPlan.md)を追加。
   既存コードは未変更(この時点で旧実装と logic が並存。全テスト pass を確認)
2. **commit 2**: ControlPanel を logic 呼び出しに置換(ready_state / accel / status_summary)。
   AppController に `get_status_snapshot` 追加・`get_status_summary` 削除。関連テスト書き換え
3. **commit 3**: SettingsPanel を logic 呼び出しに置換(language_choices / backend_display)。
   `test_settings_panel_lang.py` 等の shim テストを logic 直テストへ書き換え(配線確認の
   smoke は panel テストとして 1〜2 件残す)
4. **commit 4**: 触った範囲の防御 `except Exception` 削減 + docstring の役割表明更新 +
   Class.md 反映 + 契約手チェック記録

各 commit で `py -m uv run pytest` 全 pass を維持すること。

---

## 5. ガードレール(実装者への必須指示)

1. **テストを通すためにテストを弱めない**(assert 削除・緩和禁止。書き換えはシナリオ温存が条件)
2. **新しい `try/except Exception` を追加しない**。logic 関数内に try/except は原則不要
   (入力は View が正規化してから渡す)
3. 本 Plan に無い public API の追加・変更・改名をしない。必要になったら**作業を止めて報告**
4. 表示文字列・色・候補順序を「改善」しない(同一性が完了条件。golden テストで担保)
5. logic モジュールから customtkinter / controller / ConfigStore を import しない
6. マージしない(レビュー後にユーザ判断)
