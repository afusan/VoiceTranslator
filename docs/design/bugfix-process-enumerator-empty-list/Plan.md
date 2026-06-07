# bugfix/process-enumerator-empty-list

WASAPI プロセス列挙の採用範囲の誤りを修正する。「音が出ているのにプロセス選択
ダイアログが空」という症状の根治。

---

## 1. 症状(別環境で観測)

事前条件:
- proctap backend(`--extra capture-proctap`)で動かす
- 音量ミキサーに Chrome / Firefox / Spotify 等が表示されている状態
- 既定の再生デバイスはスピーカ、実際に音が出ている

操作: SettingsPanel → 「プロセス選択…」ダイアログを開く

期待: 音を出しているアプリが列挙される(Chrome, Spotify など)

実際: **0 件**(または「(プロセスなし)」)。Start ボタンは「プロセス未選択」のままで押せない。

---

## 2. 原因分析

### 2-1. WASAPI `AudioSessionState` の仕様(Microsoft 公式)

| 値 | 名前 | 意味 |
|---|---|---|
| 0 | Inactive | セッションは存在するが、現在ストリームが running 中ではない(`Stop` 後 / 未 `Start`) |
| 1 | Active | 少なくとも 1 つのストリームが running 中(`IAudioClient::Start` 直後の瞬間) |
| 2 | Expired | セッション終了済み |

重要な事実:
- `Active → Inactive` は **クライアントが `IAudioClient::Stop` を呼んだ瞬間に起きる**
- **Sndvol(Windows の音量ミキサー)は Active と Inactive 両方を表示する**(Expired のみ表示しない)

### 2-2. Windows 11 の audio engine の挙動

- **無音 10 秒で audio engine が sleep モード**に入り、セッションが Inactive に落ちる
- mpv / Audacity 等の事例でも同様の症状が複数報告されている

### 2-3. アプリ側の実装パターン

- Spotify や多くのプレイヤーは **無音区間で `IAudioClient::Stop` を呼ぶ**実装
- 結果: 観測時点では「再生中のアプリ」も Inactive 状態のことが支配的

### 2-4. 旧コードの誤り

`process_enumerator._is_active` は `state == Active(1)` のみを採用していた:

```python
def _is_active(session):
    state = session._ctl.GetState()
    return int(state) == _AUDIO_SESSION_STATE_ACTIVE
```

仕様上は「Active = 一瞬しか取れない状態」なので、ポーリング型の列挙では
**Active だけを拾うのは原理的に厳しい**。Sndvol と同じ範囲(Active + Inactive)を
採用すべきだった。

---

## 3. 対応内容

### 3-1. フィルタ範囲の拡張(第 1 段)

`_is_active` を `_is_capturable` に改名し、採用条件を `state in {Inactive, Active}`
に拡張(Expired のみ除外)。Sndvol の表示集合と完全一致。

```python
_CAPTURABLE_STATES = frozenset({0, 1})  # Inactive + Active(Expired を除外)
```

### 3-2. 全 Render エンドポイント走査(第 2 段、2026-06-08 追加)

第 1 段でフィルタを緩めても **別環境では依然 0 件** の状況が観測された。

診断ランナー(`runner_proc_list`)で IMMDeviceEnumerator 経由の全エンドポイント
走査を追加したところ、firefox / chrome が **デフォルト以外のエンドポイント
(Device 1)** に紐づいていることが判明:

```
[Device 0] {既定}                  → システムセッションのみ
[Device 1] {実際のスピーカ / 別 GUID} → firefox.exe / chrome.exe ★
[Device 2] ...                     → システムのみ
[Device 3] ...                     → システムのみ
```

`AudioUtilities.GetAllSessions()` は **デフォルトエンドポイントの
`IAudioSessionManager` しか見ない** ため、Windows 11 + 複数オーディオデバイス
構成(HDMI / Bluetooth / 仮想デバイス等)で別エンドポイントに紐づくアプリは
原理的に取りこぼされる。

対応として `_list_active_sessions()` を全 Render エンドポイント走査に書き換え:

```
IMMDeviceEnumerator
  → EnumAudioEndpoints(eRender, DEVICE_STATE_ACTIVE)
    → 各 IMMDevice
      → Activate(IAudioSessionManager2, INPROC_SERVER)
        → GetSessionEnumerator()
          → GetCount() / GetSession(j)
            → IAudioSessionControl + QueryInterface(IAudioSessionControl2)
              → GetProcessId() / GetState()
```

同一 PID が複数エンドポイントに居る場合は **最初に見つけた 1 件のみ採用(dedupe)**。
proc-tap は PID 指定でキャプチャするので、列挙時のエンドポイントは関係ない。

### 3-3. `_query_meter` / `_is_capturable` の互換性維持

旧仕様の pycaw `AudioSession` ラッパー(`session._ctl.X`)経由も互換のため
fallback で残す。新仕様(生 `IAudioSessionControl`)では `session.X` を直接呼ぶ
ルートを優先する 2 段構成。テストや別 backend からの利用に備えた防御。

### 3-4. 関連箇所の整理

- `enumerate_active_processes()` の docstring を新仕様に更新
- 関数名は歴史的経緯で `_list_active_sessions` のまま(実体は Active + Inactive、
  かつ全エンドポイント)
- `runner_proc_list`(診断ツール)に全エンドポイント走査セクションを追加し、
  どの Device に何のセッションが居るかを可視化する

### 3-5. 件数爆発の懸念について

WASAPI セッションを持つのは「音を出す/出した可能性のあるアプリ」のみで、
**起動中の全プロセスではない**。具体的には音量ミキサーに表示されるアプリと同じ集合:

| 持つ | 持たない |
|---|---|
| Spotify / iTunes / Chrome / Firefox(動画/通知音) | Notepad / メモ帳 / エクスプローラ |
| Discord / Teams / Zoom(通話) | IDE / エディタ全般 |
| ゲーム / システム音 | 多くの常駐ツール |

実用上は通常 5〜10 件(別環境のスクショで実際 4 件だった実績あり)。

### 3-6. 自プロセス除外(フィードバックループ防止)

本アプリは翻訳音声を Output デバイスに出すため、自プロセスも WASAPI セッションを
持つことがある(SAPI / soundcard が WASAPI セッションを開く)。これをユーザに
選ばせると「翻訳音声 → 自分の音を再キャプチャ → 再翻訳」のフィードバックループに
陥り、CPU が無限に回る。

`_list_active_sessions()` で `pid == os.getpid()` を除外する 1 行で対処。
`DeviceValidator(入力 ≠ 出力)` と同じ思想の防衛策。

### 3-7. proc-tap 側の対応は不要

proc-tap は **PID 指定で動く**ため、Inactive な状態の PID でも、ユーザが
Start を押した時点で実際に音が鳴っていれば普通にキャプチャできる。
列挙時点の状態と、キャプチャ時点の状態は独立した話。

---

## 4. 残課題: pycaw 20251023 の挙動

別環境で **pycaw 20251023** にすると、`AudioUtilities.GetAllSessions()` が
PID 0 のシステムセッション 1 件しか返さない症状あり(firefox / Spotify 等が消える)。
pycaw 20240210 にダウングレードすると 3 件返るようになる(全て Inactive)。

| バージョン | GetAllSessions の結果 |
|---|---|
| 20240210 | firefox / Spotify / システム の 3 件(Inactive) |
| 20251023 | システム 1 件のみ |

これは本変更とは独立した話で、pycaw バージョン側の挙動変化を疑っている。
ただし **pycaw のバグと断定するには情報が足りない**(comtypes 側の変化、Win11 24H2
の挙動変化、別の API パスの変化など、複数可能性あり)。本ブランチでは扱わず、
個別調査として残す。

なお **第 2 段の全エンドポイント走査(IMMDeviceEnumerator 経由)** が `GetAllSessions`
を経由しないため、20251023 でもデフォルト以外のエンドポイントからセッションを
拾えるようになった可能性がある(別環境で要確認)。これが効くなら 20251023 のまま
でも動く可能性があり、ダウングレードの必要性も再評価できる。

### 暫定運用

`uv pip install pycaw==20240210` でダウングレードして使う(`uv` は venv を自動検出するため
`py -m uv` ではなく `uv` で起動する。`uv run --no-sync` で起動して sync の再上書きを防ぐ)。
`pyproject.toml` でのバージョン上限ピンは入れない(調査未完のため)。

---

## 5. 関連ファイル

- `src/voice_translator/capture/process_enumerator.py` — 本体修正
- `tests/test_process_enumerator.py` — テストを新仕様に書き換え
- `src/voice_translator/dev/runner_proc_list.py` — 診断メッセージ更新

詳細なテスト項目は [testPlan.md](testPlan.md) 参照。
