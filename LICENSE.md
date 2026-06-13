LICENSE:MIT

意見：
金が欲しくないわけではないが、これは金をとれるようなものではないし、SNSでゴミを売る連中と同一視されたくはない。
もし、これを使って何かしら得をしたのであれば、これらのベースとなった技術に対して敬意とできればお金を払ってほしい。
私はただできそうだと考え、これらを繋ぐ様にLLMに頼んだだけだ。
需要があるなら、LLM提供者やプラットフォーマが全てをパッキングし、洗練したサービスをそのうち提供するだろう。それまでのつなぎだと思ってほしい。

Links

このアプリは以下の技術の上に成り立っている。

言語・フレームワーク:
- [Python](https://www.python.org/) (PSF License) — 言語
- [uv](https://github.com/astral-sh/uv) (MIT / Apache-2.0) — 依存・環境管理
- [customtkinter](https://github.com/TomSchimansky/CustomTkinter) (CC0-1.0) — GUI
- [PyTorch](https://pytorch.org/) (BSD-3-Clause) — ML ランタイム
- [Transformers](https://github.com/huggingface/transformers) (Apache-2.0) / [SentencePiece](https://github.com/google/sentencepiece) (Apache-2.0) — モデルロード・トークナイザ
- [NumPy](https://numpy.org/) (BSD-3-Clause) / [PyYAML](https://github.com/yaml/pyyaml) (MIT) / [keyring](https://github.com/jaraco/keyring) (MIT) / [pytest](https://pytest.org/) (MIT)

バックエンド(既定構成・ローカル):
- [soundcard](https://github.com/bastibe/SoundCard) (BSD-3-Clause) — 音声取得 / 出力
- [Silero VAD](https://github.com/snakers4/silero-vad) (MIT、モデルも MIT) — 発話区切り
- [faster-whisper](https://github.com/SYSTRAN/faster-whisper) / [CTranslate2](https://github.com/OpenNMT/CTranslate2) (MIT) — ASR。[Whisper](https://github.com/openai/whisper) モデル (MIT) は OpenAI 製
- [NLLB-200](https://huggingface.co/facebook/nllb-200-distilled-600M) — 翻訳(Meta 製)。**モデル重みは CC-BY-NC 4.0(非商用限定)**。本アプリ本体が MIT でもこの制約はモデル側に残る。商用文脈では翻訳 backend をクラウド系へ切り替えること
- [pyttsx3](https://github.com/nateshmbhat/pyttsx3) (MPL-2.0) — Windows SAPI TTS

バックエンド(extras で追加・opt-in):
- [proc-tap](https://pypi.org/project/proc-tap/) (MIT) / [pycaw](https://github.com/AndreMiras/pycaw) (MIT) / [psutil](https://github.com/giampaolo/psutil) (BSD-3) / [SciPy](https://scipy.org/) (BSD-3) — per-process キャプチャ
- [py-webrtcvad-wheels](https://github.com/daanzu/py-webrtcvad-wheels) (MIT、WebRTC 本体 BSD-3) — VAD
- [pyannote.audio](https://github.com/pyannote/pyannote-audio) (MIT) — VAD。[segmentation-3.0](https://huggingface.co/pyannote/segmentation-3.0) モデル (MIT、HF 上で利用同意 + Token が必要)
- [pvcobra](https://picovoice.ai/) (Apache-2.0、サービスは個人非商用 / 商用別ライセンス) — VAD
- [openai-whisper](https://github.com/openai/whisper) (MIT) — ASR(公式実装)
- [Piper](https://github.com/OHF-Voice/piper1-gpl) (**GPL-3.0**) / [onnxruntime](https://onnxruntime.ai/) (MIT) — TTS。本リポジトリには同梱せず、利用者が extras で導入する。[voice モデル](https://huggingface.co/rhasspy/piper-voices)のライセンスは voice ごとに異なる
- [httpx](https://github.com/encode/httpx) (BSD-3) — クラウド系 backend の HTTP クライアント
- [google-cloud-speech / texttospeech](https://github.com/googleapis/google-cloud-python) (Apache-2.0) / [deepgram-sdk](https://github.com/deepgram/deepgram-python-sdk) (MIT) — クラウド SDK
- [huggingface_hub](https://github.com/huggingface/huggingface_hub) (Apache-2.0) — モデル / voice の取得

クラウドサービス(利用時は各規約に従う。アプリは選択時に規約 URL を提示する):
[OpenAI](https://openai.com/policies/terms-of-use) /
[Google Cloud](https://cloud.google.com/terms) /
[Deepgram](https://deepgram.com/terms-of-service) /
[DeepL](https://www.deepl.com/pro-license) /
[Anthropic](https://www.anthropic.com/legal/aup) /
[ElevenLabs](https://elevenlabs.io/terms-of-use) /
[Picovoice](https://picovoice.ai/pricing/) /
[HuggingFace](https://huggingface.co/terms-of-service)



免責と使用許諾：
使用はあなたの責任の元で判断する必要がある。如何なる不利益を被った場合も私はそれらを補償しない。
これらは、完全に無料で公開されたソフトウェアであり、使用・改変を行うことに制限はない。（使用したライブラリのライセンス規約は守って頂く。）
私は、あなたが他者への不当な被害を与えないことを願うだけだ。
これを使って怒りや憎悪に流されないでほしい。私も貴方もAIも自分が思っているほど完ぺきではないのだ。
（感情にのまれる前にアプリを閉じろ）

警告:
このレポジトリは私だけが変更を行う。
クリティカルな問題以外でのPR・Issueには何も対応する気はない。
コードは公開している。好きにフォークして持って行けば良い。（どうせ一週間もかからず君は作るだろう。）

ここまで読んでくれてありがとう。
奇特な貴方には珈琲をおごる権利をそのうち差し上げよう。

気が向いたら（スターが何個か着いたら？）載せる
