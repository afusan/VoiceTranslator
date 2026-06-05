# feature/capture-kind — 計画

ProcTap 取り込みの **段階 1**。`CaptureKind` 概念を導入し、SettingsPanel の「音声取得」
プルダウンを「取得単位 (backend)」表記に統合する。Python 3.12 への引き上げも同時実施。

メタ計画: [feature-runtime-flex-and-input](../feature-runtime-flex-and-input/Plan.md)。
段階 2(ProcTap 本体)/段階 3(プロセス列挙 + エコーバック)は別ブランチ。

---

## 1. 目的・経緯

- P5(`feature/capture-backend-split`)で「複数 capture backend が並ぶ」UI 構造は整えた。
- だが「**取得単位**(デバイス / プロセス)」というユーザ視点の概念が UI に明示されていなかった。
- ProcTap(per-process キャプチャ)取り込みを前に、概念を導入してから本体実装に進む。

---

## 2. 検討事項と結論

### Q1: バックエンドにうまく追加できる構造か?

3 案を比較(詳細は `tmp/report1.md` 参照):
- A: 別 backend として並列(P5 の延長)
- B: 1 backend で kind を持つ
- C: ハイブリッド(別 backend + UI 上の「取得単位」概念)

**結論: 案 C 採用**。backend は単一責務のまま、UI 上で kind を見せる。

### Q2: ダウンサンプリングは取得時の指定だけで OK?

**NO**。`proctap.ProcessAudioCapture` は **出力 48kHz/2ch/float32 固定**(`resample_quality` は
内部リサンプル品質の指定のみ)。VoiceTranslator 内部標準 16kHz/1ch/float32 へは **段階 2 で自前
リサンプル + ダウンミックス**(`scipy.signal.resample_poly` + 平均化)を実装する。

### Q3: Python 3.12 に上げて問題ないか?

uv で `cpython-3.12.13` がインストール済み。`pyproject.toml` を `requires-python = ">=3.12"` に
引き上げ、`uv python pin 3.12` + `uv sync --extra cpu` で再構築。**small テスト 964 件すべて pass**
で回帰なし。本ブランチで採用。

---

## 3. スコープ

### in
- `pyproject.toml`: `requires-python = ">=3.12"` に引き上げ
- `.python-version`: `3.12` に固定
- `CaptureKind` enum 新設(`common/types.py`)
- `CaptureSource.kind` フィールド追加(既定 `DEVICE`、後方互換)
- `AudioCaptureBackend.capture_kind() -> CaptureKind` クラスメソッド追加(既定 `DEVICE`)
- `SoundcardCaptureBackend.capture_kind() = DEVICE` を明示。`list_sources()` 内の `CaptureSource`
  にも `kind=DEVICE` を渡す
- `AppController.get_capture_kind(backend_name) -> CaptureKind` ヘルパ追加
- `SettingsPanel`:
  - 「音声取得」プルダウンの表示を「`<kind label> (<backend>)`」形式に変更
  - 表示 ↔ 内部値変換を `_render_backend_choices` / `_backend_internal_to_display` /
    `_backend_display_to_internal` の 3 ヘルパに統一(TTS=(なし) と CAPTURE の特例を吸収)
- 既存 P5 の自動 refresh はそのまま継承
- 新規テスト: `tests/test_capture_kind.py`(13 件)

### out
- **ProcTapCaptureBackend 本体実装**(段階 2 / pendList 起票済み)
- **プロセス列挙 + エコーバック確認**(段階 3 / pendList 起票済み)
- 「取得単位」を独立プルダウンとして UI に追加することは見送り(「音声取得」プルダウン 1 つに
  kind と backend 名を併記する案を採用、UI 要素を増やさない)

---

## 4. 変更ファイル

| ファイル | 変更内容 |
|---|---|
| `pyproject.toml` | `requires-python = ">=3.12"` |
| `.python-version` | `3.12` |
| `src/voice_translator/common/types.py` | `CaptureKind` enum / `CaptureSource.kind` フィールド |
| `src/voice_translator/capture/backend.py` | `capture_kind()` クラスメソッド既定 + docstring |
| `src/voice_translator/capture/soundcard_backend.py` | `capture_kind() = DEVICE` 明示、`list_sources` で kind を渡す |
| `src/voice_translator/common/app_controller.py` | `get_capture_kind(name)` ヘルパ |
| `src/voice_translator/gui/settings_panel.py` | 3 ヘルパで表示↔内部値変換を統一 / CAPTURE 行を kind 主体表示に |
| `docs/design/Class.md` | `AudioCaptureBackend` 表に `capture_kind`、AppController 表に `get_capture_kind` |
| `docs/manual.md` | Python 3.12 / 「音声取得」プルダウンの新表記 |
| `docs/design/pendList.md` | 段階 2 / 段階 3 を新規起票 |
| `tests/test_capture_kind.py` | 13 件(enum / CaptureSource / backend / AppController / SettingsPanel) |

---

## 5. 設計上のポイント

### 5-1. 表示形式 ↔ 内部値変換の統一

以前は TTS の「(なし)」のみが特例だったが、CAPTURE の kind 主体表示でも同様の変換が必要になった。
この機会に `_render_backend_choices` / `_backend_internal_to_display` / `_backend_display_to_internal`
の 3 つに集約し、レイヤ別の特例を 1 箇所で扱える形にした。将来他レイヤで類似の表示が必要に
なっても拡張しやすい。

### 5-2. ConfigStore の互換

`backends.capture` キーの**内部値は backend 名のまま**(`"soundcard"`)。表示形式が変わるだけで、
既存の `config.yaml` ファイルとは完全互換。マイグレーション処理は不要。

### 5-3. mock 互換性の防衛

`_capture_internal_to_display` で `kind` が `CaptureKind` の値でない場合(古い AppController モック
や仕様逸脱)は backend 名そのままを返す。既存テスト(`test_capture_backend_split.py` 等)が
`get_capture_kind` を持たないモックで動いていても回帰しない。

### 5-4. Python 3.12 引き上げの判断

- `uv` に `cpython-3.12.13` がローカル済みで `uv sync` が成功
- 全 small テスト 964 件で回帰なし
- ProcTap 段階 2 で同梱されている native wheel が `cp312` 対応のため、3.12 化が前提として整合する
- 3.11 をサポートし続ける必然性は無い(本アプリは PyPI 配布もしていない、`uv sync` で自動取得)

---

## 6. 確認手順

1. `py -m uv run pytest tests/test_capture_kind.py tests/test_capture_backend_split.py tests/test_settings_panel_*.py` が緑(本ブランチで確認)。
2. `py -m uv run pytest -q --ignore=tests/test_google_cloud_tts_large.py` で small 全体 964 件 pass(本ブランチで確認済み)。
3. 実機: `py -m voice_translator` 起動 → 「バックエンド」セクションの「音声取得」プルダウンが「デバイス (soundcard)」と表示される。プルダウン操作で旧来通り動作する。
