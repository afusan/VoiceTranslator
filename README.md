# voice-translator

ローカルで動作する音声翻訳アプリ。
PC内で再生される音声(YouTube/Twitch 等)やマイク入力をリアルタイムに翻訳し、
別デバイスから音声で再生する。

**MVP は Windows 11 想定。** 言語/設計の詳細は [docs/](docs/) を参照。

---

## クイックスタート

```bash
# 1) uv をインストール(いずれか)
py -m pip install --user uv
# or: powershell -c "irm https://astral.sh/uv/install.ps1 | iex"

# 2) 仮想環境と Python 3.11 を自動セットアップ
py -m uv sync

# 3) 起動(GUI が立ち上がる)
py -m uv run python -m voice_translator
```

> **初回起動の注意**: GUI 起動後、「開始」ボタンを押すと ASRモデル(150MB) と
> 翻訳モデル(NLLB-200 約2.5GB) のダウンロードが走ります。数分〜十数分かかります。
> 2回目以降はキャッシュから読み込まれます。

---

## デバイス選び方(重要)

**入力と出力に別デバイスを選んでください**。同じだとフィードバックループになります。

例(英語YouTube → 日本語音声):
- 入力: `[LB] Speakers`(PC既定スピーカのループバック)
- 出力: `Headphones`(ヘッドホン側)

---

## 構成

```
[入力] → [VAD] → [ASR] → [翻訳] → [TTS] → [出力]
```

| レイヤ | MVP実装 | 役割 |
|--------|---------|------|
| 音声取得 | soundcard | デバイス/LBから 16kHz/mono/float32 で取得 |
| VAD | silero-vad | 発話区切り検出 |
| ASR | faster-whisper | 書き起こし(transcribe固定) |
| 翻訳 | NLLB-200 distilled 600M | 200言語対応のローカル翻訳 |
| TTS | SAPI (pyttsx3) | 音声合成(WAV経由でPCM取得) |
| 音声出力 | soundcard | 指定デバイスで再生 |

各レイヤは差し替え可能な抽象I/Fで設計(Phase 2 以降で別実装追加予定)。

---

## 開発

```bash
py -m uv run pytest               # 126件のテスト
py -m uv run pytest --cov=src     # カバレッジ
py -m uv add <package>            # 依存追加
```

---

## ドキュメント
- [使い方 (manual)](docs/manual.md)
- [アーキテクチャ](docs/design/Architecture.html)
- [クラス詳細](docs/design/Class.md)
- [ユーザシナリオ](docs/design/UserSinario.md)
- [全体タスク(Phase 0〜5)](docs/design/TaskList.md)
- [保留・暫定決定](docs/design/pendList.md)
