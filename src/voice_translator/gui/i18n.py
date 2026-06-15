"""i18n: UI 表示文言を集約する言語別カタログとアクセス API。

役割: 画面に出る文言を 1 か所(言語別カタログ)に集約し、`tr(key, **kwargs)` で
キー経由で引けるようにする。logic / widget は文言を直書きせず、必ず `tr()` を通す。

設計方針(詳細は docs/design/feature-ui-i18n-foundation/Plan.md):
- キーは「文脈単位」で持つ(同一文言でも出る場所が違えば別キー)。真に同一概念のみ
  `common.*` に集約する。
- キーは **リテラル**で渡す(`tr("ready.toggle.start")`)。動的なキー生成は禁止
  (健全性検査を静的解析で成立させるため)。
- **`tr()` は表示する瞬間(関数内)で呼ぶ。モジュールレベルで結果を定数に焼かない**。
  これにより将来 `current_locale()` を可変化したとき(即時切替が要件化したとき)に
  追従できる(健全性検査でトップレベル `tr()` を機械的に禁止している)。
- 現状の対応言語は ja のみ。`current_locale()` をロケール解決の単一窓口とし、将来
  en / zh / es を足すときは `_CATALOGS` に辞書を追加し、ここで現在ロケールを返す形にする。
- 未登録キー / テンプレート引数不足は黙って空文字にせず例外にする(早期発見のため)。
- **カタログに入れるのは「翻訳対象の文言」のみ**。他の真実の源のミラー(enum value と
  揃える `Missing Credentials` 等)はカタログに入れず、源を直接参照する(二重管理を避ける)。
"""

from __future__ import annotations

# ロケール → メッセージカタログ。土台フェーズは ja のみ。
# 将来 en / zh / es を足すときはこの dict にカタログを追加する。
_JA: dict[str, str] = {
    # NOTE: 土台フェーズは logic 層のみ tr() 化したため、widget 直書きの共通文言
    # (Cancel / OK 等)はまだここに無い。Phase 3(widget 置換)で common.* を追加する。
    # --- ready_state: ControlPanel のトグル/ステータス/ロード/出力テスト ---
    "ready.toggle.auth_missing": "認証情報未設定",
    "ready.status.auth_missing": "認証情報未設定(詳細ダイアログで設定してください)",
    "ready.toggle.auth_unverified": "認証未検証",
    "ready.status.auth_unverified": "認証が未検証です(詳細ダイアログの「認証」でテストしてください)",
    "ready.toggle.downloading": "モデル DL 中…",
    "ready.status.downloading": "モデルダウンロード中…",
    "ready.toggle.no_process": "プロセス未選択",
    "ready.status.no_process": "プロセスを選択してください(設定 → プロセス選択…)",
    "ready.toggle.start": "▶ 開始",
    "ready.status.idle_will_load": "停止中(押下時にロードします)",
    "ready.status.idle_loading": "停止中(ロード中)",
    "ready.status.idle": "停止中",
    "ready.load.loaded": "ロード済み",
    "ready.load.loading": "ロード中…",
    "ready.load.load": "↻ ロード",
    "ready.test.tts_none": "🔊 (TTS なし)",
    "ready.test.no_output": "🔊 出力未選択",
    "ready.test.run": "🔊 出力テスト",
    # --- language_choices: fallback / TTS 非対応の通知バナー ---
    "language.src_fallback": (
        "入力言語を {old} から {new} に変更しました({backend} が {code} に対応していないため)"
    ),
    "language.tgt_fallback": (
        "出力言語を {old} から {new} に変更しました({backend} が {code} に対応していないため)"
    ),
    "language.tts_warning": (
        "TTS バックエンド {backend} は読み上げ言語 {lang} に対応していません"
        "(Translator 出力言語を変えるか、別の TTS バックエンドに切り替えてください)"
    ),
    # --- accel_summary: 「演算: …」ラベル ---
    "accel.gpu": "演算: GPU ({devices})",
    "accel.cpu": "演算: CPU のみ",
    "accel.preparing": "演算: -(モデル準備中)",
    # --- status_summary: ステータス集約のセクション見出し・レイヤ行 ---
    "status.recent_errors": "最近のエラー:",
    "status.gui_events": "操作イベント:",
    "status.layer_skipped": "(なし)",
    "status.layer_absorbed": "({into} の {backend} で実行)",
    # --- backend_display: プルダウン表示・skipped ステータス・kind ラベル ---
    "backend.tts_none": "(なし)",
    "backend.skipped_status": "(なし)",
    "capture_kind.device": "デバイス",
    "capture_kind.process": "プロセス",
    # --- restart_messages: 自動 restart バナー ---
    "restart.device.input": "入力",
    "restart.device.output": "出力",
    "restart.started": "{device}デバイスを切り替えました(再開中…)",
    "restart.failed": "{device}デバイス変更後の再開に失敗しました: {message}",
}

_CATALOGS: dict[str, dict[str, str]] = {
    "ja": _JA,
}

_DEFAULT_LOCALE = "ja"


def current_locale() -> str:
    """現在の UI ロケールを返す(ロケール解決の単一窓口)。

    土台フェーズでは ja 固定で、起動後も不変(その場切替 UI は作らない)。即時切替が
    要件化したら、この関数の戻り値を可変化し再描画イベントを足すだけで拡張できる
    (文言は表示時に `tr()` で引く規約のため焼き付きが無い)。
    """
    return _DEFAULT_LOCALE


def tr(key: str, **kwargs: object) -> str:
    """キーに対応する文言を返す。`kwargs` があれば `str.format` で差し込む。

    - 未登録キーは `KeyError`(黙って空文字を返さない)。
    - テンプレートが要求する引数が `kwargs` に無ければ `format` が `KeyError` を送出する
      (プレースホルダ未置換のまま表示する事故を防ぐ)。文言に波括弧を出したい場合は
      `{{` / `}}` でエスケープする。
    """
    catalog = _CATALOGS[current_locale()]
    try:
        template = catalog[key]
    except KeyError:
        raise KeyError(f"未登録の i18n キー: {key!r}") from None
    return template.format(**kwargs)


def all_keys(locale: str = _DEFAULT_LOCALE) -> frozenset[str]:
    """指定ロケールのカタログが持つ全キー(健全性検査用)。"""
    return frozenset(_CATALOGS[locale].keys())
