# 保留・暫定決定リスト (pendList)

各項目は `[起票日] 内容 / 背景 / 対応の見送り理由` を含める。

---

## [📌方針 2026-05-28] プロジェクト配布方針 — GPU はオプション扱い、CPU を floor とする
- **背景・目的**: 将来 GitHub で公開して**誰でも自由に持っていける**形にしたい。そのためには「特定のハードがないと動かない」状態は避けたい。
- **方針**:
  1. **CPU で動くことを必須**(floor)。NVIDIA GPU 無しでも起動・縦通しが可能であること。
  2. **GPU/アクセラレータがあれば自動で使う**(bonus)。NVIDIA(CUDA)/Apple Silicon(MPS)/将来的に他も自動検出。
  3. **テスト工数を増やさない**: コードパスは1本(`device` を引数で渡すだけ)。「GPU 用テスト」と「CPU 用テスト」を二重に書かない。
- **テスト戦略**:
  - small テストは **CPU のみ・モデル本体ロードなし**(モック)。これは GPU の有無に関係なく成立する。
  - middle/large テストは実モデル使用。**GPU があれば GPU、無ければ CPU** で同じテストが走るようにする(`device="auto"` を内部で解決)。
  - 「GPU でしか起きないバグ」がもし発覚した場合のみ `@pytest.mark.gpu` を追加(現時点では作らない)。
- **設計上の含意**:
  - 新規バックエンドや既存バックエンドの GPU 対応は `device` 引数(または `backends_config.<backend>.device`)で受ける形に統一。`"auto" | "cuda" | "mps" | "cpu"` を許容。
  - 「GPU 専用バックエンド」と「CPU 専用バックエンド」を別クラスとして並列で持つのは避ける(テストが2倍になる)。
- **判断のラインを明文化**:
  - 公開対象ユーザの中央値想定は「**GPU 無しのデスクトップ/ノート PC**」。
  - 「GPU 必須」を前提にしたい機能(例: 大型 LLM 翻訳)は **オプトイン**(`backends` に追加するだけで既定は変わらない)。
- **再検討トリガ**: GitHub 公開後にユーザフィードバックで「重すぎて使い物にならない」が多発した時。その時点で再度設計を見直す(CTranslate2 等の軽量化、または GPU 推奨のラインを引き上げる検討)。

---

## [✅完了 2026-05-27] PipelineCoordinator のキュー変数名を意味のある名前にリネーム
- **対応ブランチ**: `refactor/queue-rename`
- **対応内容**: 過去分詞ベースの命名で統一(命名規則の対称性 + 視認性):
  - `q_raw` → `captured_queue`(Input → ASR)
  - `q_tr` → `recognized_queue`(ASR → Translator。`translated_queue` との見間違い回避のため `transcribed_queue` ではなく `recognized` を採用)
  - `q_xl` → `translated_queue`(Translator → TTS)
  - `q_syn` → `synthesized_queue`(TTS → Output)
  - サイズ引数も `captured_queue_size` 等に連動。pipeline.py / tests / Architecture.html / Class.md / Plan.md を同時更新。
- **元の背景**: 5スレッド版への書き換え時(`refactor/pipeline-5thread`)に、ループ内で頻出するため短縮形を採用した。Python では PEP 8 的に descriptive な名前が推奨される一方、ローカル変数で頻出する場合は短縮形も許容範囲。C#/C++ 出身のレビュアーから「もう少し意味を持たせたい」との指摘で本作業に至る。

---

## [2026-05-27] SAPI(pyttsx3)で音節が異常に繰り返される既知バグ + 暫定対処
- **問題の概要**: 低頻度で、TTS再生時に特定の音節(例: 「問題」が「問問問問問...問題」のように)1分近く繰り返されて再生されるケースが発生する。テキスト本来の長さを大幅に超える音声が出る。
- **想定原因(未確定)**:
  - 第1候補: pyttsx3 / SAPI 内部の音素生成バグ(GitHub Issues に類似報告あり)
  - 第2候補: `runAndWait()` 完了直後に WAV ファイルがまだ完全に flush されておらず、壊れたWAVを読んでいる
- **暫定対処(本コミットで実装)**: `SapiTtsBackend` の `runAndWait()` 直後に **短時間 sleep**(`flush_delay_sec`、既定 0.1 秒)を挿入。flush 不整合が原因なら緩和される。
  - 構造: `SapiTtsBackend(flush_delay_sec=...)` で調整可能、`0` で無効化可。
  - 副作用: 全 TTS で +0.1 秒のレイテンシ増加(許容範囲)。
- **暫定処理である理由**:
  - 根本原因が SAPI 内部か flush 不整合か未特定
  - 第1候補(SAPI バグ)なら sleep だけでは完全には防げない
  - 真の解決は TTS バックエンドの差し替え(VOICEVOX / edge-tts 等、Phase 2 の F-1 参照)
- **将来の削除条件**: TTS バックエンドを SAPI 以外に切り替えた時点で `flush_delay_sec` パラメータごと削除する。
  - 切替時に grep 対象: `flush_delay_sec` / 本 pendList エントリの ID。
- **追加調査の入口**:
  - 異常検知(生成WAV長 ÷ 文字数 の閾値判定)を入れれば暫定 SKIP できる(別途検討)。
  - 発生時の文字列パターンをログから収集すれば再現条件が見える可能性。

---

## [2026-05-26] 音声取得ライブラリの GStreamer 移行検討
- **内容**: MVPでは `soundcard` を採用するが、将来 GStreamer への移行を検討する。
- **背景**: GStreamer はメンテ・品質ともに問題なし。クロスOS抽象化やプロセス単位取得・複雑なルーティング・録画混在などの要件にも応えられる。
- **対応の見送り理由**: 現時点では MVP に対して機能リッチすぎる。ランタイム本体のOS側インストールが必要で配布も重い。学習コストも高い。
- **再検討トリガ**: per-app取得が必須化した時 / 複雑なルーティング要件が出た時 / 録画機能を追加する時。
- **備考**: `AudioCaptureBackend` インタフェースを切っておけば差し替えコストは抑えられる。

---

## [2026-05-26] 音声入力状態の可視化UI(サブダイアログ)
- **内容**: 入力デバイスからの音量レベルを時系列グラフで可視化するサブダイアログを追加する(メモリ使用状況のような帯グラフのイメージ)。
- **背景**: 「入力デバイスは選んだが本当に音が入っているか分からない」状況が発生しうる。フィードバックや無音の切り分けを目視できると初期セットアップ時のトラブルシュートが楽になる。
- **対応の見送り理由**: MVPの達成ライン(英語YouTubeを日本語音声で聞ける)には必須ではないため。実装には音量メータ(peak/RMS)算出、定期 UI 更新スレッド、サブダイアログの作成が必要で、それなりの工数になる。
- **再検討トリガ**: ユーザがセットアップで詰まる頻度が増えた時 / マルチデバイス環境でのデバッグ要望が出た時。
- **備考**: ピーク/RMS は `np.abs(pcm).max()` / `np.sqrt((pcm**2).mean())` 程度で安価に計算可能。Input スレッドからキャプチャ済みチャンクをタップして UI に渡す形にすれば既存設計と整合する。

---

## [✅完了 2026-05-26] 翻訳前後テキストの個別ログ出力(デバッグ用)
- **対応コミット**: `7e78950` / マージ `de9b9d1`(`feature/individual-text-logs`)
- **対応内容**: `TextLogger` クラス追加、ファイル名は `soundsrc.txt` / `translated.txt`、ConfigStore に `log.src_text_enabled` / `log.tgt_text_enabled` 追加(既定 OFF)、書式 `[YYYY-MM-DD HH:MM:SS] [lang] text`、`config.yaml` で個別 ON/OFF。
- **未対応 / 将来課題**: GUI からの ON/OFF 切替は未実装(`config.yaml` 直接編集)。Phase 2 で SettingsPanel に追加検討。

---

## [✅完了 2026-05-27] パイプラインの完全並列化(案C)
- **対応ブランチ**: `refactor/pipeline-5thread`
- **対応内容**: `PipelineCoordinator` を 5 スレッド(Input / ASR / Translator / TTS / Output)・4 キュー(captured_queue=5 / recognized_queue=10 / translated_queue=10 / synthesized_queue=5)構成に書き換え。各段は `PipelineMessage`(seq_id + 最小ペイロード)を流し、横断メタは `UtteranceLedger` に集約。あわせて shortcutList A-1(Utterance 構造の本格分割)も同時に解消。
- **元の内容**: 現状の B+案(Input/Process/Output の3スレッド) をさらに分割し、ASR / 翻訳 / TTS を**個別スレッド**にする(計5パイプラインスレッド)。各段がキュー越しに非同期で動く。
- **背景**: 連続発話(映画/ポッドキャスト等で沈黙が少ない素材)で Process 段が詰まり、`_put_with_drop` で発話が捨てられるケースに有効。スループット2〜3倍が見込める。
- **当時の見送り理由(参考)**: 単一発話のレイテンシは変わらない。会話用途や通常視聴では B+ で十分。スレッド/キュー数増による停止シーケンス・エラー伝播・テストの複雑化コストが先に立つ。
- **未対応 / 将来課題**: 実機での縦通し確認(testPlan の「実機(全Phase完了後)」セクション)は別途実施。

---

## [2026-05-26] パイプラインステージのパラメータ GUI 化
- **内容**: VAD の `min_silence_ms` / `max_speech_sec` / `threshold`、Whisper の `beam_size` 等、現状ハードコード/コンストラクタ既定値のパラメータをユーザが GUI から調整できるようにする。
- **背景**: 値を変えるたびにコード書き換え+再起動が必要で試行錯誤しづらい。
- **対応の見送り理由**: バックエンドを切替えるとパラメータの**有無/意味/値域**がバックエンドごとに違うため、素朴に GUI 化すると「切替えたら値が消える/効かない/意味が変わる」事故が起きる。設計検討が必要(プリセット方式 vs バックエンド固有パラメータ動的展開 vs ハイブリッド)。
- **再検討トリガ**: バックエンドを実際に複数登録するフェーズ(Phase 2)に入った時。
- **備考**: 当面は `ConfigStore` 経由で `config.yaml` を手編集で対応する。推奨設計はプリセット(quality: fast/balanced/accurate)+ Advanced 展開のハイブリッド。
- **[追記 2026-05-28] VAD パラメータ群**: ボトルネック調査(`feature/vad-max-utterance-length`)で `min_silence_ms` / `max_speech_sec` を `config.yaml` から触れるようにした(`backends_config.silero.{min_silence_ms, max_speech_sec, threshold, speech_pad_ms}`)。**レイヤ別「設定」ダイアログから編集可** にする UI 連携はここに統合する(`layer_settings_schema` の VAD レイヤに `applies_when_backend="silero"` で追加)。本作業はサイドチャットで保留。

---

## [2026-05-26] 出力デバイスの音量調整UI
- **内容**: 翻訳音声(TTS出力)の音量をアプリ内から調整できるようにする。最終的には別ダイアログにまとめる想定だが、**一旦は ControlPanel 内のスライダ**でもよい。
- **背景**: OS のミキサーで個別アプリの音量を絞るのは手間。アプリ内で完結すると配信視聴中の調整が楽。TTS が大きすぎる/小さすぎるケースで頻繁に発生する。
- **対応の見送り理由**: MVPの達成ライン(縦通し)には不要。実装方針(ソフトゲイン vs OSレベル)も決めきれていない。
- **再検討トリガ**: 実機運用で「音量が合わない」フィードバックが続いた時 / Phase 2 で UI を整理するタイミング。
- **備考**: 実装案は2つ。
  - **A. ソフトゲイン(推奨/簡単)**: `SoundcardOutputBackend.play()` の直前で `pcm * gain` を掛ける。スライダ値 0.0〜1.5 程度。クリップ防止のためゲイン>1.0 時は `np.clip(-1.0, 1.0)`。
  - **B. OS音量制御**: Windows なら `pycaw` で出力デバイス本体の音量を変える。他アプリも影響を受けるので慎重に。
  - 設定永続化キー: `audio.output_gain`(0.0〜1.5 の float)。

---

## [2026-05-28] 翻訳/LLM バックエンドの生成パラメータを設定可能にする
- **問題の具体(発生事例)**: `translations.jsonl` L184 で、920文字の英文(イラン国内インターネット復旧の話題、`internet` / `online` / `restrictions` / `businesses` が密集して反復)を入力した際、`tgt_text` が「ウェブのインターネットは,インターネットの普及を 妨げている.」を約 28 回繰り返すなど、明確な **degenerate output**(同じ n-gram に吸着して max_length まで延々と回る現象)が発生した。L180 や L183 は同等の長文だが語彙が散らばっており崩れていない。
- **直接の要因**: `Nllb200TranslatorBackend.translate()` の `generate()` 呼び出しが `forced_bos_token_id` と `max_length` しか渡しておらず、`num_beams=1`(greedy)/ `no_repeat_ngram_size=0` / `repetition_penalty=1.0` という HF デフォルトのまま。NLLB-200 distilled 600M は容量が小さく、greedy 探索が局所最適に落ちると抜け出せない。修正は `num_beams=4` / `no_repeat_ngram_size=3` / `repetition_penalty=1.1` / `early_stopping=True` を渡すだけで効くことが確認できる。
- **抽象化した課題**: **翻訳バックエンド(将来 LLM ベースも含む)の "生成パラメータ" は、バックエンドごとに名前/意味/値域が異なる**。NLLB の `num_beams`、Marian の `length_penalty`、Ollama の `temperature` / `top_p`、OpenAI 互換 API の `max_tokens` / `frequency_penalty` … 共通化できない。一律な GUI 化は事故のもと(切替えたら効かない・値が無意味になる)。
- **対応方針(案)**: 既存パターン(`backends_config.sapi.rate` / `SapiTtsBackend.__init__(rate=...)`)に倣って、**バックエンド固有の名前空間** で持つ。
  - `config.yaml` 例: `backends_config.nllb200.{num_beams, no_repeat_ngram_size, repetition_penalty, early_stopping}`
  - `Nllb200TranslatorBackend.__init__` で受けて `translate()` 内の `generate()` に渡す。
  - `layer_settings_schema` の Translator レイヤに足せばレイヤ別「設定」ダイアログから編集可能になる(`applies_when_backend="nllb200"`)。
- **対応の見送り理由(現時点)**: ここはサイドチャットからの派生。本筋の作業を進めつつ、別ブランチで対応する。最低限の "degenerate 防止" は **コード内のデフォルト引数を変える**(`num_beams=4, no_repeat_ngram_size=3, ...`)だけでも回避可能(設定可能化は別フェーズ)。
- **再検討トリガ**: 実機で再度 degenerate が観測された時 / 第二の翻訳バックエンド(Ollama / M2M100 / API ベース等)を追加するタイミング(設定スキーマの汎用化を一緒に検討)。
- **関連項目**: 下の「パイプラインステージのパラメータ GUI 化」とも重なる(VAD/ASR/翻訳/TTS いずれも同種の課題)。プリセット方式 vs バックエンド固有展開の設計検討は共通。

---