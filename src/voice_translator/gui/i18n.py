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
    # --- layer_settings_schema: レイヤ別設定ダイアログのラベル/ヘルプ ---
    # キーは layer_settings.<config パス>.{label,help}。共通ヘルパは概念単位で 1 キー。
    # schema は label_key / help_key にこのキーを持ち、LayerSettingsDialog が tr() で解決する。
    "layer_settings.auto_load.label": "起動時に自動ロード",
    "layer_settings.auto_load.help": (
        "ON にすると、アプリ起動時にこの backend を自動でロードする(既定 OFF)。"
        "OFF のままなら「▶ 開始」を押したときにロードする。"
    ),
    "layer_settings.load_model.label": "モデルを(再)ロード",
    "layer_settings.load_model.help": (
        "今すぐこのレイヤの backend をバックグラウンドで(再)ロードする。"
        "既にロード済みでも一度 evict して新しい設定値で作り直す。"
    ),
    "layer_settings.recent_durations.label": "直近処理時間",
    "layer_settings.recent_durations.help": "完了した発話の直近 5 件の平均処理時間。",
    "layer_settings.recent_durations.none": "直近データなし",
    "layer_settings.recent_durations.average": "直近 {count} 件平均: {avg} ms",
    # CAPTURE
    "layer_settings.pipeline.captured_queue_max_bytes.label": "入力バッファ容量 (bytes)",
    "layer_settings.pipeline.captured_queue_max_bytes.help": (
        "VAD出力PCMを次段(ASR)に渡すバッファのバイト上限。"
        "16kHz×float32 で 10MB ≒ 約 156 秒分。"
        "「▶ 開始」を押した時に反映される。"
    ),
    "layer_settings.backends_config.proctap.input_gain.label": "ProcTap: 入力ゲイン (倍率)",
    "layer_settings.backends_config.proctap.input_gain.help": (
        "取得した音声に掛ける増幅倍率(1.0=等倍、2〜8 程度が目安)。"
        "対象アプリの音量が小さく認識されないときに上げる(±1.0 でクリップ)。"
        "音量 0 は増幅できない。変更後は「モデルを(再)ロード」で反映。"
    ),
    # VAD
    "layer_settings.backends_config.webrtcvad.aggressiveness.label": "WebRTC: 感度 (0=低 〜 3=高)",
    "layer_settings.backends_config.webrtcvad.aggressiveness.help": (
        "3 にすると speech 判定が厳しくなり、ノイズで誤検知しにくい代わりに発話の取りこぼし増。"
    ),
    "layer_settings.backends_config.webrtcvad.frame_ms.label": "WebRTC: フレーム長 (ms)",
    "layer_settings.backends_config.webrtcvad.frame_ms.help": (
        "10 / 20 / 30 のいずれか。短いほど反応速いが CPU 負荷↑。"
    ),
    "layer_settings.backends_config.pyannote.model_id.label": "pyannote: モデル ID",
    "layer_settings.backends_config.pyannote.model_id.help": (
        "HuggingFace のモデル ID。標準は voice-activity-detection。"
    ),
    "layer_settings.backends_config.pyannote.device.label": "pyannote: device",
    "layer_settings.backends_config.pyannote.device.help": "cpu / cuda / mps / auto。CPU でも動くが激重。",
    "layer_settings.backends_config.pvcobra.threshold.label": "Cobra: 閾値 (0〜1)",
    "layer_settings.backends_config.pvcobra.threshold.help": (
        "voice probability の閾値。下げると speech が拾いやすくなる。"
    ),
    # ASR
    "layer_settings.pipeline.recognized_queue_size.label": "認識結果バッファ件数",
    "layer_settings.pipeline.recognized_queue_size.help": (
        "ASR が出力した認識テキストを翻訳段に渡すキューの上限件数。"
        "テキストは1発話で数百バイトと小さいため件数で管理する。"
    ),
    "layer_settings.backends_config.faster_whisper.model_size.label": "Whisper モデル",
    "layer_settings.backends_config.faster_whisper.model_size.help": (
        "Whisper のモデルサイズ。大きいほど精度が上がるが、RAM/VRAM と処理時間が増える。"
        "✓ 推奨 / ⚠ 重い / ✗ 不可 アイコンは現環境(RAM/VRAM)との適合度の目安。"
        "変更後は「モデルを(再)ロード」ボタンで反映する。"
    ),
    "layer_settings.backends_config.openai_whisper.model_size.label": "Whisper モデル(公式)",
    "layer_settings.backends_config.openai_whisper.model_size.help": (
        "openai-whisper(公式)のモデルサイズ。faster-whisper より重い傾向。"
        "tiny/base/small/medium/large-v3 から選択。"
    ),
    "layer_settings.backends_config.openai_whisper_api.model.label": "OpenAI API: モデル",
    "layer_settings.backends_config.openai_whisper_api.model.help": (
        "OpenAI Whisper API のモデル名。現状は `whisper-1` のみ。"
    ),
    "layer_settings.backends_config.google_stt.default_language.label": "Google STT: default 言語(auto 時)",
    "layer_settings.backends_config.google_stt.default_language.help": (
        "Google STT は自動言語検出に未対応のため、入力言語が `auto` のときは"
        "ここで指定した言語(ISO 639-3)で API を呼ぶ。"
    ),
    "layer_settings.backends_config.deepgram.model.label": "Deepgram: モデル",
    "layer_settings.backends_config.deepgram.model.help": (
        "Deepgram のモデル名(例: nova-3 / nova-2)。既定は nova-3。"
    ),
    # TRANSLATOR
    "layer_settings.pipeline.translated_queue_size.label": "翻訳結果バッファ件数",
    "layer_settings.pipeline.translated_queue_size.help": "翻訳済みテキストを TTS に渡すキューの上限件数。",
    "layer_settings.backends_config.nllb200.model_name.label": "NLLB-200 モデル",
    "layer_settings.backends_config.nllb200.model_name.help": (
        "NLLB-200 のモデル名(HF id)。distilled-600M / distilled-1.3B / "
        "1.3B / 3.3B から選択。大型は GPU 推奨。"
    ),
    "layer_settings.backends_config.openai_gpt.model.label": "OpenAI GPT: モデル",
    "layer_settings.backends_config.openai_gpt.model.help": (
        "OpenAI Chat Completions のモデル名(例: gpt-4o-mini / gpt-4o)。"
    ),
    "layer_settings.backends_config.anthropic_claude.model.label": "Anthropic Claude: モデル",
    "layer_settings.backends_config.anthropic_claude.model.help": (
        "Anthropic Messages のモデル名(例: claude-haiku-4-5-20251001)。"
    ),
    # TTS
    "layer_settings.backends_config.sapi.rate.label": "読み上げ速度 (rate)",
    "layer_settings.backends_config.sapi.rate.help": (
        "SAPI(pyttsx3)の rate。既定 180(普通)。早口にすると再生時間が短くなる。"
        "GUI 反映には「設定を再読込」または再起動が必要。"
    ),
    "layer_settings.backends_config.piper.voice_name.label": "Piper: voice",
    "layer_settings.backends_config.piper.voice_name.help": (
        "Piper の voice モデル名(`<lang_country>-<speaker>-<quality>` 形式)。"
        "初回利用時に Hugging Face `rhasspy/piper-voices` から DL される。"
        "日本語(ja)voice は標準配布なし。"
    ),
    "layer_settings.backends_config.elevenlabs.voice_id.label": "ElevenLabs: voice_id",
    "layer_settings.backends_config.elevenlabs.voice_id.help": (
        "ElevenLabs のプリメイド voice ID。デフォルトは Rachel(英語女性)。"
        "他の voice は ElevenLabs ダッシュボードの Voices ページから ID を取得。"
    ),
    "layer_settings.backends_config.elevenlabs.model_id.label": "ElevenLabs: モデル",
    "layer_settings.backends_config.elevenlabs.model_id.help": (
        "多言語: `eleven_multilingual_v2`(29 言語、品質寄り)。"
        "低レイテンシ: `eleven_turbo_v2_5`。"
        "英語専用: `eleven_monolingual_v1`。"
    ),
    "layer_settings.backends_config.openai_tts.voice.label": "OpenAI TTS: voice",
    "layer_settings.backends_config.openai_tts.voice.help": (
        "プリメイド 6 voice(alloy / echo / fable / onyx / nova / shimmer)。"
    ),
    "layer_settings.backends_config.openai_tts.model.label": "OpenAI TTS: モデル",
    "layer_settings.backends_config.openai_tts.model.help": "tts-1(低レイテンシ)/ tts-1-hd(高品質)。",
    "layer_settings.backends_config.google_tts.voice_name.label": "Google TTS: voice 名",
    "layer_settings.backends_config.google_tts.voice_name.help": (
        "Google TTS の voice 名(例: `en-US-Wavenet-A`、`ja-JP-Neural2-B`)。"
        "空欄なら言語コードから既定 voice が自動選択される。"
    ),
    "layer_settings.backends_config.google_tts.default_language.label": "Google TTS: default 言語",
    "layer_settings.backends_config.google_tts.default_language.help": (
        "`tgt_lang` が空のときに使う既定言語(ISO 639-3)。"
    ),
    # OUTPUT
    "layer_settings.pipeline.synthesized_queue_max_bytes.label": "出力バッファ容量 (bytes)",
    "layer_settings.pipeline.synthesized_queue_max_bytes.help": (
        "TTS 合成済み PCM を再生段に渡すバッファのバイト上限。"
        "16kHz×float32 で 5MB ≒ 約 78 秒分。"
        "「▶ 開始」を押した時に反映される。"
    ),
    # --- common(複数 widget で共有する真に同一概念の文言) ---
    "common.cancel": "Cancel",
    "common.ok": "OK",
    # --- dialog: ProcessSelectDialog ---
    "dialog.process_select.title": "プロセス選択 — ProcTap",
    "dialog.process_select.heading": "音声出力中のプロセス",
    "dialog.process_select.description": (
        "現在音を出している(または出す準備のできた)プロセスを表示しています。\n"
        "選択して「試聴開始」で当該プロセスの音量が右のメータで確認できます。"
    ),
    "dialog.process_select.refresh": "↻ 更新",
    "dialog.process_select.audition_start": "▶ 試聴開始",
    "dialog.process_select.audition_stop": "■ 停止",
    "dialog.process_select.no_process": "(該当プロセスなし — 音を鳴らしてから ↻ 更新)",
    # --- dialog: ConsentDialog ---
    "dialog.consent.title": "クラウド送信の同意確認",
    "dialog.consent.heading": "クラウドサービスの利用同意",
    "dialog.consent.default_data_summary": "音声データ(発話単位の PCM)とテキスト",
    "dialog.consent.body": (
        "backend: {backend}\n"
        "送信先サービス: {service}\n"
        "送信内容: {summary}\n\n"
        "このサービスを使うと、上記のデータが外部サーバに送信されます。"
        "サービス側のプライバシーポリシー/利用規約を確認のうえ、同意してください。"
    ),
    "dialog.consent.terms": "利用規約: {url}",
    "dialog.consent.suppress": "今後このダイアログを表示しない(suppress_dialogs)",
    "dialog.consent.cancel": "キャンセル",
    "dialog.consent.accept": "同意して使用",
    # --- dialog: CredentialDialog ---
    "dialog.credential.title": "認証情報の入力 — {service}",
    "dialog.credential.description": (
        "下のフィールドに API キー等を入力し、「テスト」を押してください。"
        "成功すると保存され、「動作開始」ボタンが押せるようになります。"
    ),
    "dialog.credential.no_spec": "このバックエンドは認証情報を要求していません。",
    "dialog.credential.cancel": "キャンセル",
    "dialog.credential.test": "テスト",
    "dialog.credential.testing": "テスト中…",
    "dialog.credential.browse": "参照…",
    "dialog.credential.placeholder_file": "(参照ボタンで選択)",
    "dialog.credential.placeholder_set": "●●●●●●●● (設定済み、変更時のみ入力)",
    "dialog.credential.placeholder_unset": "(未設定)",
    "dialog.credential.file_picker_title": "{label} のファイルを選択",
    "dialog.credential.missing_fields": "未入力のフィールドがあります: {fields}",
    "dialog.credential.internal_error": "検証中に内部エラー: {error}",
    "dialog.credential.ok": "✓ 認証 OK — {message}",
    "dialog.credential.saved": "保存しました",
    "dialog.credential.failed": "✗ 認証失敗 — {message}",
    "dialog.credential.unknown_cause": "原因不明",
    # --- dialog: LanguageSelectDialog ---
    "dialog.language_select.title": "言語を選択",
    "dialog.language_select.search_label": "言語を検索(コード / 英語名):",
    "dialog.language_select.search_placeholder": "例: swahili / swh",
    "dialog.language_select.no_match": "(一致なし)",
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
