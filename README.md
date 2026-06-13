# voice-translator

ローカル PC で完結するリアルタイム音声翻訳アプリ。

PC 内で再生されている音声(配信 / 通話など)やマイク入力を取り込み、
別言語に翻訳した音声を別の出力デバイスから再生する。

```
[入力デバイス] → VAD → ASR → 翻訳 → TTS → [出力デバイス]
              (発話区切り)(書き起こし)(翻訳)(音声合成)
```

- 既定構成は**すべてローカルで動作**(音声を外部に送らない)。クラウド backend は opt-in
- **CPU だけで動く**。NVIDIA GPU があれば自動で使われ、大幅に速くなる
- レイヤごとに backend を**プルダウンで差し替え可能**(ローカル / クラウド)
- 特定アプリの音だけ翻訳(per-process キャプチャ、Windows)/ 字幕だけの軽量モードも可
- 仮想マイクと組み合わせれば「自分の声を翻訳して通話相手に送る」ことも可能

**対応 OS**: Windows 11(主対象)。macOS / Linux は一部 backend を除き動作
([manual §2](docs/manual.md#2-動作環境) 参照)。

---

## クイックスタート

```bash
# 1) uv をインストール(いずれか)
py -m pip install --user uv
# or: powershell -c "irm https://astral.sh/uv/install.ps1 | iex"

# 2) セットアップ — cpu / cuda はどちらか必須(排他)
py -m uv sync --extra cpu            # CPU 専用(誰でも動く)
# py -m uv sync --extra cuda         # NVIDIA GPU 向け(+約3GB、CUDA Toolkit 不要)
# 追加 backend を全部入れるなら: --extra full を足す

# 3) 起動 — sync 時と同じ extras を付ける(省くと extras が剥がされる)
py -m uv run --extra cpu python -m voice_translator
```

- **初回起動**: 「▶ 開始」時にモデル DL(計 3GB 弱)が走る。以降はオフラインで動く
- **入力と出力は別のデバイスを選ぶ**(例: 入力 `[LB] Speakers` / 出力 `Headphones`)
- インストール詳細・使い方は **[docs/manual.md](docs/manual.md)** を参照

---

## backend 一覧

| レイヤ | 既定(ローカル) | extras で追加 |
|---|---|---|
| 音声取得 | soundcard | proc-tap(Windows、per-process) |
| VAD | silero | webrtcvad / pyannote / pvcobra |
| ASR | faster_whisper | openai_whisper / クラウド 3 種 + ASR+翻訳の複合 3 種 |
| 翻訳 | NLLB-200(200 言語) | DeepL / OpenAI GPT / Anthropic Claude |
| TTS | sapi(Windows) | piper / クラウド 3 種 / 「(なし)」= 字幕モード |
| 音声出力 | soundcard | — |

- クラウド backend は**同意ダイアログ + API key の疎通テスト**を通すまで起動しない。
  API key は OS の資格情報ストアに保管される
- **注意**: 既定翻訳 NLLB-200 のモデル重みは **CC-BY-NC 4.0(非商用限定)**。
  商用文脈ではクラウド翻訳へ切り替えること
- 各技術のライセンスと規約リンクは **[LICENSE.md](LICENSE.md)** を参照

---

## ドキュメント

- [使い方マニュアル](docs/manual.md) — インストール / 操作 / チューニング / トラブルシュート
- [LICENSE.md](LICENSE.md) — ライセンス・使用技術一覧
- [アーキテクチャ](docs/design/Architecture.html) / [クラス詳細](docs/design/Class.md)(開発者向け)

## 開発

```bash
py -m uv run pytest        # 単体テスト(small)
```

テスト方針・運用ルールは [CLAUDE.md](CLAUDE.md) を参照。
