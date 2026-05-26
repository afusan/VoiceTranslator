# voice-translator

ローカルで動作する音声翻訳アプリ。
PC内で再生される音声(YouTube/Twitch等)やマイク入力をリアルタイムに翻訳し、別デバイスから音声で再生する。

詳細・設計は [docs/](docs/) を参照。

## セットアップ

```bash
# uv が未インストールなら入れる(いずれか)
py -m pip install --user uv     # pip経由
# or: powershell -c "irm https://astral.sh/uv/install.ps1 | iex"

# 仮想環境とPythonの準備(自動)
py -m uv sync

# テスト実行
py -m uv run pytest
```

## ドキュメント
- [使い方 (manual)](docs/manual.md)
- [アーキテクチャ](docs/design/Architecture.html)
- [クラス詳細](docs/design/Class.md)
- [ユーザシナリオ](docs/design/UserSinario.md)
- [全体タスク](docs/design/TaskList.md)
