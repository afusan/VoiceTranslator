# 保留・暫定決定リスト (pendList)

各項目は `[起票日] 内容 / 背景 / 対応の見送り理由` を含める。

---

## [2026-06-16] ASR 対応・翻訳非対応の入力言語が「英語誤認」で訳される(src↔翻訳のカバレッジ不一致)

- **内容**: 入力言語(src)に「ASR(Whisper)は対応するが Translator(NLLB)が対応しない言語」を
  選ぶ / `auto` でそれが検出されると、NLLB のソース言語が `eng_Latn` にフォールバックし、
  **原文を英語と誤認した翻訳**になる(エラーも警告も出ない)。出力(tgt)側は「翻訳∩TTS」で
  絞っているが、**src 側は翻訳側で絞っていない**のが根本。
- **メカニズム(再開時に読む)**:
  - src ドロップダウンは ASR の `supported_input_languages()`(= Whisper 99 言語)で構築。
    翻訳側で絞り込まない(`gui/settings_panel.py` の `_refresh_input_language_choices`)。
  - 翻訳時 `nllb200_backend._to_nllb_code(src, fallback="eng_Latn")` が、対応表
    (`ISO_TO_NLLB` 639-1 / `CANONICAL_TO_NLLB` 639-3)に無いコードを **`eng_Latn`** に倒す。
  - src 言語の流れは `common/pipeline.py`(L557-562, L580-581): `src=auto` のときは
    **ASR 検出言語**が、明示時はその指定が translate に渡る。よって auto/明示どちらでも発生する。
- **該当言語(2026-06-16 時点、実コードで算出 = Whisper正準 − NLLB申告)13 件**:
  `afr, bre, haw, lat, lin, ltz, mlg, mri, oci, san, snd, tuk, yid`。
  - うち **`afr`(アフリカーンス)/ `lin`(リンガラ)/ `mlg`(マダガスカル)/ `snd`(シンド)** は
    **NLLB-200 本体は対応**(FLORES に `afr_Latn` 等あり)。当アプリの申告表に未登録なだけなので、
    表追加で正しく翻訳可能になる。残りは NLLB 自体が非対応寄り。
- **対応案(2 系統。組合せ可)**:
  - (a) **申告表の拡充**: NLLB が実対応する言語(まず上記 4 件)を `CANONICAL_TO_NLLB` に
    FLORES コードで追加。`docs/design/done/feature-mms-multilingual/gen_lang_table.py` と同様に
    NLLB tokenizer の `_extra_special_tokens` から FLORES コードを確認して足す(推測しない)。
  - (b) **入力側の警告/絞り込み**: 出力側の TTS 非対応警告(`gui/logic/language_choices.py:
    tts_warning_needed` / `format_tts_warning_message`)と対称に、「現 Translator が src を
    訳せないとき警告」または「src 候補を翻訳可能言語に絞る」を入れる。判断は logic、表示は View。
- **対応の見送り理由**:
  - **639-3 移行による回帰ではない**(移行前も `ISO_TO_NLLB` にこれら 639-1 が無く、同じく
    英語フォールバックしていた)。`feature/mms-multilingual` は出力側(MMS-TTS)の多言語化が主題で、
    本件は**入力側の ASR↔翻訳カバレッジ**という別テーマ。スコープを切るためブランチ外とした。
  - ユーザ判断(2026-06-16)で pendList 起票。
- **再検討トリガ**: 低資源言語の**入力**を実運用し始めたとき / 翻訳の品質崩れがドッグフーディングで
  顕在化したとき / 出力側に倣って「対応言語の警告」を体系的に入れる UI 改善に着手するとき。

---

## [2026-06-10] 「設定を再読込」のスマート化(差分 evict + セッション内 PID 維持)
- **内容**: `AppController.load_settings()` は現在、ファイル側で backend が変わっている可能性に
  備えて**全 backend キャッシュを無条件 evict** する(全レイヤ INIT に戻る)。これを
  「再読込前後で `backends.<layer>` と選択 backend の `backends_config.<name>.*` が変わらない
  レイヤは evict しない」差分方式にする。あわせて、capture backend が変わらない場合は
  セッション内の PID 選択(`devices.input`)も維持できると望ましい(A-7 の揮発正規化は
  ファイル保存・起動時のみに限定する形)。
- **背景**: ドッグフーディング(2026-06-10)で「再読込のたびにロード済みモデルが破棄され、
  PID 選択も消える」点が指摘された。動作中の再読込は同日ガードを入れて拒否済み
  (settings_panel `_reload_blocked`)。停止中の再読込での体験改善が本件。
- **対応の見送り理由**: 差分判定は backends 名だけでなく backends_config(モデルサイズ等)・
  認証情報の変化とも整合させる必要があり中規模。再読込の頻度は低く、ロードし直しで
  実害は時間コストのみ(ユーザ合意: 「そこまで困らないので pend 行きでもよい」)。
- **再検討トリガ**: 再読込を多用する運用が出てきたとき / 設定ファイルの手編集ワークフローを
  正式サポートするとき / refactor-ui-3move P4 に着手するとき(同時に扱うと安い)。

---

## [2026-06-10] 「設定を保存」ボタンの auto-persist 化(停止 MVVM 計画からの引き継ぎ)
- **内容**: 設定変更を debounce 付きで自動保存し、「設定を保存」ボタンを撤去する UX 変更。
- **背景**: 停止した MVVM 再構築計画(behavioral-contract 旧 §13.1)で予定されていた項目。
  UI 肥大化対策(`docs/design/refactor-ui-3move/`)を起こす際、肥大化解消とは独立した
  「挙動が変わる UX 変更」のためリファクタリング系列から切り離した。
- **見送り理由**: リファクタリング(ふるまい変更ゼロが原則)に UX 変更を混ぜると、
  契約チェックの基準が曖昧になる。単独の feature ブランチで扱うべき規模。
- **再検討トリガ**: refactor-ui-3move P3 完了後 / 保存忘れによる設定消失が
  ドッグフーディングで発生したとき。

---

## [✅完了 2026-06-10] flaky テスト: `test_set_languages_takes_effect_on_next_utterance`

> **解決(2026-06-10)**: 真因は下記の推定(tk/COM 干渉)ではなく、**テスト自身のレース**だった。
> `WavReplayCapture` は実時間ペーシング無しの全速再生のため、有限 5 秒 PCM(≈52 発話)では
> 「最初の done 検出 → `set_languages` 呼び出し」の間に本スレッドが負荷で遅れると、全発話が
> translator を通過済みになり「切替後の発話」が存在しなくなる。sleep(1.5s) を挟む再現スクリプトで
> 決定論的に再現できた(単体 pass / 並走負荷時のみ fail の観測とも一致)。
> 対応: `WavReplayCapture` に `loop=True`(ループ供給)を追加し、当該テストで使用。
> 切替後の発話が必ず存在するため原理的にレースが消えた(`refactor/ui-phase1-logic-extract` 上で修正)。
> 以下は経緯の記録として残す。
- **対象**: `tests/test_pipeline_e2e.py::TestPipelineE2EWithSynthPcm::test_set_languages_takes_effect_on_next_utterance`
- **症状**: `py -m uv run pytest`(全体実行)で**数十回に 1 回程度**失敗するが、
  単体実行(`pytest <そのテストだけ>`)では必ず pass。
- **原因(推定)**: tkinter の `after` 残りコールバック + `soundcard` の COM 後始末の
  スレッド間相互作用。GUI 系テストで `CTk` ルートを destroy するとき未完了の `after`
  が次テスト中に発火 → tk interp が一時的におかしくなる。過去にも観察され、fixture の
  `after_cancel` 強化等で何度か対処済みだが完全には根治していない。
- **暫定運用**: 全体実行で当該テストが落ちたら、単体実行で pass することを確認して
  「本件と無関係 / 既知 flaky」と切り分け、コミットを進める。CI には載せていないので
  ローカル運用上の問題のみ。
- **対応の見送り理由**:
  - 影響範囲が大きい(`tests/conftest.py` や全 GUI 系 fixture の後始末強化が要る)
  - 頻度が低く、運用上は単体実行で切り分け可能
  - 本筋(機能追加 / バグ修正)の優先度を上げたい
- **再検討トリガ**: 失敗頻度が顕著に上がる / 別の flaky が連鎖して切り分けが困難になる
  / CI を導入するタイミング(全体実行で stable が要求される)。

---

## [✅完了 2026-06-05] ProcTap 取り込み 段階 2: ProcTapCaptureBackend 本体実装
- **対応ブランチ**: `feature/proctap-backend`
- **対応内容**:
  - `pyproject.toml` extras に `capture-proctap = ["proc-tap>=0.4"]`(scipy は proc-tap が連れてくる)
  - `src/voice_translator/capture/proctap_backend.py`:
    - `capture_kind() = PROCESS`、`list_sources()` は段階 3 まで空リスト仮実装
    - `start(pid_str)` で `int(source_id)` → `ProcessAudioCapture` 構築
    - `read_chunk()` で bytes → np.frombuffer → reshape(-1,2).mean(axis=1) →
      `scipy.signal.resample_poly(up=1, down=3)` で 48kHz/2ch → 16kHz/mono へ変換
    - 各失敗ケース(extras 未インストール / 不正 PID / WASAPI 起動失敗 / read 失敗)を FatalError で包む
  - `backend_setup.py` に opt-in register、`ConfigStore` に `backends_config.proctap.{auto_load,resample_quality}` 既定
  - small 16 件 + large 1 件(実 ProcTap で自プロセス録音、Python 自身 PID 指定)— pass 済み
  - PyPI 配布の `proc-tap==1.0.3` が Python 3.12 wheel として動作することを確認
- **未対応(段階 3 へ)**: `list_sources()` のプロセス列挙(`pycaw`)+ エコーバック確認 UI。
  現状は GUI プルダウンから選べないため、`config.yaml` の `devices.input` に PID 文字列を
  直接書く運用が必要。

---

## [✅完了 2026-06-05] ProcTap 段階 3 のスレッド処理を Peak Worker 方式に整理
- **対応ブランチ**: `refactor/process-peak-worker`
- **対応内容**:
  - 段階 3 初版は GUI スレッドでの pycaw 呼び出しが COM モード競合 (`RPC_E_CHANGED_MODE`) を
    起こす問題に対し、毎 peak ごとに新規スレッドを `start()` + `join()` する暫定対応だった
    (30fps × 60sec = 1800 スレッド生成/分という非効率)
  - 永続 COM ワーカスレッド `_PeakWorker` を導入(1 個固定、`CoInitialize` も 1 回だけ)。
    試聴中はワーカが内部で **5fps poll** で peak を取って `_latest_peak: float` を atomic 保持し、
    GUI スレッドは `latest_peak()` を atomic 読みするだけ
  - 公開 API を `start_audition(pid)` / `stop_audition()` / `latest_peak()` / `is_auditioning()`
    / `dispose()` に整理。旧 `get_session_meter` / `_MeterProxy` / `_run_in_com_thread` は廃止
  - `ProcessSelectController` は `_PeakProvider` Protocol 経由に変更(本番は `process_enumerator`
    モジュールを束ねた `_DefaultProvider`、テストは fake)
- **効果**:
  - 試聴中の毎秒スレッド生成: 30 → 0(永続スレッド 1 個)
  - CoInitialize 回数: 30/sec → 1/process
  - GUI スレッドと COM 操作が完全分離
- **未対応**(下記別エントリ):

---

## [✅完了 2026-06-05] ProcTap 取り込み 段階 3: プロセス列挙と試聴メータ
- **対応ブランチ**: `feature/proctap-process-list`
- **対応内容**:
  - `pyproject.toml` の `capture-proctap` extras に `pycaw>=20240210` / `psutil>=5.9` 追加
  - `src/voice_translator/capture/process_enumerator.py` 新規:
    - `enumerate_active_processes() -> list[CaptureSource]`(`AudioSessionState.Active` のみ、PID 単位 dedupe、psutil でプロセス名補完、失敗時 `"unknown"`)
    - `get_session_meter(pid)`(試聴ダイアログ用に `IAudioMeterInformation` を返す)
    - pycaw / psutil 呼び出しは `_list_active_sessions` / `_resolve_process_name` に隔離、テスト時 monkeypatch 完全置換可
  - `ProcTapCaptureBackend.list_sources()` を `process_enumerator` 経由の本実装に切替(段階 2 の空リスト仮実装を廃止)
  - `src/voice_translator/gui/process_select_dialog.py` 新規:
    - `ProcessSelectDialog`(CTkToplevel) + `ProcessSelectController`(GUI 非依存ロジック)
    - PID テーブル + ↻ 更新 + ▶ 試聴開始・■ 停止トグル + レベルメータ + OK/Cancel
    - 試聴は本番パイプラインと完全独立(pycaw `GetPeakValue()` を 30fps poll、WASAPI Process Loopback は開かない)
  - `SettingsPanel`: `capture_kind == PROCESS` のとき source プルダウンを「プロセス選択…」ボタンに切替、押下で `ProcessSelectDialog` を開く
  - `ControlPanel._sync_ready_state`: 「PROCESS kind かつ source 未選択」分岐追加(Start を「プロセス未選択」で disable)
  - `AppController.save_settings()` / `load_settings()`: A-7 確定方針として PROCESS kind の `devices.input` を空文字に正規化(永続化しない+起動時もセーフティで空)
- **検討経緯と判断記録**: `tmp/report1.md`(着手前論点整理 / 確定版)
- **未対応の派生項目**(下記別エントリに起票):
  - 動的列挙更新(プロセス起動/終了の追従)
  - Linux/Mac の process-kind 列挙
- **やらないと確定したもの**: プロセス名 / exe path での PID 永続化 → A-7 で「保存しない」方針が確定。再起動で都度選択する UX。

---

## [⏳保留 2026-06-05] ProcessSelectDialog の動的列挙更新
- **対象**: 段階 3 で実装した `ProcessSelectDialog` のプロセス一覧を、ダイアログを開いたまま
  プロセスが起動/終了したときに自動追従する。
- **現状**: 列挙はダイアログを開いた瞬間に 1 回 + 「↻ 更新」ボタンで手動再列挙。
- **見送り理由**: 段階 3 のスコープを「列挙 + 試聴」に絞ったため。手動更新ボタンで十分実用に
  なるかをドッグフーディングで観察してから着手するかを判断する。
- **着手トリガ**: 「↻ 更新の押し忘れで取りこぼした」事案が複数件出たら。

---

## [⏳保留 2026-06-05] Linux/Mac の process-kind 列挙
- **対象**: 配布方針(CPU を floor / OS 抽象を維持)で「Windows 以外でも per-process キャプチャを
  選びたい」需要が出たときの対応。
- **現状**: `process_enumerator` は pycaw / psutil に直接依存し、Windows 専用。
- **対応案**: Strategy / Adapter で OS 別 enumerator を差し替え可能にする(`LinuxPulseAudioEnumerator`
  / `MacCoreAudioEnumerator` 等)。
- **見送り理由**: ProcTap 自体が Windows 専用のため、OS 横断はまだ需要が見えない。
- **着手トリガ**: Linux/Mac で per-process キャプチャ可能な backend が登場したとき。

---

## [✅完了 2026-06-05] 出力モード(TTS=(なし))対応 — text_only モード
- **対応ブランチ**:
  - `feature/text-only-output`(初実装。`pipeline.output_mode` キーで切替)
  - `refactor/text-only-via-tts-none`(出力モードを `backends.tts` から派生する形に統合)
- **対応内容**:
  - `PipelineCoordinator` に `output_mode` パラメータを追加。`text_only` のとき
    Input / ASR / Translator の 3 スレッドのみ起動、Translator 完了で `on_text_ready`
    発火 + `ledger.pop()` でバッファ即解放
  - TTS / Output レイヤの backend ロード・認証 gate を `text_only` 時に skip
  - SettingsPanel の TTS プルダウンに「(なし)」を追加、選択で text_only モード
    (内部値 `"none"`)。Output 行はグレーアウト
  - 動作中の即時モード切替は対象外(次回 Start で反映)
- **派生で出た保留項目**: 提案 A(動作中の音声出力 graceful 切替)/ 提案 C(進行中発話の
  キャンセル機構)は別途下記に起票。

---

## [⏳保留 2026-06-05] 動作中デバイス変更時の graceful 切替
- **対象**: `feature/runtime-flex-and-input` P4 の「動作中デバイス変更=停止→再開」方式の発展。
- **背景**: P4 では停止→再開で再生バッファを捨てる。バッファ再生後に切り替えれば中断が
  目立たないが、Output スレッドを独立して止める仕組みが必要。
- **見送り理由**: 停止→再開で十分許容できる体感の見込み。動作を見てから判断。
- **着手トリガ**: P4 完了後、停止→再開の体感ラグが運用上 NG と判明したら。
- **関連**: `feature-runtime-flex-and-input/Plan.md` の「提案 A」。

---

## [✅完了 2026-06-05] ProcTap backend 実装(per-process キャプチャ)
- **対応ブランチ**: `feature/proctap-backend`(段階 2 / 本体実装)+
  `feature/proctap-process-list`(段階 3 / プロセス列挙 + 試聴メータ)+
  `refactor/process-peak-worker`(永続 COM ワーカ整理)
- **対応内容**: 上記 ProcTap 取り込み 段階 2 / 段階 3 / Peak Worker 整理の
  3 エントリにそれぞれ詳細記載。`uv sync --extra capture-proctap` で PyPI から
  `proc-tap==1.0.3` / `pycaw` / `psutil` が入り、SettingsPanel の「プロセス選択…」
  ダイアログから PID を選んで per-process キャプチャができる状態。
- **関連**: 下記「入力処理レイヤーの改善案」エントリも Windows 部分は本対応で完了。
  Linux / Mac の per-process 取得は別エントリ「Linux/Mac の process-kind 列挙」参照。

---

## [⏳保留 2026-06-05] 動作中の capture / VAD / ASR / Translator / TTS backend 変更で自動 restart
- **対象**: `feature/dynamic-devices` (P4) で「動作中の **デバイス変更** で自動 restart」を実装した。
  `feature/capture-backend-split` (P5) で CAPTURE backend 切替時のソース一覧 refresh も実装。
  だが「動作中に **backend 自体を切り替えた** ときの restart」は対象外。
- **背景**: backend 切替 = 旧 backend を evict + 新 backend をロード(バックグラウンド)。
  動作中の Coordinator は旧 backend を握ったままなので、新 backend を有効にするには restart が必要。
  これは CAPTURE に限らず全レイヤ(VAD/ASR/Translator/TTS)に共通する課題。
- **見送り理由**: backend ロードはレイヤや実装によって数秒〜数十秒かかる(faster-whisper medium 等)。
  ロード完了を待ってから restart する UX 設計と、ロード失敗時の挙動(旧 backend に戻す? エラー?)を
  詰める必要がある。実装規模が大きい。
- **対応案(着手時)**:
  - AppController で「未ロードレイヤがあれば順次ロード → 完了後に restart_pipeline_async を呼ぶ」
    ヘルパを追加。バナーは「(レイヤ)backend をロード中… / 再開中…」の段階表示。
  - 失敗時は旧 backend にロールバックするか、エラー表示で停止のまま留めるかを選択。
- **着手トリガ**: ProcTap など「動作中に切り替えたい」需要が強い backend が来たとき。

---

## [⏳保留 2026-06-05] 動作中言語切替時の進行中発話の扱い
- **対象**: `feature/dynamic-languages` で「言語切替は次発話から反映」の仕様にしたが、
  既にキューに入っている発話は古い言語のまま流れる。
- **背景**: 体験悪化の頻度が読めない。実機で見てから判断したい。
- **見送り理由**: 即時の影響は限定的(1〜2 発話のみ)。実運用で NG となれば対応。
- **着手トリガ**: 「言語を変えたつもりが古い言語の翻訳が出てくる」苦情が出たら。
- **対応案**: `captured_queue` / `recognized_queue` を drain して新言語で再 capture、
  または「切替時刻以降の発話のみ通す」フィルタを Input/ASR スレッドに置く。

---

## [📌方針 2026-05-30 / 改 2026-06-05] 追加モデル続行・UI 調整・配布形態の段階対応(複数日)

| 項目 | 状態 | 備考 |
|---|---|---|
| **a) backendCandidates の残り picks 実装** | ✅完了 | ASR(openai_whisper_api / deepgram / google_stt) と Translator(deepl / openai_gpt / anthropic_claude) を `feature/asr-picks` / `feature/translator-picks` で実装。後続で TTS picks(piper / elevenlabs / openai_tts / google_cloud_tts) も `feature/tts-picks` で実装済 |
| **d) ライセンス規約のあるモデルを README に明記** | ✅完了 | 2026-06-05 に README 全体を整理(VAD / Capture / ASR / Translator / TTS の各セクションに必要な利用同意 URL を併記。Windows 専用機能セクションも追加) |
| **b) UI 調整** | ⏳一部完了 / 一部未着手 | 折り畳みは `feature/ui-sections-split` + `feature/ui-adjustments` で完了。NotificationBanner も追加済。残: 詳細ダイアログから抜けたときの再描画タイミング / 認証ダイアログ閉じた直後の状態反映の挙動(個別エントリ化するか、ドッグフーディングで再検討) |
| **c) 配布形態(実行環境のポーティング / インストーラ)** | ⏳保留(未着手) | 現状は `git clone + uv sync --extra cpu` 前提。非開発者向け配布(PyInstaller / Nuitka one-folder / Windows MSI 等)は未着手。モデル DL は配布物に含めず初回起動時 DL とする方針は維持 |

---

## [⏳保留 2026-05-31] TTS 音声クローニング(Voice Cloning)対応
- **対象**: ElevenLabs Instant Voice Cloning(IVC, 1 分音声 → 即 voice_id 発行)等、ユーザのサンプル音声から voice を作る機能。Coqui XTTS-v2 のような zero-shot ローカルクローニングも将来候補。
- **背景**: `feature/tts-picks` 検討時に挙がった機能。今回ピックの 4 backend(Piper / ElevenLabs / OpenAI TTS / Google Cloud TTS)は **全てプリメイド voice を持ち、クローニング無しで完結する** ため、MVP の TTS としては不要と判断。
- **対応の見送り理由**: プリメイド voice(Piper の HF 配布モデル / ElevenLabs の Rachel 等 30 種 / OpenAI 6 voice / Google の Wavenet 多数)で MVP は十分成立。アプリ内 UI(サンプル音声選択 / 一覧 / 削除)+ paid 限定 API のテスト戦略は別途検討が必要。
- **対応案(着手時)**:
  - `TtsBackend.capabilities` に `supports_voice_cloning: bool` を追加し、True の backend のみ「声の管理…」UI を出す。
  - I/F: `add_voice(name, sample_paths) -> VoiceRef` / `list_voices() -> [VoiceRef]` / `delete_voice(ref)`。
  - 保存は `config.yaml` の `tts.<backend>.selected_voice_id` のみ。voice 一覧は backend 側(クラウド)が source of truth。
  - 別ブランチ `feature/tts-voice-cloning` で対応。
- **再検討トリガ**: ユーザから「自分の声で読ませたい」要望が出た時 / ElevenLabs を実運用で使い込んだあと。
- **関連**: `docs/design/append/backendCandidates.html` の TTS テーブル(ElevenLabs 行の備考でプリメイド主軸と明記)。

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

## [⏳保留 2026-05-29 / 縮小 2026-06-10] cloud backend 認証テスト(skip スケルトンの埋め込み)
> **縮小(2026-06-10)**: asr-picks / translator-picks / tts-picks の実装で OpenAI Whisper API /
> DeepL / OpenAI TTS / Anthropic Claude / Google(file_picker 型含む)の分は実テスト化済み。
> **skip で残るのは AWS Transcribe の 5 件のみ**(backend 未実装のため。実装時に有効化)。
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

## [✅完了 2026-06-05] UI セクションの折り畳み(設定全体 / ステータス)
- **対応ブランチ**: `feature/ui-adjustments`(ステータステキスト分) /
  `feature/ui-sections-split`(設定パネル 3 セクション分割)
- **対応内容**:
  - `CollapsibleSection` widget を作成(ヘッダクリックで body を `.grid_remove()` / `.grid()`)
  - ControlPanel のステータステキストボックスを折り畳み対応(`ui.collapsed.status_text`)
  - SettingsPanel を「バックエンド / デバイス / 翻訳」の **3 セクション独立折り畳み**に分割
    (`ui.collapsed.{backends, devices, languages}`)。ログ出力先 / 保存ボタン群は共通行
    として下部に維持(畳めない)
- **元の要望**: MainWindow 上の SettingsPanel(バックエンド・デバイス・翻訳言語)と
  ControlPanel のステータステキストボックスを、それぞれ畳めるようにしたい。

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

## [✅完了 2026-06-05 (Windows のみ) / ⏳残り保留] 入力処理レイヤーの改善案
- **Windows 部分は完了**: `feature/proctap-backend` + `feature/proctap-process-list` +
  `refactor/process-peak-worker` で `ProcTapCaptureBackend`(WASAPI Process Loopback)+
  プロセス選択ダイアログ + 試聴メータを実装。per-app / per-process キャプチャ要件は
  Windows で達成。
- **残: Linux / Mac** の per-process キャプチャは別エントリ「Linux/Mac の process-kind 列挙」
  で起票済み(着手トリガ待ち)。
- **当時の議論(参考)**: 案 B(入力処理を別 module 化、ネイティブ API 直叩き)を採用し、
  Windows = WASAPI Process Loopback の C++/Rust 実装(`proc-tap` PyPI パッケージ経由)に
  落とし込んだ。下記は当時の検討メモ。

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

## [✅完了 2026-06-05] 音声入力状態の可視化UI(サブダイアログ)
- **対応ブランチ**: `feature/proctap-process-list` + `refactor/process-peak-worker`
- **対応内容**: `ProcessSelectDialog` 内のレベルメータ(`CTkProgressBar`)で実現。
  pycaw `IAudioMeterInformation.GetPeakValue()` を永続 COM ワーカ(`_PeakWorker`)が
  5fps で内部 poll し、GUI スレッドは `latest_peak()` を atomic 読みするだけ。
  「鳴っているか / 入力できているか」の視覚確認はプロセス選択 UI 内で完結する。
- **元の要望との差分**: 元案は「常駐サブダイアログで時系列グラフ」だったが、実利用上は
  プロセス選択時の試聴メータで十分実用と判断。本番動作中の常駐メータが必要になったら
  別エントリで再起票する。

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

## [✅完了 2026-06-10] AppController の責務分離(リファクタ予約)
- **解決**: `refactor-ui-3move`(P1〜P3)で対応。ただし採用形は当時の方針案と異なる:
  - UI 判断ロジックは `gui/logic/` の純関数へ(P1)、通知は `add_<event>_listener`
    (Subscription)1 本に統一(P2)、メタ問合せ → `BackendCatalog` / 認証 →
    `CredentialsService` に分離(P3)
  - `ModelLoaderService` / `StatusBroadcaster` / `LatencyBuffer` は**不採用**
    (ロード・起動停止は `_load_lock` / `_backends` を共有しており、切ると配線が純増するため
    ランタイムとして AppController に残す判断。Roadmap §1「やらないこと」参照)
- **確立した規約**: `Architecture.html §9`(GUI 内部構成と UI 実装規約)+ CLAUDE.md
- **経緯の記録**: `docs/design/refactor-ui-3move/`(マージ後は done/ 配下)
- 以下は起票時の記録:
- **背景**: `feature/backend-mgmt` の Phase A2 で AppController に layer 単位 load / multi-listener / 処理時間 buffer 等の orchestration 責務が追加される。R2-1 解消の分散化で `_model_status` dict は消えるものの、全体としては引き続き肥大化傾向
- **方針案**(将来のリファクタ時):
  - `ModelLoaderService`(ロード処理)
  - `StatusBroadcaster`(listener 管理 + re-broadcast)
  - `LatencyBuffer`(layer 別 処理時間)
  - AppController は composition でこれらを保有する形に整理
- **見送り理由**: 動かしてから整理する方が安全(早すぎる抽象化を避ける)
- **再検討トリガ**: `feature/backend-mgmt` ブランチ完了後 / さらに新機能で AppController が肥大化したタイミング

---

## [2026-05-29] リトライ機構の効果検証 — ✅決着(2026-06-10: 採用継続・現状維持)
- **決定(2026-06-10)**: リトライ機構は**現状のまま採用継続**。「3 連続失敗 → STOP(全停止)」は
  意図的設計とする(3 連続失敗 ≒ 依存 API 側の障害で以降も失敗が続く公算が高く、粘るのは無駄。
  ユーザはローカル backend への切替でしのぐ、という運用)。
- **起票時の懸念(キュー詰まり)の机上決着**: 実装は (1) リトライのブロックに上限がある
  (3 回・バックオフ 0.5→1→2 秒)、(2) PCM キューはバイト上限付きで**最古から退避**するため、
  障害中にバックログが伸び続ける構造ではない。「詰まり続けて予後が悪化」は起きない。
- **残す観察項目(ドッグフーディング中の任意確認)**: 実クラウド backend + ネット切断で
  縮退挙動(停止までの体感)を一度見る。瞬断のたびに止まって煩わしければ
  「枯渇 → SKIP(発話破棄で継続)+ WARN 通知」化を再検討(数十行 + small テストの規模)。
- **背景(起票時)**: クラウド backend の 3 回リトライ実装に対し「リトライ中も上流 capture が
  動き続けてキューが詰まる」懸念(knownRisks R-4)があり、効果薄なら撤回(失敗即停止)も
  視野に実機検証を予定していた。

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