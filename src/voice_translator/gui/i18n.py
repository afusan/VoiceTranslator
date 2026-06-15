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
    "backend.unregistered": "(未登録)",
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
    # --- layer 表示名(LayerSettingsDialog / SettingsPanel で共有) ---
    "layer.capture": "音声取得",
    "layer.vad": "VAD",
    "layer.asr": "ASR(書き起こし)",
    "layer.translator": "翻訳",
    "layer.tts": "TTS(音声合成)",
    "layer.output": "音声出力",
    # --- dialog: LayerSettingsDialog(スキーマ項目のラベルは layer_settings.* 参照) ---
    "dialog.layer_settings.title": "{layer} の設定",
    "dialog.layer_settings.unselected": "(未選択)",
    "dialog.layer_settings.backend_line": "バックエンド: {backend}",
    "dialog.layer_settings.no_fields": "このレイヤに編集可能な設定はありません。",
    "dialog.layer_settings.cancel": "キャンセル",
    "dialog.layer_settings.save": "保存",
    "dialog.layer_settings.unsupported_suffix": "(未対応型: {ft})",
    "dialog.layer_settings.no_options": "(選択肢なし)",
    "dialog.layer_settings.cred_placeholder_unset": "(未設定)",
    "dialog.layer_settings.cred_placeholder_set": "●●●●●●●● (設定済み、変更時のみ入力)",
    "dialog.layer_settings.auth": "認証",
    "dialog.layer_settings.auth_verified": "✓ 認証済み",
    "dialog.layer_settings.auth_unverified": "未認証",
    "dialog.layer_settings.auth_open": "認証を開く / 再認証",
    "dialog.layer_settings.auth_help": (
        "API キー等を入力して疎通確認まで通すと、verified=True で保存されます。"
        "キー失効 / サブスク切れ等で動作中にエラーが出た場合、verified は自動で False に戻ります。"
    ),
    "dialog.layer_settings.auth_saved": "認証 OK が保存されました。",
    "dialog.layer_settings.input_error": "入力エラー({label}): {error}",
    "dialog.layer_settings.cred_save_failed": "認証情報保存に失敗: {error}",
    "dialog.layer_settings.saved_reload": (
        "保存しました。中央「↻ ロード」ボタンで新しい設定を反映してください。"
    ),
    "dialog.layer_settings.saved_pipeline": (
        "保存しました。pipeline 値は次の「▶ 開始」で反映されます。"
    ),
    # --- dialog: LanguageSelectDialog ---
    "dialog.language_select.title": "言語を選択",
    "dialog.language_select.search_label": "言語を検索(コード / 英語名):",
    "dialog.language_select.search_placeholder": "例: swahili / swh",
    "dialog.language_select.no_match": "(一致なし)",
    # --- ControlPanel(idle 状態の文言は ready.* を再利用、過渡状態は下記) ---
    "control_panel.section_action": "動作",
    "control_panel.section_status": "ステータス",
    "control_panel.latency_none": "平均レイテンシ: -",
    "control_panel.latency": "平均レイテンシ: {avg} 秒(直近{count}件)",
    "control_panel.accel_init": "演算: -",
    "control_panel.clear_events": "操作イベントをクリア",
    "control_panel.recent_translations": "最近の翻訳:",
    "control_panel.clear": "クリア",
    "control_panel.test_playback_text": "テスト音声",
    "control_panel.playing": "再生中…",
    "control_panel.starting": "開始中…",
    "control_panel.stopping": "停止中…",
    "control_panel.running": "動作中",
    "control_panel.stop": "■ 停止",
    "control_panel.load_btn_starting": "(起動中)",
    "control_panel.load_btn_stopping": "(停止中)",
    "control_panel.load_btn_running": "(動作中)",
    "control_panel.test_btn_running": "🔊 (動作中)",
    "control_panel.idle_start_failed": "停止中(起動失敗)",
    "control_panel.idle_error": "停止中(エラー)",
    "control_panel.load_start_failed": "ロード起動失敗: {error}",
    "control_panel.load_failed": "ロード失敗: {error}",
    "control_panel.event_load_failed": "[ロード失敗] {error}",
    "control_panel.event_test_done": "[出力テスト] 再生完了: {text}",
    "control_panel.test_failed": "出力テスト失敗: {error}",
    "control_panel.event_test_failed": "[出力テスト失敗] {error}",
    "control_panel.start_failed": "起動失敗: {error}",
    "control_panel.event_start_failed": "[起動失敗] {error}",
    "control_panel.event_stop_exception": "[停止時例外] {error}",
    "control_panel.event_fatal": "[致命的エラー] {error}",
    "control_panel.suppressed_suffix": " (+{count}件抑制)",
    "control_panel.status_fetch_failed": "(ステータス取得に失敗: {error})",
    # --- SettingsPanel(レイヤ名は layer.* を共有) ---
    "settings_panel.section_backends": "バックエンド",
    "settings_panel.section_devices": "デバイス",
    "settings_panel.section_languages": "翻訳",
    "settings_panel.config_btn": "設定",
    "settings_panel.input_device": "入力デバイス:",
    "settings_panel.output_device": "出力デバイス:",
    "settings_panel.enumerating": "(列挙中)",
    "settings_panel.process_select": "プロセス選択…",
    "settings_panel.pid_selected": "PID {pid} ▼",
    "settings_panel.src_lang": "入力言語 (src):",
    "settings_panel.tgt_lang": "出力言語 (tgt):",
    "settings_panel.log_dir": "ログ出力先:",
    "settings_panel.save": "設定を保存",
    "settings_panel.reload": "設定を再読込",
    "settings_panel.redetect_devices": "デバイス再列挙",
    "settings_panel.unselected": "(未選択)",
    "settings_panel.fetch_failed": "(取得失敗: {error})",
    "settings_panel.no_input_device": "(入力デバイスなし)",
    "settings_panel.no_output_device": "(出力デバイスなし)",
    "settings_panel.process_dialog_failed": "プロセス選択ダイアログ起動失敗: {error}",
    "settings_panel.save_failed": "保存失敗: {error}",
    "settings_panel.saved": "設定を保存しました",
    "settings_panel.reload_blocked": "動作中は設定を再読込できません(停止してから実行してください)",
    "settings_panel.reload_failed": "読込失敗: {error}",
    "settings_panel.reloaded": "設定を再読込しました",
    # --- MainWindow ---
    "main_window.locale_running_blocked": (
        "動作中は表示言語を切り替えられません(停止してから実行してください)"
    ),
}

# 英語カタログ(Phase 4b)。キー集合は _JA と一致させる(test_catalog_key_parity で担保)。
_EN: dict[str, str] = {
    # ready_state
    "ready.toggle.auth_missing": "Credentials Not Set",
    "ready.status.auth_missing": "Credentials not set (configure them in the details dialog)",
    "ready.toggle.auth_unverified": "Auth Not Verified",
    "ready.status.auth_unverified": "Auth not verified (test it in the details dialog's 'Auth' section)",
    "ready.toggle.downloading": "Downloading model…",
    "ready.status.downloading": "Downloading model…",
    "ready.toggle.no_process": "No Process Selected",
    "ready.status.no_process": "Select a process (Settings → Select process…)",
    "ready.toggle.start": "▶ Start",
    "ready.status.idle_will_load": "Stopped (loads on press)",
    "ready.status.idle_loading": "Stopped (loading)",
    "ready.status.idle": "Stopped",
    "ready.load.loaded": "Loaded",
    "ready.load.loading": "Loading…",
    "ready.load.load": "↻ Load",
    "ready.test.tts_none": "🔊 (No TTS)",
    "ready.test.no_output": "🔊 No Output Selected",
    "ready.test.run": "🔊 Test Output",
    # language_choices
    "language.src_fallback": (
        "Changed input language from {old} to {new} ({backend} does not support {code})"
    ),
    "language.tgt_fallback": (
        "Changed output language from {old} to {new} ({backend} does not support {code})"
    ),
    "language.tts_warning": (
        "TTS backend {backend} does not support speech language {lang} "
        "(change the Translator output language, or switch to another TTS backend)"
    ),
    # accel_summary
    "accel.gpu": "Compute: GPU ({devices})",
    "accel.cpu": "Compute: CPU only",
    "accel.preparing": "Compute: - (preparing model)",
    # status_summary
    "status.recent_errors": "Recent errors:",
    "status.gui_events": "Actions:",
    "status.layer_skipped": "(none)",
    "status.layer_absorbed": "(run by {into}'s {backend})",
    # backend_display
    "backend.tts_none": "(none)",
    "backend.skipped_status": "(none)",
    "backend.unregistered": "(not registered)",
    "capture_kind.device": "Device",
    "capture_kind.process": "Process",
    # restart_messages
    "restart.device.input": "input",
    "restart.device.output": "output",
    "restart.started": "Switched {device} device (restarting…)",
    "restart.failed": "Failed to restart after changing {device} device: {message}",
    # layer_settings_schema
    "layer_settings.auto_load.label": "Auto-load on startup",
    "layer_settings.auto_load.help": (
        "When ON, this backend is loaded automatically at app startup (default OFF). "
        "If left OFF, it loads when you press '▶ Start'."
    ),
    "layer_settings.load_model.label": "(Re)load model",
    "layer_settings.load_model.help": (
        "(Re)load this layer's backend in the background now. "
        "Even if already loaded, it is evicted once and rebuilt with the new settings."
    ),
    "layer_settings.recent_durations.label": "Recent processing time",
    "layer_settings.recent_durations.help": "Average processing time of the last 5 completed utterances.",
    "layer_settings.recent_durations.none": "No recent data",
    "layer_settings.recent_durations.average": "Last {count} avg: {avg} ms",
    "layer_settings.pipeline.captured_queue_max_bytes.label": "Input buffer capacity (bytes)",
    "layer_settings.pipeline.captured_queue_max_bytes.help": (
        "Byte limit of the buffer passing VAD-output PCM to the next stage (ASR). "
        "At 16kHz×float32, 10MB ≈ about 156 seconds. "
        "Applied when you press '▶ Start'."
    ),
    "layer_settings.backends_config.proctap.input_gain.label": "ProcTap: input gain (multiplier)",
    "layer_settings.backends_config.proctap.input_gain.help": (
        "Amplification multiplier applied to captured audio (1.0=unity, 2–8 is a rough guide). "
        "Raise it when the target app's volume is too low to recognize (clips at ±1.0). "
        "Volume 0 cannot be amplified. Apply via '(Re)load model' after changing."
    ),
    "layer_settings.backends_config.webrtcvad.aggressiveness.label": "WebRTC: sensitivity (0=low – 3=high)",
    "layer_settings.backends_config.webrtcvad.aggressiveness.help": (
        "Setting it to 3 makes speech detection stricter — less false detection from noise, "
        "but more missed speech."
    ),
    "layer_settings.backends_config.webrtcvad.frame_ms.label": "WebRTC: frame length (ms)",
    "layer_settings.backends_config.webrtcvad.frame_ms.help": (
        "One of 10 / 20 / 30. Shorter reacts faster but raises CPU load↑."
    ),
    "layer_settings.backends_config.pyannote.model_id.label": "pyannote: model ID",
    "layer_settings.backends_config.pyannote.model_id.help": (
        "HuggingFace model ID. The standard is voice-activity-detection."
    ),
    "layer_settings.backends_config.pyannote.device.label": "pyannote: device",
    "layer_settings.backends_config.pyannote.device.help": "cpu / cuda / mps / auto. Works on CPU but very heavy.",
    "layer_settings.backends_config.pvcobra.threshold.label": "Cobra: threshold (0–1)",
    "layer_settings.backends_config.pvcobra.threshold.help": (
        "Voice probability threshold. Lowering it makes speech easier to pick up."
    ),
    "layer_settings.pipeline.recognized_queue_size.label": "Recognition result buffer count",
    "layer_settings.pipeline.recognized_queue_size.help": (
        "Max item count of the queue passing ASR-recognized text to the translation stage. "
        "Text is a few hundred bytes per utterance, so it is managed by count."
    ),
    "layer_settings.backends_config.faster_whisper.model_size.label": "Whisper model",
    "layer_settings.backends_config.faster_whisper.model_size.help": (
        "Whisper model size. Larger is more accurate but uses more RAM/VRAM and time. "
        "The ✓ recommended / ⚠ heavy / ✗ infeasible icons indicate fit with the current "
        "environment (RAM/VRAM). Apply via the '(Re)load model' button after changing."
    ),
    "layer_settings.backends_config.openai_whisper.model_size.label": "Whisper model (official)",
    "layer_settings.backends_config.openai_whisper.model_size.help": (
        "openai-whisper (official) model size. Tends to be heavier than faster-whisper. "
        "Choose from tiny/base/small/medium/large-v3."
    ),
    "layer_settings.backends_config.openai_whisper_api.model.label": "OpenAI API: model",
    "layer_settings.backends_config.openai_whisper_api.model.help": (
        "Model name for the OpenAI Whisper API. Currently only `whisper-1`."
    ),
    "layer_settings.backends_config.google_stt.default_language.label": "Google STT: default language (when auto)",
    "layer_settings.backends_config.google_stt.default_language.help": (
        "Google STT does not support automatic language detection, so when the input "
        "language is `auto` it calls the API with the language specified here (ISO 639-3)."
    ),
    "layer_settings.backends_config.deepgram.model.label": "Deepgram: model",
    "layer_settings.backends_config.deepgram.model.help": (
        "Deepgram model name (e.g. nova-3 / nova-2). Default is nova-3."
    ),
    "layer_settings.pipeline.translated_queue_size.label": "Translation result buffer count",
    "layer_settings.pipeline.translated_queue_size.help": "Max item count of the queue passing translated text to TTS.",
    "layer_settings.backends_config.nllb200.model_name.label": "NLLB-200 model",
    "layer_settings.backends_config.nllb200.model_name.help": (
        "NLLB-200 model name (HF id). Choose from distilled-600M / distilled-1.3B / "
        "1.3B / 3.3B. GPU recommended for large models."
    ),
    "layer_settings.backends_config.openai_gpt.model.label": "OpenAI GPT: model",
    "layer_settings.backends_config.openai_gpt.model.help": (
        "OpenAI Chat Completions model name (e.g. gpt-4o-mini / gpt-4o)."
    ),
    "layer_settings.backends_config.anthropic_claude.model.label": "Anthropic Claude: model",
    "layer_settings.backends_config.anthropic_claude.model.help": (
        "Anthropic Messages model name (e.g. claude-haiku-4-5-20251001)."
    ),
    "layer_settings.backends_config.sapi.rate.label": "Speech rate (rate)",
    "layer_settings.backends_config.sapi.rate.help": (
        "SAPI (pyttsx3) rate. Default 180 (normal). Faster shortens playback time. "
        "Reloading settings or a restart is needed to apply in the GUI."
    ),
    "layer_settings.backends_config.piper.voice_name.label": "Piper: voice",
    "layer_settings.backends_config.piper.voice_name.help": (
        "Piper voice model name (`<lang_country>-<speaker>-<quality>` format). "
        "Downloaded from Hugging Face `rhasspy/piper-voices` on first use. "
        "No Japanese (ja) voice is distributed by default."
    ),
    "layer_settings.backends_config.elevenlabs.voice_id.label": "ElevenLabs: voice_id",
    "layer_settings.backends_config.elevenlabs.voice_id.help": (
        "ElevenLabs premade voice ID. Default is Rachel (English female). "
        "Get other voice IDs from the Voices page of the ElevenLabs dashboard."
    ),
    "layer_settings.backends_config.elevenlabs.model_id.label": "ElevenLabs: model",
    "layer_settings.backends_config.elevenlabs.model_id.help": (
        "Multilingual: `eleven_multilingual_v2` (29 languages, quality-oriented). "
        "Low latency: `eleven_turbo_v2_5`. "
        "English-only: `eleven_monolingual_v1`."
    ),
    "layer_settings.backends_config.openai_tts.voice.label": "OpenAI TTS: voice",
    "layer_settings.backends_config.openai_tts.voice.help": (
        "6 premade voices (alloy / echo / fable / onyx / nova / shimmer)."
    ),
    "layer_settings.backends_config.openai_tts.model.label": "OpenAI TTS: model",
    "layer_settings.backends_config.openai_tts.model.help": "tts-1 (low latency) / tts-1-hd (high quality).",
    "layer_settings.backends_config.google_tts.voice_name.label": "Google TTS: voice name",
    "layer_settings.backends_config.google_tts.voice_name.help": (
        "Google TTS voice name (e.g. `en-US-Wavenet-A`, `ja-JP-Neural2-B`). "
        "If blank, a default voice is auto-selected from the language code."
    ),
    "layer_settings.backends_config.google_tts.default_language.label": "Google TTS: default language",
    "layer_settings.backends_config.google_tts.default_language.help": (
        "Default language used when `tgt_lang` is empty (ISO 639-3)."
    ),
    "layer_settings.pipeline.synthesized_queue_max_bytes.label": "Output buffer capacity (bytes)",
    "layer_settings.pipeline.synthesized_queue_max_bytes.help": (
        "Byte limit of the buffer passing TTS-synthesized PCM to the playback stage. "
        "At 16kHz×float32, 5MB ≈ about 78 seconds. "
        "Applied when you press '▶ Start'."
    ),
    # common
    "common.cancel": "Cancel",
    "common.ok": "OK",
    # dialog: ProcessSelectDialog
    "dialog.process_select.title": "Select Process — ProcTap",
    "dialog.process_select.heading": "Processes outputting audio",
    "dialog.process_select.description": (
        "Showing processes currently producing sound (or ready to).\n"
        "Select one and press 'Start audition' to check its volume on the meter at right."
    ),
    "dialog.process_select.refresh": "↻ Refresh",
    "dialog.process_select.audition_start": "▶ Start audition",
    "dialog.process_select.audition_stop": "■ Stop",
    "dialog.process_select.no_process": "(No matching process — play sound, then ↻ Refresh)",
    # dialog: ConsentDialog
    "dialog.consent.title": "Cloud Transmission Consent",
    "dialog.consent.heading": "Consent to use a cloud service",
    "dialog.consent.default_data_summary": "Audio data (per-utterance PCM) and text",
    "dialog.consent.body": (
        "backend: {backend}\n"
        "Destination service: {service}\n"
        "Data sent: {summary}\n\n"
        "Using this service sends the above data to an external server. "
        "Please review the service's privacy policy / terms of use and consent."
    ),
    "dialog.consent.terms": "Terms of use: {url}",
    "dialog.consent.suppress": "Do not show this dialog again (suppress_dialogs)",
    "dialog.consent.cancel": "Cancel",
    "dialog.consent.accept": "Consent and use",
    # dialog: CredentialDialog
    "dialog.credential.title": "Enter credentials — {service}",
    "dialog.credential.description": (
        "Enter your API key etc. in the fields below and press 'Test'. "
        "On success it is saved and the 'Start' button becomes available."
    ),
    "dialog.credential.no_spec": "This backend does not require credentials.",
    "dialog.credential.cancel": "Cancel",
    "dialog.credential.test": "Test",
    "dialog.credential.testing": "Testing…",
    "dialog.credential.browse": "Browse…",
    "dialog.credential.placeholder_file": "(choose with the Browse button)",
    "dialog.credential.placeholder_set": "●●●●●●●● (set; enter only to change)",
    "dialog.credential.placeholder_unset": "(not set)",
    "dialog.credential.file_picker_title": "Select the file for {label}",
    "dialog.credential.missing_fields": "Some fields are empty: {fields}",
    "dialog.credential.internal_error": "Internal error during verification: {error}",
    "dialog.credential.ok": "✓ Auth OK — {message}",
    "dialog.credential.saved": "Saved",
    "dialog.credential.failed": "✗ Auth failed — {message}",
    "dialog.credential.unknown_cause": "Unknown cause",
    # layer 表示名
    "layer.capture": "Audio Capture",
    "layer.vad": "VAD",
    "layer.asr": "ASR (Transcription)",
    "layer.translator": "Translation",
    "layer.tts": "TTS (Speech Synthesis)",
    "layer.output": "Audio Output",
    # dialog: LayerSettingsDialog
    "dialog.layer_settings.title": "{layer} settings",
    "dialog.layer_settings.unselected": "(not selected)",
    "dialog.layer_settings.backend_line": "Backend: {backend}",
    "dialog.layer_settings.no_fields": "This layer has no editable settings.",
    "dialog.layer_settings.cancel": "Cancel",
    "dialog.layer_settings.save": "Save",
    "dialog.layer_settings.unsupported_suffix": "(unsupported type: {ft})",
    "dialog.layer_settings.no_options": "(no options)",
    "dialog.layer_settings.cred_placeholder_unset": "(not set)",
    "dialog.layer_settings.cred_placeholder_set": "●●●●●●●● (set; enter only to change)",
    "dialog.layer_settings.auth": "Auth",
    "dialog.layer_settings.auth_verified": "✓ Verified",
    "dialog.layer_settings.auth_unverified": "Not verified",
    "dialog.layer_settings.auth_open": "Open auth / re-authenticate",
    "dialog.layer_settings.auth_help": (
        "Enter your API key etc. and pass the connectivity check to save with verified=True. "
        "If an error occurs during operation due to an expired key / lapsed subscription, "
        "verified automatically reverts to False."
    ),
    "dialog.layer_settings.auth_saved": "Auth OK has been saved.",
    "dialog.layer_settings.input_error": "Input error ({label}): {error}",
    "dialog.layer_settings.cred_save_failed": "Failed to save credentials: {error}",
    "dialog.layer_settings.saved_reload": (
        "Saved. Press the central '↻ Load' button to apply the new settings."
    ),
    "dialog.layer_settings.saved_pipeline": (
        "Saved. pipeline values apply on the next '▶ Start'."
    ),
    # dialog: LanguageSelectDialog
    "dialog.language_select.title": "Select Language",
    "dialog.language_select.search_label": "Search language (code / English name):",
    "dialog.language_select.search_placeholder": "e.g. swahili / swh",
    "dialog.language_select.no_match": "(no match)",
    # ControlPanel
    "control_panel.section_action": "Operation",
    "control_panel.section_status": "Status",
    "control_panel.latency_none": "Avg latency: -",
    "control_panel.latency": "Avg latency: {avg} s (last {count})",
    "control_panel.accel_init": "Compute: -",
    "control_panel.clear_events": "Clear actions",
    "control_panel.recent_translations": "Recent translations:",
    "control_panel.clear": "Clear",
    "control_panel.test_playback_text": "Test audio",
    "control_panel.playing": "Playing…",
    "control_panel.starting": "Starting…",
    "control_panel.stopping": "Stopping…",
    "control_panel.running": "Running",
    "control_panel.stop": "■ Stop",
    "control_panel.load_btn_starting": "(starting)",
    "control_panel.load_btn_stopping": "(stopping)",
    "control_panel.load_btn_running": "(running)",
    "control_panel.test_btn_running": "🔊 (running)",
    "control_panel.idle_start_failed": "Stopped (start failed)",
    "control_panel.idle_error": "Stopped (error)",
    "control_panel.load_start_failed": "Failed to start load: {error}",
    "control_panel.load_failed": "Load failed: {error}",
    "control_panel.event_load_failed": "[Load failed] {error}",
    "control_panel.event_test_done": "[Output test] playback done: {text}",
    "control_panel.test_failed": "Output test failed: {error}",
    "control_panel.event_test_failed": "[Output test failed] {error}",
    "control_panel.start_failed": "Start failed: {error}",
    "control_panel.event_start_failed": "[Start failed] {error}",
    "control_panel.event_stop_exception": "[Stop exception] {error}",
    "control_panel.event_fatal": "[Fatal error] {error}",
    "control_panel.suppressed_suffix": " (+{count} suppressed)",
    "control_panel.status_fetch_failed": "(failed to fetch status: {error})",
    # SettingsPanel
    "settings_panel.section_backends": "Backends",
    "settings_panel.section_devices": "Devices",
    "settings_panel.section_languages": "Translation",
    "settings_panel.config_btn": "Settings",
    "settings_panel.input_device": "Input device:",
    "settings_panel.output_device": "Output device:",
    "settings_panel.enumerating": "(enumerating)",
    "settings_panel.process_select": "Select process…",
    "settings_panel.pid_selected": "PID {pid} ▼",
    "settings_panel.src_lang": "Input language (src):",
    "settings_panel.tgt_lang": "Output language (tgt):",
    "settings_panel.log_dir": "Log output dir:",
    "settings_panel.save": "Save settings",
    "settings_panel.reload": "Reload settings",
    "settings_panel.redetect_devices": "Re-detect devices",
    "settings_panel.unselected": "(not selected)",
    "settings_panel.fetch_failed": "(fetch failed: {error})",
    "settings_panel.no_input_device": "(no input device)",
    "settings_panel.no_output_device": "(no output device)",
    "settings_panel.process_dialog_failed": "Failed to open process select dialog: {error}",
    "settings_panel.save_failed": "Save failed: {error}",
    "settings_panel.saved": "Settings saved",
    "settings_panel.reload_blocked": "Cannot reload settings while running (stop first)",
    "settings_panel.reload_failed": "Reload failed: {error}",
    "settings_panel.reloaded": "Settings reloaded",
    # MainWindow
    "main_window.locale_running_blocked": "Cannot switch display language while running (stop first)",
}

# 中国語(簡体)カタログ(Phase 4b)。キー集合・placeholder 名は _JA と一致させる。
_ZH: dict[str, str] = {
    "ready.toggle.auth_missing": "未设置认证信息",
    "ready.status.auth_missing": "未设置认证信息(请在详情对话框中设置)",
    "ready.toggle.auth_unverified": "认证未验证",
    "ready.status.auth_unverified": "认证未验证(请在详情对话框的“认证”中测试)",
    "ready.toggle.downloading": "正在下载模型…",
    "ready.status.downloading": "正在下载模型…",
    "ready.toggle.no_process": "未选择进程",
    "ready.status.no_process": "请选择进程(设置 → 选择进程…)",
    "ready.toggle.start": "▶ 开始",
    "ready.status.idle_will_load": "已停止(按下时加载)",
    "ready.status.idle_loading": "已停止(加载中)",
    "ready.status.idle": "已停止",
    "ready.load.loaded": "已加载",
    "ready.load.loading": "加载中…",
    "ready.load.load": "↻ 加载",
    "ready.test.tts_none": "🔊 (无 TTS)",
    "ready.test.no_output": "🔊 未选择输出",
    "ready.test.run": "🔊 输出测试",
    "language.src_fallback": "已将输入语言从 {old} 更改为 {new}({backend} 不支持 {code})",
    "language.tgt_fallback": "已将输出语言从 {old} 更改为 {new}({backend} 不支持 {code})",
    "language.tts_warning": (
        "TTS 后端 {backend} 不支持朗读语言 {lang}"
        "(请更改 Translator 的输出语言,或切换到其他 TTS 后端)"
    ),
    "accel.gpu": "运算: GPU ({devices})",
    "accel.cpu": "运算: 仅 CPU",
    "accel.preparing": "运算: -(正在准备模型)",
    "status.recent_errors": "最近的错误:",
    "status.gui_events": "操作事件:",
    "status.layer_skipped": "(无)",
    "status.layer_absorbed": "(由 {into} 的 {backend} 执行)",
    "backend.tts_none": "(无)",
    "backend.skipped_status": "(无)",
    "backend.unregistered": "(未注册)",
    "capture_kind.device": "设备",
    "capture_kind.process": "进程",
    "restart.device.input": "输入",
    "restart.device.output": "输出",
    "restart.started": "已切换{device}设备(正在重启…)",
    "restart.failed": "更改{device}设备后重启失败: {message}",
    "layer_settings.auto_load.label": "启动时自动加载",
    "layer_settings.auto_load.help": (
        "开启后,应用启动时会自动加载此后端(默认关闭)。"
        "若保持关闭,则在按下“▶ 开始”时加载。"
    ),
    "layer_settings.load_model.label": "(重新)加载模型",
    "layer_settings.load_model.help": (
        "立即在后台(重新)加载此层的后端。"
        "即使已加载,也会先卸载一次并用新设置重建。"
    ),
    "layer_settings.recent_durations.label": "最近处理时间",
    "layer_settings.recent_durations.help": "最近 5 次完成发话的平均处理时间。",
    "layer_settings.recent_durations.none": "无最近数据",
    "layer_settings.recent_durations.average": "最近 {count} 次平均: {avg} ms",
    "layer_settings.pipeline.captured_queue_max_bytes.label": "输入缓冲容量 (bytes)",
    "layer_settings.pipeline.captured_queue_max_bytes.help": (
        "将 VAD 输出的 PCM 传给下一阶段(ASR)的缓冲字节上限。"
        "16kHz×float32 下 10MB ≈ 约 156 秒。"
        "按下“▶ 开始”时生效。"
    ),
    "layer_settings.backends_config.proctap.input_gain.label": "ProcTap: 输入增益 (倍率)",
    "layer_settings.backends_config.proctap.input_gain.help": (
        "对采集音频施加的放大倍率(1.0=原样,2–8 左右为参考)。"
        "当目标应用音量过低无法识别时调高(±1.0 处削波)。"
        "音量 0 无法放大。更改后通过“(重新)加载模型”生效。"
    ),
    "layer_settings.backends_config.webrtcvad.aggressiveness.label": "WebRTC: 灵敏度 (0=低 – 3=高)",
    "layer_settings.backends_config.webrtcvad.aggressiveness.help": (
        "设为 3 会使语音判定更严格——更不易被噪声误判,但会漏掉更多语音。"
    ),
    "layer_settings.backends_config.webrtcvad.frame_ms.label": "WebRTC: 帧长 (ms)",
    "layer_settings.backends_config.webrtcvad.frame_ms.help": (
        "10 / 20 / 30 之一。越短反应越快但 CPU 负载↑。"
    ),
    "layer_settings.backends_config.pyannote.model_id.label": "pyannote: 模型 ID",
    "layer_settings.backends_config.pyannote.model_id.help": (
        "HuggingFace 的模型 ID。标准为 voice-activity-detection。"
    ),
    "layer_settings.backends_config.pyannote.device.label": "pyannote: device",
    "layer_settings.backends_config.pyannote.device.help": "cpu / cuda / mps / auto。CPU 也能运行但非常慢。",
    "layer_settings.backends_config.pvcobra.threshold.label": "Cobra: 阈值 (0–1)",
    "layer_settings.backends_config.pvcobra.threshold.help": (
        "voice probability 的阈值。调低会更容易拾取语音。"
    ),
    "layer_settings.pipeline.recognized_queue_size.label": "识别结果缓冲数量",
    "layer_settings.pipeline.recognized_queue_size.help": (
        "将 ASR 识别文本传给翻译阶段的队列上限数量。"
        "文本每条发话仅数百字节,故按数量管理。"
    ),
    "layer_settings.backends_config.faster_whisper.model_size.label": "Whisper 模型",
    "layer_settings.backends_config.faster_whisper.model_size.help": (
        "Whisper 的模型大小。越大越精确,但占用更多 RAM/VRAM 和时间。"
        "✓ 推荐 / ⚠ 偏重 / ✗ 不可 图标表示与当前环境(RAM/VRAM)的适配度。"
        "更改后通过“(重新)加载模型”按钮生效。"
    ),
    "layer_settings.backends_config.openai_whisper.model_size.label": "Whisper 模型(官方)",
    "layer_settings.backends_config.openai_whisper.model_size.help": (
        "openai-whisper(官方)的模型大小。往往比 faster-whisper 更重。"
        "可从 tiny/base/small/medium/large-v3 选择。"
    ),
    "layer_settings.backends_config.openai_whisper_api.model.label": "OpenAI API: 模型",
    "layer_settings.backends_config.openai_whisper_api.model.help": (
        "OpenAI Whisper API 的模型名。目前仅 `whisper-1`。"
    ),
    "layer_settings.backends_config.google_stt.default_language.label": "Google STT: 默认语言(auto 时)",
    "layer_settings.backends_config.google_stt.default_language.help": (
        "Google STT 不支持自动语言检测,因此当输入语言为 `auto` 时,"
        "会使用此处指定的语言(ISO 639-3)调用 API。"
    ),
    "layer_settings.backends_config.deepgram.model.label": "Deepgram: 模型",
    "layer_settings.backends_config.deepgram.model.help": (
        "Deepgram 的模型名(例: nova-3 / nova-2)。默认是 nova-3。"
    ),
    "layer_settings.pipeline.translated_queue_size.label": "翻译结果缓冲数量",
    "layer_settings.pipeline.translated_queue_size.help": "将翻译后文本传给 TTS 的队列上限数量。",
    "layer_settings.backends_config.nllb200.model_name.label": "NLLB-200 模型",
    "layer_settings.backends_config.nllb200.model_name.help": (
        "NLLB-200 的模型名(HF id)。可从 distilled-600M / distilled-1.3B / "
        "1.3B / 3.3B 选择。大型推荐 GPU。"
    ),
    "layer_settings.backends_config.openai_gpt.model.label": "OpenAI GPT: 模型",
    "layer_settings.backends_config.openai_gpt.model.help": (
        "OpenAI Chat Completions 的模型名(例: gpt-4o-mini / gpt-4o)。"
    ),
    "layer_settings.backends_config.anthropic_claude.model.label": "Anthropic Claude: 模型",
    "layer_settings.backends_config.anthropic_claude.model.help": (
        "Anthropic Messages 的模型名(例: claude-haiku-4-5-20251001)。"
    ),
    "layer_settings.backends_config.sapi.rate.label": "朗读速度 (rate)",
    "layer_settings.backends_config.sapi.rate.help": (
        "SAPI(pyttsx3)的 rate。默认 180(普通)。加快会缩短播放时间。"
        "在 GUI 中生效需要“重新加载设置”或重启。"
    ),
    "layer_settings.backends_config.piper.voice_name.label": "Piper: voice",
    "layer_settings.backends_config.piper.voice_name.help": (
        "Piper 的 voice 模型名(`<lang_country>-<speaker>-<quality>` 格式)。"
        "首次使用时从 Hugging Face `rhasspy/piper-voices` 下载。"
        "默认不分发日语(ja)voice。"
    ),
    "layer_settings.backends_config.elevenlabs.voice_id.label": "ElevenLabs: voice_id",
    "layer_settings.backends_config.elevenlabs.voice_id.help": (
        "ElevenLabs 的预制 voice ID。默认是 Rachel(英语女声)。"
        "其他 voice 可从 ElevenLabs 仪表盘的 Voices 页面获取 ID。"
    ),
    "layer_settings.backends_config.elevenlabs.model_id.label": "ElevenLabs: 模型",
    "layer_settings.backends_config.elevenlabs.model_id.help": (
        "多语言: `eleven_multilingual_v2`(29 种语言,偏质量)。"
        "低延迟: `eleven_turbo_v2_5`。"
        "仅英语: `eleven_monolingual_v1`。"
    ),
    "layer_settings.backends_config.openai_tts.voice.label": "OpenAI TTS: voice",
    "layer_settings.backends_config.openai_tts.voice.help": (
        "6 个预制 voice(alloy / echo / fable / onyx / nova / shimmer)。"
    ),
    "layer_settings.backends_config.openai_tts.model.label": "OpenAI TTS: 模型",
    "layer_settings.backends_config.openai_tts.model.help": "tts-1(低延迟)/ tts-1-hd(高质量)。",
    "layer_settings.backends_config.google_tts.voice_name.label": "Google TTS: voice 名称",
    "layer_settings.backends_config.google_tts.voice_name.help": (
        "Google TTS 的 voice 名称(例: `en-US-Wavenet-A`、`ja-JP-Neural2-B`)。"
        "留空则根据语言代码自动选择默认 voice。"
    ),
    "layer_settings.backends_config.google_tts.default_language.label": "Google TTS: 默认语言",
    "layer_settings.backends_config.google_tts.default_language.help": (
        "当 `tgt_lang` 为空时使用的默认语言(ISO 639-3)。"
    ),
    "layer_settings.pipeline.synthesized_queue_max_bytes.label": "输出缓冲容量 (bytes)",
    "layer_settings.pipeline.synthesized_queue_max_bytes.help": (
        "将 TTS 合成的 PCM 传给播放阶段的缓冲字节上限。"
        "16kHz×float32 下 5MB ≈ 约 78 秒。"
        "按下“▶ 开始”时生效。"
    ),
    "common.cancel": "取消",
    "common.ok": "确定",
    "dialog.process_select.title": "选择进程 — ProcTap",
    "dialog.process_select.heading": "正在输出音频的进程",
    "dialog.process_select.description": (
        "正在显示当前发声(或已准备发声)的进程。\n"
        "选择后按“开始试听”可在右侧音量表确认该进程的音量。"
    ),
    "dialog.process_select.refresh": "↻ 刷新",
    "dialog.process_select.audition_start": "▶ 开始试听",
    "dialog.process_select.audition_stop": "■ 停止",
    "dialog.process_select.no_process": "(无匹配进程 — 发声后再 ↻ 刷新)",
    "dialog.consent.title": "云端发送同意确认",
    "dialog.consent.heading": "同意使用云服务",
    "dialog.consent.default_data_summary": "音频数据(按发话的 PCM)和文本",
    "dialog.consent.body": (
        "backend: {backend}\n"
        "发送目标服务: {service}\n"
        "发送内容: {summary}\n\n"
        "使用此服务会将上述数据发送到外部服务器。"
        "请确认该服务的隐私政策/使用条款后同意。"
    ),
    "dialog.consent.terms": "使用条款: {url}",
    "dialog.consent.suppress": "今后不再显示此对话框(suppress_dialogs)",
    "dialog.consent.cancel": "取消",
    "dialog.consent.accept": "同意并使用",
    "dialog.credential.title": "输入认证信息 — {service}",
    "dialog.credential.description": (
        "在下方字段输入 API 密钥等并按“测试”。"
        "成功后会保存,并可按下“开始”按钮。"
    ),
    "dialog.credential.no_spec": "此后端不需要认证信息。",
    "dialog.credential.cancel": "取消",
    "dialog.credential.test": "测试",
    "dialog.credential.testing": "测试中…",
    "dialog.credential.browse": "浏览…",
    "dialog.credential.placeholder_file": "(用浏览按钮选择)",
    "dialog.credential.placeholder_set": "●●●●●●●● (已设置,仅在更改时输入)",
    "dialog.credential.placeholder_unset": "(未设置)",
    "dialog.credential.file_picker_title": "选择 {label} 的文件",
    "dialog.credential.missing_fields": "有未填写的字段: {fields}",
    "dialog.credential.internal_error": "验证时发生内部错误: {error}",
    "dialog.credential.ok": "✓ 认证成功 — {message}",
    "dialog.credential.saved": "已保存",
    "dialog.credential.failed": "✗ 认证失败 — {message}",
    "dialog.credential.unknown_cause": "原因不明",
    "layer.capture": "音频采集",
    "layer.vad": "VAD",
    "layer.asr": "ASR(转写)",
    "layer.translator": "翻译",
    "layer.tts": "TTS(语音合成)",
    "layer.output": "音频输出",
    "dialog.layer_settings.title": "{layer} 设置",
    "dialog.layer_settings.unselected": "(未选择)",
    "dialog.layer_settings.backend_line": "后端: {backend}",
    "dialog.layer_settings.no_fields": "此层没有可编辑的设置。",
    "dialog.layer_settings.cancel": "取消",
    "dialog.layer_settings.save": "保存",
    "dialog.layer_settings.unsupported_suffix": "(不支持的类型: {ft})",
    "dialog.layer_settings.no_options": "(无选项)",
    "dialog.layer_settings.cred_placeholder_unset": "(未设置)",
    "dialog.layer_settings.cred_placeholder_set": "●●●●●●●● (已设置,仅在更改时输入)",
    "dialog.layer_settings.auth": "认证",
    "dialog.layer_settings.auth_verified": "✓ 已认证",
    "dialog.layer_settings.auth_unverified": "未认证",
    "dialog.layer_settings.auth_open": "打开认证 / 重新认证",
    "dialog.layer_settings.auth_help": (
        "输入 API 密钥等并通过连通性检查后,将以 verified=True 保存。"
        "若因密钥失效 / 订阅过期等在运行中出错,verified 会自动恢复为 False。"
    ),
    "dialog.layer_settings.auth_saved": "认证成功已保存。",
    "dialog.layer_settings.input_error": "输入错误({label}): {error}",
    "dialog.layer_settings.cred_save_failed": "保存认证信息失败: {error}",
    "dialog.layer_settings.saved_reload": (
        "已保存。请按中央的“↻ 加载”按钮以应用新设置。"
    ),
    "dialog.layer_settings.saved_pipeline": (
        "已保存。pipeline 值将在下次“▶ 开始”时应用。"
    ),
    "dialog.language_select.title": "选择语言",
    "dialog.language_select.search_label": "搜索语言(代码 / 英文名):",
    "dialog.language_select.search_placeholder": "例: swahili / swh",
    "dialog.language_select.no_match": "(无匹配)",
    "control_panel.section_action": "运行",
    "control_panel.section_status": "状态",
    "control_panel.latency_none": "平均延迟: -",
    "control_panel.latency": "平均延迟: {avg} 秒(最近{count}条)",
    "control_panel.accel_init": "运算: -",
    "control_panel.clear_events": "清除操作事件",
    "control_panel.recent_translations": "最近的翻译:",
    "control_panel.clear": "清除",
    "control_panel.test_playback_text": "测试音频",
    "control_panel.playing": "播放中…",
    "control_panel.starting": "正在开始…",
    "control_panel.stopping": "正在停止…",
    "control_panel.running": "运行中",
    "control_panel.stop": "■ 停止",
    "control_panel.load_btn_starting": "(启动中)",
    "control_panel.load_btn_stopping": "(停止中)",
    "control_panel.load_btn_running": "(运行中)",
    "control_panel.test_btn_running": "🔊 (运行中)",
    "control_panel.idle_start_failed": "已停止(启动失败)",
    "control_panel.idle_error": "已停止(错误)",
    "control_panel.load_start_failed": "启动加载失败: {error}",
    "control_panel.load_failed": "加载失败: {error}",
    "control_panel.event_load_failed": "[加载失败] {error}",
    "control_panel.event_test_done": "[输出测试] 播放完成: {text}",
    "control_panel.test_failed": "输出测试失败: {error}",
    "control_panel.event_test_failed": "[输出测试失败] {error}",
    "control_panel.start_failed": "启动失败: {error}",
    "control_panel.event_start_failed": "[启动失败] {error}",
    "control_panel.event_stop_exception": "[停止时异常] {error}",
    "control_panel.event_fatal": "[致命错误] {error}",
    "control_panel.suppressed_suffix": " (+{count}条被抑制)",
    "control_panel.status_fetch_failed": "(获取状态失败: {error})",
    "settings_panel.section_backends": "后端",
    "settings_panel.section_devices": "设备",
    "settings_panel.section_languages": "翻译",
    "settings_panel.config_btn": "设置",
    "settings_panel.input_device": "输入设备:",
    "settings_panel.output_device": "输出设备:",
    "settings_panel.enumerating": "(枚举中)",
    "settings_panel.process_select": "选择进程…",
    "settings_panel.pid_selected": "PID {pid} ▼",
    "settings_panel.src_lang": "输入语言 (src):",
    "settings_panel.tgt_lang": "输出语言 (tgt):",
    "settings_panel.log_dir": "日志输出目录:",
    "settings_panel.save": "保存设置",
    "settings_panel.reload": "重新加载设置",
    "settings_panel.redetect_devices": "重新检测设备",
    "settings_panel.unselected": "(未选择)",
    "settings_panel.fetch_failed": "(获取失败: {error})",
    "settings_panel.no_input_device": "(无输入设备)",
    "settings_panel.no_output_device": "(无输出设备)",
    "settings_panel.process_dialog_failed": "打开进程选择对话框失败: {error}",
    "settings_panel.save_failed": "保存失败: {error}",
    "settings_panel.saved": "设置已保存",
    "settings_panel.reload_blocked": "运行中无法重新加载设置(请先停止)",
    "settings_panel.reload_failed": "加载失败: {error}",
    "settings_panel.reloaded": "设置已重新加载",
    "main_window.locale_running_blocked": "运行中无法切换显示语言(请先停止)",
}

# スペイン語カタログ(Phase 4b)。キー集合・placeholder 名は _JA と一致させる。
_ES: dict[str, str] = {
    "ready.toggle.auth_missing": "Credenciales no configuradas",
    "ready.status.auth_missing": "Credenciales no configuradas (configúrelas en el diálogo de detalles)",
    "ready.toggle.auth_unverified": "Auth no verificada",
    "ready.status.auth_unverified": "Auth no verificada (pruébela en la sección 'Auth' del diálogo de detalles)",
    "ready.toggle.downloading": "Descargando modelo…",
    "ready.status.downloading": "Descargando modelo…",
    "ready.toggle.no_process": "Ningún proceso seleccionado",
    "ready.status.no_process": "Seleccione un proceso (Configuración → Seleccionar proceso…)",
    "ready.toggle.start": "▶ Iniciar",
    "ready.status.idle_will_load": "Detenido (carga al pulsar)",
    "ready.status.idle_loading": "Detenido (cargando)",
    "ready.status.idle": "Detenido",
    "ready.load.loaded": "Cargado",
    "ready.load.loading": "Cargando…",
    "ready.load.load": "↻ Cargar",
    "ready.test.tts_none": "🔊 (Sin TTS)",
    "ready.test.no_output": "🔊 Sin salida seleccionada",
    "ready.test.run": "🔊 Probar salida",
    "language.src_fallback": "Idioma de entrada cambiado de {old} a {new} ({backend} no admite {code})",
    "language.tgt_fallback": "Idioma de salida cambiado de {old} a {new} ({backend} no admite {code})",
    "language.tts_warning": (
        "El backend de TTS {backend} no admite el idioma de lectura {lang} "
        "(cambie el idioma de salida del Translator o cambie a otro backend de TTS)"
    ),
    "accel.gpu": "Cómputo: GPU ({devices})",
    "accel.cpu": "Cómputo: solo CPU",
    "accel.preparing": "Cómputo: - (preparando modelo)",
    "status.recent_errors": "Errores recientes:",
    "status.gui_events": "Acciones:",
    "status.layer_skipped": "(ninguno)",
    "status.layer_absorbed": "(ejecutado por el {backend} de {into})",
    "backend.tts_none": "(ninguno)",
    "backend.skipped_status": "(ninguno)",
    "backend.unregistered": "(no registrado)",
    "capture_kind.device": "Dispositivo",
    "capture_kind.process": "Proceso",
    "restart.device.input": "entrada",
    "restart.device.output": "salida",
    "restart.started": "Dispositivo de {device} cambiado (reiniciando…)",
    "restart.failed": "Error al reiniciar tras cambiar el dispositivo de {device}: {message}",
    "layer_settings.auto_load.label": "Cargar automáticamente al iniciar",
    "layer_settings.auto_load.help": (
        "Si está activado, este backend se carga automáticamente al iniciar la aplicación "
        "(predeterminado: desactivado). Si se deja desactivado, se carga al pulsar '▶ Iniciar'."
    ),
    "layer_settings.load_model.label": "(Re)cargar modelo",
    "layer_settings.load_model.help": (
        "(Re)cargar ahora el backend de esta capa en segundo plano. "
        "Aunque ya esté cargado, se descarga una vez y se reconstruye con la nueva configuración."
    ),
    "layer_settings.recent_durations.label": "Tiempo de procesamiento reciente",
    "layer_settings.recent_durations.help": "Tiempo medio de procesamiento de las últimas 5 elocuciones completadas.",
    "layer_settings.recent_durations.none": "Sin datos recientes",
    "layer_settings.recent_durations.average": "Promedio de las últimas {count}: {avg} ms",
    "layer_settings.pipeline.captured_queue_max_bytes.label": "Capacidad del búfer de entrada (bytes)",
    "layer_settings.pipeline.captured_queue_max_bytes.help": (
        "Límite de bytes del búfer que pasa el PCM de salida del VAD a la siguiente etapa (ASR). "
        "A 16kHz×float32, 10MB ≈ unos 156 segundos. "
        "Se aplica al pulsar '▶ Iniciar'."
    ),
    "layer_settings.backends_config.proctap.input_gain.label": "ProcTap: ganancia de entrada (multiplicador)",
    "layer_settings.backends_config.proctap.input_gain.help": (
        "Multiplicador de amplificación aplicado al audio capturado (1.0=unidad, 2–8 como guía). "
        "Súbalo cuando el volumen de la app objetivo sea demasiado bajo para reconocer (satura en ±1.0). "
        "Un volumen de 0 no se puede amplificar. Aplique con '(Re)cargar modelo' tras el cambio."
    ),
    "layer_settings.backends_config.webrtcvad.aggressiveness.label": "WebRTC: sensibilidad (0=baja – 3=alta)",
    "layer_settings.backends_config.webrtcvad.aggressiveness.help": (
        "Ponerlo en 3 hace más estricta la detección de voz: menos falsos positivos por ruido, "
        "pero más voz perdida."
    ),
    "layer_settings.backends_config.webrtcvad.frame_ms.label": "WebRTC: longitud de trama (ms)",
    "layer_settings.backends_config.webrtcvad.frame_ms.help": (
        "Uno de 10 / 20 / 30. Más corto reacciona más rápido pero aumenta la carga de CPU↑."
    ),
    "layer_settings.backends_config.pyannote.model_id.label": "pyannote: ID de modelo",
    "layer_settings.backends_config.pyannote.model_id.help": (
        "ID de modelo de HuggingFace. El estándar es voice-activity-detection."
    ),
    "layer_settings.backends_config.pyannote.device.label": "pyannote: device",
    "layer_settings.backends_config.pyannote.device.help": "cpu / cuda / mps / auto. Funciona en CPU pero muy lento.",
    "layer_settings.backends_config.pvcobra.threshold.label": "Cobra: umbral (0–1)",
    "layer_settings.backends_config.pvcobra.threshold.help": (
        "Umbral de voice probability. Bajarlo facilita captar la voz."
    ),
    "layer_settings.pipeline.recognized_queue_size.label": "Número del búfer de resultados de reconocimiento",
    "layer_settings.pipeline.recognized_queue_size.help": (
        "Número máximo de elementos de la cola que pasa el texto reconocido por ASR a la etapa "
        "de traducción. El texto son unos cientos de bytes por elocución, por eso se gestiona por número."
    ),
    "layer_settings.backends_config.faster_whisper.model_size.label": "Modelo Whisper",
    "layer_settings.backends_config.faster_whisper.model_size.help": (
        "Tamaño del modelo Whisper. Más grande es más preciso pero usa más RAM/VRAM y tiempo. "
        "Los iconos ✓ recomendado / ⚠ pesado / ✗ inviable indican la adecuación al entorno "
        "actual (RAM/VRAM). Aplique con el botón '(Re)cargar modelo' tras el cambio."
    ),
    "layer_settings.backends_config.openai_whisper.model_size.label": "Modelo Whisper (oficial)",
    "layer_settings.backends_config.openai_whisper.model_size.help": (
        "Tamaño del modelo de openai-whisper (oficial). Suele ser más pesado que faster-whisper. "
        "Elija entre tiny/base/small/medium/large-v3."
    ),
    "layer_settings.backends_config.openai_whisper_api.model.label": "OpenAI API: modelo",
    "layer_settings.backends_config.openai_whisper_api.model.help": (
        "Nombre del modelo de la API de OpenAI Whisper. Actualmente solo `whisper-1`."
    ),
    "layer_settings.backends_config.google_stt.default_language.label": "Google STT: idioma predeterminado (cuando es auto)",
    "layer_settings.backends_config.google_stt.default_language.help": (
        "Google STT no admite detección automática de idioma, así que cuando el idioma de entrada "
        "es `auto` llama a la API con el idioma especificado aquí (ISO 639-3)."
    ),
    "layer_settings.backends_config.deepgram.model.label": "Deepgram: modelo",
    "layer_settings.backends_config.deepgram.model.help": (
        "Nombre del modelo de Deepgram (p. ej. nova-3 / nova-2). El predeterminado es nova-3."
    ),
    "layer_settings.pipeline.translated_queue_size.label": "Número del búfer de resultados de traducción",
    "layer_settings.pipeline.translated_queue_size.help": "Número máximo de elementos de la cola que pasa el texto traducido a TTS.",
    "layer_settings.backends_config.nllb200.model_name.label": "Modelo NLLB-200",
    "layer_settings.backends_config.nllb200.model_name.help": (
        "Nombre del modelo NLLB-200 (HF id). Elija entre distilled-600M / distilled-1.3B / "
        "1.3B / 3.3B. Se recomienda GPU para modelos grandes."
    ),
    "layer_settings.backends_config.openai_gpt.model.label": "OpenAI GPT: modelo",
    "layer_settings.backends_config.openai_gpt.model.help": (
        "Nombre del modelo de OpenAI Chat Completions (p. ej. gpt-4o-mini / gpt-4o)."
    ),
    "layer_settings.backends_config.anthropic_claude.model.label": "Anthropic Claude: modelo",
    "layer_settings.backends_config.anthropic_claude.model.help": (
        "Nombre del modelo de Anthropic Messages (p. ej. claude-haiku-4-5-20251001)."
    ),
    "layer_settings.backends_config.sapi.rate.label": "Velocidad de lectura (rate)",
    "layer_settings.backends_config.sapi.rate.help": (
        "rate de SAPI (pyttsx3). Predeterminado 180 (normal). Más rápido acorta el tiempo de "
        "reproducción. Para aplicarlo en la GUI se necesita 'Recargar configuración' o reiniciar."
    ),
    "layer_settings.backends_config.piper.voice_name.label": "Piper: voice",
    "layer_settings.backends_config.piper.voice_name.help": (
        "Nombre del modelo de voz de Piper (formato `<lang_country>-<speaker>-<quality>`). "
        "Se descarga de Hugging Face `rhasspy/piper-voices` en el primer uso. "
        "No se distribuye voz en japonés (ja) por defecto."
    ),
    "layer_settings.backends_config.elevenlabs.voice_id.label": "ElevenLabs: voice_id",
    "layer_settings.backends_config.elevenlabs.voice_id.help": (
        "ID de voz prefabricada de ElevenLabs. El predeterminado es Rachel (voz femenina en inglés). "
        "Obtenga otros IDs de voz en la página Voices del panel de ElevenLabs."
    ),
    "layer_settings.backends_config.elevenlabs.model_id.label": "ElevenLabs: modelo",
    "layer_settings.backends_config.elevenlabs.model_id.help": (
        "Multilingüe: `eleven_multilingual_v2` (29 idiomas, orientado a calidad). "
        "Baja latencia: `eleven_turbo_v2_5`. "
        "Solo inglés: `eleven_monolingual_v1`."
    ),
    "layer_settings.backends_config.openai_tts.voice.label": "OpenAI TTS: voice",
    "layer_settings.backends_config.openai_tts.voice.help": (
        "6 voces prefabricadas (alloy / echo / fable / onyx / nova / shimmer)."
    ),
    "layer_settings.backends_config.openai_tts.model.label": "OpenAI TTS: modelo",
    "layer_settings.backends_config.openai_tts.model.help": "tts-1 (baja latencia) / tts-1-hd (alta calidad).",
    "layer_settings.backends_config.google_tts.voice_name.label": "Google TTS: nombre de voice",
    "layer_settings.backends_config.google_tts.voice_name.help": (
        "Nombre de voz de Google TTS (p. ej. `en-US-Wavenet-A`, `ja-JP-Neural2-B`). "
        "Si está vacío, se selecciona automáticamente una voz predeterminada según el código de idioma."
    ),
    "layer_settings.backends_config.google_tts.default_language.label": "Google TTS: idioma predeterminado",
    "layer_settings.backends_config.google_tts.default_language.help": (
        "Idioma predeterminado usado cuando `tgt_lang` está vacío (ISO 639-3)."
    ),
    "layer_settings.pipeline.synthesized_queue_max_bytes.label": "Capacidad del búfer de salida (bytes)",
    "layer_settings.pipeline.synthesized_queue_max_bytes.help": (
        "Límite de bytes del búfer que pasa el PCM sintetizado por TTS a la etapa de reproducción. "
        "A 16kHz×float32, 5MB ≈ unos 78 segundos. "
        "Se aplica al pulsar '▶ Iniciar'."
    ),
    "common.cancel": "Cancelar",
    "common.ok": "Aceptar",
    "dialog.process_select.title": "Seleccionar proceso — ProcTap",
    "dialog.process_select.heading": "Procesos que emiten audio",
    "dialog.process_select.description": (
        "Mostrando los procesos que producen sonido actualmente (o están listos para hacerlo).\n"
        "Seleccione uno y pulse 'Iniciar audición' para comprobar su volumen en el medidor de la derecha."
    ),
    "dialog.process_select.refresh": "↻ Actualizar",
    "dialog.process_select.audition_start": "▶ Iniciar audición",
    "dialog.process_select.audition_stop": "■ Detener",
    "dialog.process_select.no_process": "(Ningún proceso coincidente — reproduzca sonido y luego ↻ Actualizar)",
    "dialog.consent.title": "Confirmación de consentimiento de envío a la nube",
    "dialog.consent.heading": "Consentimiento para usar un servicio en la nube",
    "dialog.consent.default_data_summary": "Datos de audio (PCM por elocución) y texto",
    "dialog.consent.body": (
        "backend: {backend}\n"
        "Servicio de destino: {service}\n"
        "Datos enviados: {summary}\n\n"
        "Usar este servicio envía los datos anteriores a un servidor externo. "
        "Revise la política de privacidad / términos de uso del servicio y dé su consentimiento."
    ),
    "dialog.consent.terms": "Términos de uso: {url}",
    "dialog.consent.suppress": "No volver a mostrar este diálogo (suppress_dialogs)",
    "dialog.consent.cancel": "Cancelar",
    "dialog.consent.accept": "Consentir y usar",
    "dialog.credential.title": "Introducir credenciales — {service}",
    "dialog.credential.description": (
        "Introduzca su clave de API, etc. en los campos de abajo y pulse 'Probar'. "
        "Si tiene éxito se guarda y el botón 'Iniciar' queda disponible."
    ),
    "dialog.credential.no_spec": "Este backend no requiere credenciales.",
    "dialog.credential.cancel": "Cancelar",
    "dialog.credential.test": "Probar",
    "dialog.credential.testing": "Probando…",
    "dialog.credential.browse": "Examinar…",
    "dialog.credential.placeholder_file": "(elija con el botón Examinar)",
    "dialog.credential.placeholder_set": "●●●●●●●● (configurado; introduzca solo para cambiar)",
    "dialog.credential.placeholder_unset": "(no configurado)",
    "dialog.credential.file_picker_title": "Seleccione el archivo para {label}",
    "dialog.credential.missing_fields": "Hay campos vacíos: {fields}",
    "dialog.credential.internal_error": "Error interno durante la verificación: {error}",
    "dialog.credential.ok": "✓ Auth OK — {message}",
    "dialog.credential.saved": "Guardado",
    "dialog.credential.failed": "✗ Auth fallida — {message}",
    "dialog.credential.unknown_cause": "Causa desconocida",
    "layer.capture": "Captura de audio",
    "layer.vad": "VAD",
    "layer.asr": "ASR (Transcripción)",
    "layer.translator": "Traducción",
    "layer.tts": "TTS (Síntesis de voz)",
    "layer.output": "Salida de audio",
    "dialog.layer_settings.title": "Configuración de {layer}",
    "dialog.layer_settings.unselected": "(no seleccionado)",
    "dialog.layer_settings.backend_line": "Backend: {backend}",
    "dialog.layer_settings.no_fields": "Esta capa no tiene ajustes editables.",
    "dialog.layer_settings.cancel": "Cancelar",
    "dialog.layer_settings.save": "Guardar",
    "dialog.layer_settings.unsupported_suffix": "(tipo no admitido: {ft})",
    "dialog.layer_settings.no_options": "(sin opciones)",
    "dialog.layer_settings.cred_placeholder_unset": "(no configurado)",
    "dialog.layer_settings.cred_placeholder_set": "●●●●●●●● (configurado; introduzca solo para cambiar)",
    "dialog.layer_settings.auth": "Auth",
    "dialog.layer_settings.auth_verified": "✓ Verificado",
    "dialog.layer_settings.auth_unverified": "No verificado",
    "dialog.layer_settings.auth_open": "Abrir auth / reautenticar",
    "dialog.layer_settings.auth_help": (
        "Introduzca su clave de API, etc. y pase la comprobación de conectividad para guardar con "
        "verified=True. Si ocurre un error durante el funcionamiento por una clave caducada / "
        "suscripción vencida, verified vuelve automáticamente a False."
    ),
    "dialog.layer_settings.auth_saved": "Auth OK se ha guardado.",
    "dialog.layer_settings.input_error": "Error de entrada ({label}): {error}",
    "dialog.layer_settings.cred_save_failed": "Error al guardar las credenciales: {error}",
    "dialog.layer_settings.saved_reload": (
        "Guardado. Pulse el botón central '↻ Cargar' para aplicar la nueva configuración."
    ),
    "dialog.layer_settings.saved_pipeline": (
        "Guardado. Los valores de pipeline se aplican en el próximo '▶ Iniciar'."
    ),
    "dialog.language_select.title": "Seleccionar idioma",
    "dialog.language_select.search_label": "Buscar idioma (código / nombre en inglés):",
    "dialog.language_select.search_placeholder": "p. ej. swahili / swh",
    "dialog.language_select.no_match": "(sin coincidencias)",
    "control_panel.section_action": "Operación",
    "control_panel.section_status": "Estado",
    "control_panel.latency_none": "Latencia media: -",
    "control_panel.latency": "Latencia media: {avg} s (últimas {count})",
    "control_panel.accel_init": "Cómputo: -",
    "control_panel.clear_events": "Borrar acciones",
    "control_panel.recent_translations": "Traducciones recientes:",
    "control_panel.clear": "Borrar",
    "control_panel.test_playback_text": "Audio de prueba",
    "control_panel.playing": "Reproduciendo…",
    "control_panel.starting": "Iniciando…",
    "control_panel.stopping": "Deteniendo…",
    "control_panel.running": "En ejecución",
    "control_panel.stop": "■ Detener",
    "control_panel.load_btn_starting": "(iniciando)",
    "control_panel.load_btn_stopping": "(deteniendo)",
    "control_panel.load_btn_running": "(en ejecución)",
    "control_panel.test_btn_running": "🔊 (en ejecución)",
    "control_panel.idle_start_failed": "Detenido (fallo al iniciar)",
    "control_panel.idle_error": "Detenido (error)",
    "control_panel.load_start_failed": "Error al iniciar la carga: {error}",
    "control_panel.load_failed": "Fallo de carga: {error}",
    "control_panel.event_load_failed": "[Fallo de carga] {error}",
    "control_panel.event_test_done": "[Prueba de salida] reproducción completada: {text}",
    "control_panel.test_failed": "Fallo de prueba de salida: {error}",
    "control_panel.event_test_failed": "[Fallo de prueba de salida] {error}",
    "control_panel.start_failed": "Fallo al iniciar: {error}",
    "control_panel.event_start_failed": "[Fallo al iniciar] {error}",
    "control_panel.event_stop_exception": "[Excepción al detener] {error}",
    "control_panel.event_fatal": "[Error fatal] {error}",
    "control_panel.suppressed_suffix": " (+{count} suprimidas)",
    "control_panel.status_fetch_failed": "(error al obtener el estado: {error})",
    "settings_panel.section_backends": "Backends",
    "settings_panel.section_devices": "Dispositivos",
    "settings_panel.section_languages": "Traducción",
    "settings_panel.config_btn": "Configuración",
    "settings_panel.input_device": "Dispositivo de entrada:",
    "settings_panel.output_device": "Dispositivo de salida:",
    "settings_panel.enumerating": "(enumerando)",
    "settings_panel.process_select": "Seleccionar proceso…",
    "settings_panel.pid_selected": "PID {pid} ▼",
    "settings_panel.src_lang": "Idioma de entrada (src):",
    "settings_panel.tgt_lang": "Idioma de salida (tgt):",
    "settings_panel.log_dir": "Directorio de salida de registros:",
    "settings_panel.save": "Guardar configuración",
    "settings_panel.reload": "Recargar configuración",
    "settings_panel.redetect_devices": "Volver a detectar dispositivos",
    "settings_panel.unselected": "(no seleccionado)",
    "settings_panel.fetch_failed": "(error al obtener: {error})",
    "settings_panel.no_input_device": "(sin dispositivo de entrada)",
    "settings_panel.no_output_device": "(sin dispositivo de salida)",
    "settings_panel.process_dialog_failed": "Error al abrir el diálogo de selección de proceso: {error}",
    "settings_panel.save_failed": "Fallo al guardar: {error}",
    "settings_panel.saved": "Configuración guardada",
    "settings_panel.reload_blocked": "No se puede recargar la configuración mientras está en ejecución (deténgala primero)",
    "settings_panel.reload_failed": "Fallo al recargar: {error}",
    "settings_panel.reloaded": "Configuración recargada",
    "main_window.locale_running_blocked": "No se puede cambiar el idioma de visualización mientras está en ejecución (deténgala primero)",
}

_CATALOGS: dict[str, dict[str, str]] = {
    "ja": _JA,
    "en": _EN,
    "zh": _ZH,
    "es": _ES,
}

_DEFAULT_LOCALE = "ja"

# 各ロケールの表示名は「その言語自身」で表記する(切替しても変わらないため tr 対象外)。
_LOCALE_DISPLAY_NAMES: dict[str, str] = {
    "ja": "日本語",
    "en": "English",
    "zh": "中文",
    "es": "Español",
}

# 現在の UI ロケール(可変)。`set_locale` で切り替え、`current_locale` で参照する。
_current_locale = _DEFAULT_LOCALE


def current_locale() -> str:
    """現在の UI ロケールを返す(ロケール解決の単一窓口)。

    文言は表示時に `tr()` で引く規約(モジュールレベルで焼かない)なので、`set_locale` で
    切り替えた直後に各 widget を再構築すれば新ロケールで表示される。
    """
    return _current_locale


def set_locale(locale: str) -> None:
    """UI ロケールを切り替える。未対応ロケールは `KeyError`。

    切替の画面反映(widget 再構築)は呼び出し側(MainWindow)の責務。
    """
    global _current_locale
    if locale not in _CATALOGS:
        raise KeyError(f"未対応のロケール: {locale!r}")
    _current_locale = locale


def available_locales() -> list[str]:
    """カタログを持つロケールのコード一覧(切替 UI の選択肢)。"""
    return list(_CATALOGS.keys())


def locale_display_name(locale: str) -> str:
    """ロケールコードの表示名(その言語自身の表記)。未知はコードをそのまま返す。"""
    return _LOCALE_DISPLAY_NAMES.get(locale, locale)


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
