# 保留・暫定決定リスト (pendList)

各項目は `[起票日] 内容 / 背景 / 対応の見送り理由` を含める。

---

## [📌方針 2026-05-30] 追加モデル続行・UI 調整・配布形態の段階対応(複数日)
本ブランチ(`feature/vad-picks-pyannote-4x`)を master にマージしたあと、下記 3 つを
別ブランチで順次対応する(各 1 日では収まらない見込み)。

### a) backendCandidates の残り picks 実装
- `docs/design/feature-backend-mgmt/backendCandidates.html` で ✓ を付けた
  ASR 3 件 / Translator 3 件をまだ実装していない:
  - ASR: OpenAI Whisper API / Deepgram / Google Cloud STT
  - Translator: DeepL API / OpenAI GPT-4o-mini / Anthropic Claude Haiku
- token が用意できた backend は `tests/test_<backend>_large.py` を必ず追加(新方針)。
- 進めるペースはユーザ要望ベース。Phase F2 として 1〜2 件ずつ別ブランチで。

### b) UI 調整
- 既に保留されている UI 折り畳み(設定セクション / ステータス)
- start 失敗時の表示動線(`ffa68e5` で status_label に出すようにしたが、もう一段
  目立つ通知バナー等の検討余地)
- 詳細ダイアログから抜けたときの再描画タイミング
- 認証ダイアログ閉じた直後の状態反映の挙動

### c) 配布形態(実行環境のポーティング / インストーラ)
- 現状は `git clone + uv sync --extra cpu` または `--extra cuda --extra vad-extra` 前提。
  非開発者には敷居が高い。
- 検討案:
  - **PyInstaller / Nuitka で one-folder** にして zip 配布
  - **uv tool** での配布(まだ実験的)
  - **Windows MSI**(WiX 等)
- 配布方針「CPU を floor、GPU は opt-in」と整合する形を選ぶ。
- 副題: モデル DL は配布物に含めず、初回起動時 DL とする(配布物サイズ削減)。

### d) ライセンス規約のあるモデルを README に明記
- pyannote.audio(`pyannote/segmentation-3.0`)— 利用同意必須(gated)
- Picovoice Cobra — 個人非商用無料 tier / 商用は要ライセンス
- 各 cloud backend(OpenAI / DeepL / Anthropic / GCP / AWS)の API 利用規約
- README に「対応 backend と必要な利用同意 / アカウント」のセクションを追加。
- 配布時に同意忘れで動かないケースが多いので、起動時のテストや warning も併設検討。

---

## [⏳保留 2026-05-30] 追加 VAD backend の依存 optional 化方針
- **対象**: `pyproject.toml` の `[project.optional-dependencies].vad-extra` に入れた
  `webrtcvad-wheels` / `pyannote.audio` / `pvcobra`。`uv sync --extra vad-extra` で初めて入る。
- **背景**: Phase F1 で 3 つ VAD backend を追加した際、配布方針「CPU を floor / 誰でも持っていける」
  を維持するため、利用者が opt-in しない限り pyannote.audio (transformers/lightning/scipy/sklearn 等
  を引っ張る) を入れないことにした。代わりに、未インストール環境では各 backend が
  `FatalError("`uv sync --extra vad-extra` で追加してください")` を投げる前提。
- **見送り判断ポイント**:
  - webrtcvad だけは小さい(< 100KB)ので**必須側に上げる**選択もある(Silero フォールバック
    としての位置付け)。pyannote / pvcobra と束ねた現状は揃ってる代わりに、ユーザが「軽い
    フォールバックだけ欲しい」場面で過剰になる。
  - もしフォールバックを自動でやるなら必須側、選択肢として並べるだけならいまの opt-in で十分。
- **着手トリガ**: dogfooding で「silero が動かない環境」を実際に踏んだ、または既定 VAD を
  入れ替えたくなった時。

---

## [⏳保留 2026-05-30] フレーム判定系 VAD の共通ロジック抽出
- **対象**: `WebRtcVadBackend` と `PvcobraVadBackend` の `_handle_frame` / `_enter_speech` /
  `_exit_speech` / `_maybe_force_emit` がほぼ同形。
- **背景**: 両 backend とも「フレーム → speech/silence(or 確率)」を返す型で、ヒステリシス
  (連続 N で start / 連続 M で end)+ pad + max_speech_sec は同じ。
- **対応の見送り理由**: 今回は責務分離を優先。共通化を急ぐと N/M の調整パラメータが
  backend ごとに違ってくる懸念があり、3 backend 揃ったあとで再検討する方が安全。
- **着手案**: `FrameThresholdSegmenter(min_speech_frames, min_silence_frames, pad_frames,
  max_speech_samples)` を作って `feed(frame, is_speech) -> list[VadSegment]` を切り出す。
  Silero は frame 粒度ではないので対象外。

---

## [⏳保留 2026-05-30] pyannote.audio の verify_credentials を実モデル不要にする
- **対象**: `PyannoteVadBackend.verify_credentials` は HuggingFace の `/api/whoami-v2` 叩きで
  token の生死だけ確認。実モデルへのアクセス権(`pyannote/voice-activity-detection` の
  利用同意済みか)はチェックしていない。
- **背景**: 完全な verify はモデルを実際にダウンロード&ロードする必要があり、verify が分単位
  かかってしまう。短時間で済ませる方を優先した。
- **未確認のリスク**: token は生きてるがモデル利用同意未済 → `Pipeline.from_pretrained` 時に
  401/403 で落ちる。`backend.__init__` で `FatalError` を投げる扱いになっている。
- **対応案(着手時)**: `https://huggingface.co/api/models/<model_id>` を GET して
  `gated` / `disabled` / 同意状態を見る軽い check に置き換える。

---

## [⏳保留 2026-05-29] cloud backend 認証テスト(skip スケルトンの埋め込み)
- **対象**: `tests/test_credential_flow.py` Part 2(6 クラス計 28 件、すべて `@pytest.mark.skip`)
- **背景**: Phase E-2 で認証フローを汎用化(spec → verify → 保存 → Start gate)した際、
  各 cloud backend が満たすべき契約をテスト雛形として先置きしてある。実 backend がまだ
  存在しないため、現状は `skip` で見える化だけしている状態。
- **対応の見送り理由**: Phase F で「どの cloud backend を 1 つ目に実装するか」が決まるまで、
  テストを書いても回せない(実 API key も準備が要る)。実装と同時に埋めるのが効率的。
- **着手時にやること**:
  1. 対応する backend クラスを `src/voice_translator/{layer}/{name}_backend.py` に実装
     (`credential_spec()` + `verify_credentials()` を含む)
  2. `backend_setup.py` で `backend_cls` + `capabilities` 付きで register
  3. テストクラスの `@pytest.mark.skip` を外す
  4. 各 test メソッドの中身を埋める:
     - 有効/無効/network error/quota 超過 を httpx モック等で再現
     - 動作中 401 → `invalidate_verification` 連携の確認
  5. 実 API key を使う large テストは別途 `@pytest.mark.large` で追加(任意)
- **対象 backend**: OpenAI Whisper API / DeepL / OpenAI TTS / Anthropic Claude /
  AWS Transcribe / Google Cloud STT(GCP は `file_picker` 型の schema 追加も必要)

---

## [⏳保留 2026-05-29] UI セクションの折り畳み(設定全体 / ステータス)
- **要望**: MainWindow 上の SettingsPanel(バックエンド・デバイス・翻訳言語)と
  ControlPanel のステータステキストボックスを、それぞれ畳めるようにしたい。
  使い込んだあとは履歴を広く見たいシーンが多いので、必要なときだけ広げる UX にしたい。
- **対応の見送り理由**: 本ブランチ(feature/backend-mgmt)は backend 管理が主題。
  customtkinter には標準で collapsible 機能が無く、自作する必要があるため、別ブランチで
  まとめて対応する(`docs/` の pendList で来歴を残す)。
- **想定実装**: 各セクションの見出しを「▼ 設定」「▶ 設定」のように切替可能なボタンにし、
  クリックで子ウィジェットを `.pack_forget()` / `.pack()` で出し入れする。配置の jitter を
  避けるため `CTkFrame` をラップして `.grid_remove()` する案も検討。

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

## [✅完了2026-05-27] SAPI(pyttsx3)で音節が異常に繰り返される既知バグ + 暫定対処
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
- 原因と対応
　- 翻訳処理に使っているモジュールが同じ単語が多い場合、それに関連を見出してループするような挙動をとっていた。
  - パラメータを調整して対応した。
---

## [2026-05-26 / 改 2026-05-29] 入力処理レイヤーの改善案
- **背景**: 現状 `soundcard` ライブラリで **post-mix loopback のみ**(再生デバイス単位の最終出力をキャプチャ)。下記の要件は MVP では満たせないが、いずれ要望が出ると想定される:
  - **per-app / per-process キャプチャ**(対象アプリだけ取りたい、他は無音)
  - 複数ソースの mix / 仮想ルーティング / 録画混在
  - 音量 0 でも対象アプリ音声を取得(post-mix 経路だと振幅 0 になり取れない)
  - クロス OS で同じユーザ体験(Windows/macOS/Linux)
- **検討案**:
  - **A. GStreamer 移行**(旧来案): クロス OS 抽象化と per-process など要件を一括カバー。
    - 短所: GStreamer ランタイム本体の OS 側インストールが必要、配布が重い、学習コストが高い。
  - **B. 入力処理レイヤーを別 module 化**(2026-05-29 追加 / **本命候補**): Python 本体とは別 module(C++/Rust 等、言語問わず)として、OS ネイティブ API を直接叩く実装を切り出す。Python とは ctypes / pybind11 / サイドカープロセス(stdio/pipe/gRPC)で疎結合に。
    - 各 OS のネイティブ API: Windows = WASAPI Process Loopback(Win10 2004+) / macOS = ScreenCaptureKit + 14.4+ の per-process audio filter / Linux = PulseAudio sink-input or PipeWire グラフ接続。
    - メリット: Python 側に重い依存を増やさず最新 API を全て使える / バイナリ単体配布が軽い / C++ なら OBS の実装(オープンソース)を参考にできる。
    - デメリット: ビルドが OS ごと(CI マトリクス必要) / 開発者の学習コストは GStreamer よりさらに高い場合あり / 公開時にユーザのアンチウィルスに引っかかるリスク(ネイティブバイナリ配布)。
- **共通の見送り理由**: 現時点では MVP の要件(post-mix loopback で「PC の再生音を字幕翻訳」)は満たせている。per-app 取得や複雑ルーティングは「ユーザが VB-Cable / BlackHole / PipeWire 等の仮想ケーブルで迂回する」運用で当面しのげる(各 OS ごとの推奨ツールを `docs/manual.md` に書く方が先)。
- **再検討トリガ**:
  - per-app 取得が必須化(「対象アプリだけ取りたい / 他は無音」要望の頻発)
  - 音量 0 で取得したい要件(配信視聴中に物理スピーカは消したい等)が頻発
  - 複雑なルーティング要件(複数ソース mix、録画混在)
  - GitHub 公開後にクロス OS で安定動作の課題が見える
- **設計上の含意**: いずれの案でも `AudioCaptureBackend` インタフェース(Class.md §1)の存在で差し替えコストは抑えられる。本変更は backend 実装の置き換え/追加であり、パイプライン本体の責務分担は維持される。
- **関連**: 「音声入力状態の可視化UI」(入力レベルが 0 の時の WARN 表示は本件の前提として有用)、「パイプラインステージのパラメータ GUI 化」(入力ソース選択 UI の拡張)。

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

## [2026-05-29] AppController の責務分離(リファクタ予約)
- **背景**: `feature/backend-mgmt` の Phase A2 で AppController に layer 単位 load / multi-listener / 処理時間 buffer 等の orchestration 責務が追加される。R2-1 解消の分散化で `_model_status` dict は消えるものの、全体としては引き続き肥大化傾向
- **方針案**(将来のリファクタ時):
  - `ModelLoaderService`(ロード処理)
  - `StatusBroadcaster`(listener 管理 + re-broadcast)
  - `LatencyBuffer`(layer 別 処理時間)
  - AppController は composition でこれらを保有する形に整理
- **見送り理由**: 動かしてから整理する方が安全(早すぎる抽象化を避ける)
- **再検討トリガ**: `feature/backend-mgmt` ブランチ完了後 / さらに新機能で AppController が肥大化したタイミング

---

## [2026-05-29] リトライ機構の効果検証 — 効果薄なら撤回も検討
- **背景**: `feature/backend-mgmt` の Phase E でクラウド backend(ネットワーク経由テキスト系 API)に 3 回リトライ機構を実装予定。だが「リトライ中も上流の capture が動き続けてキューが詰まる」という構造的な懸念がある(knownRisks R-4)
- **検証案**: Phase F で DeepL 等の実クラウド backend を 1 つ繋いだ時、わざとネット切断 / レート制限を再現してリトライ機構の挙動を観察
- **判定基準**:
  - リトライ中もキュー詰まりが許容範囲内 → 採用継続
  - キュー詰まり / drop が頻発して体感が悪い → **リトライ機構ごと撤回**(失敗即停止に倒す)
- **撤回した場合の影響**: backend の severity 設計はそのまま使えるので、ErrorHandler 側で「RECOVERABLE → リトライ」を「RECOVERABLE → SKIP / FATAL に格上げ」に変えるだけで吸収可能
- **見送り理由**: 設計段階で結論を出せない。実機の挙動を見ないと判断不能
- **再検討トリガ**: Phase F の動作確認時(feature/backend-mgmt の最終フェーズ)

---

## [2026-05-28] Whisper モデルサイズ(`model_size`)の引き上げ検討 — small → medium 以上
- **現状**: `FasterWhisperAsrBackend(model_size="small")` が既定。`small` は faster-whisper(Whisper)のモデルサイズ系列(`tiny / base / small / medium / large-v2 / large-v3`)の小さい方から3番目で、概ね VRAM ~500MB / 認識精度は実用ライン。`tests/` でも `"small"` 前提でモック値が組まれている。
- **動機**: `refactor/asr-gpu-compute-type`(2026-05-28 マージ済)後の GPU プロファイルで `asr_proc_ms` が平均 754ms まで下がり、8秒発話に対し十分な余裕が出た。total レイテンシも 13.8s と発話長(7.5s)に近づき、パイプライン的にもキューが詰まらない。**この余裕を品質向上に振り向けられる**(特に英語以外、固有名詞、専門用語、雑音下)。
- **副作用と制約**:
  - メモリ/VRAM: medium ~1.5GB、large-v3 ~3GB+。**CPU floor 制約**(配布方針)があるため、CPU 走行時の所要時間とメモリも実測してから既定値を動かす。
  - 起動時間: 初回モデルダウンロードがサイズに比例して長くなる。
  - **device に応じてサイズを自動切替するのは避ける**(コードパス1本の方針に反する)。サイズは `config.yaml` 経由のユーザ選択または起動時環境検出による初期推奨値の提示 に留める。
- **対応方針(案)**:
  1. middle テスト相当で medium / large-v3 の `asr_proc_ms` と認識品質を CPU/GPU 双方で実測(計測手順は今回の `logs/cpu` `logs/gpu` `logs/gpu_v2` 方式を踏襲)。
  2. 結果次第で `config_store` の既定値を変更、または "preset"(fast/balanced/accurate)としてまとめる(下の「パイプラインステージのパラメータ GUI 化」エントリと統合)。
  3. `tests/test_faster_whisper.py` の `"small"` 直書きを定数化しておくと差し替えが楽。
- **再検討トリガ**: 認識ミス頻発のフィードバック / 多言語サポート強化 / 大型GPU想定ユーザ向けプリセット追加の検討。
- **関連項目**: 「パイプラインステージのパラメータ GUI 化」(プリセット方式 vs バックエンド固有展開の設計検討は共通)、「プロジェクト配布方針」(CPU floor との両立)。

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