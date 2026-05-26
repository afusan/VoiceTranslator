"""SapiTtsBackend: Windows SAPI(pyttsx3 経由)による音声合成。

役割: 翻訳テキストを SAPI で WAV に保存 → 読み込んで PCM 化し
Utterance に格納する。pyttsx3 は通常はデフォルトデバイスへ直接再生するが、
ここでは出力デバイス指定をサポートするため WAV 経由の迂回方式を取る。
"""

from __future__ import annotations

import os
import tempfile
import wave
from typing import Any

import numpy as np

from voice_translator.common.errors import FatalError, SkipError
from voice_translator.common.types import BackendCapabilities
from voice_translator.common.utterance import Utterance

from .backend import TtsBackend


class SapiTtsBackend(TtsBackend):
    """pyttsx3 + SAPI による合成。

    役割: synthesize() のたびにエンジンを初期化 → 一時 WAV へ書き出し →
    読み込んで Utterance.tts_pcm / tts_samplerate を埋める。
    エンジンを毎回作り直すのは pyttsx3 の既知の問題(save_to_file の状態が残る)対策。
    """

    def __init__(self, *, rate: int = 180, voice_lang_hint: str = "ja") -> None:
        # init はテストのために遅延もしない(失敗を即時 FATAL にしたい)
        try:
            import pyttsx3  # type: ignore  # noqa: F401
        except Exception as e:  # noqa: BLE001
            raise FatalError(f"pyttsx3 のロードに失敗: {e}", cause=e) from e
        self._rate = rate
        self._voice_lang_hint = voice_lang_hint

    # ----------------------------------------------------------
    def synthesize(self, utterance: Utterance) -> Utterance:
        """utterance.tgt_text を音声合成し、tts_pcm/tts_samplerate を埋めて返す。"""
        text = (utterance.tgt_text or "").strip()
        if not text:
            raise SkipError("TTS入力テキストが空です")

        try:
            import pyttsx3  # type: ignore
        except Exception as e:  # noqa: BLE001
            raise FatalError(f"pyttsx3 のロードに失敗: {e}", cause=e) from e

        wav_path = self._make_temp_wav_path()
        try:
            engine = pyttsx3.init()
            try:
                engine.setProperty("rate", self._rate)
                self._try_set_voice_for_lang(engine, utterance.tgt_lang or self._voice_lang_hint)
                engine.save_to_file(text, wav_path)
                engine.runAndWait()
            finally:
                try:
                    engine.stop()
                except Exception:  # noqa: BLE001
                    pass

            pcm, samplerate = _read_wav_as_float32(wav_path)
        except Exception as e:  # noqa: BLE001
            raise FatalError(f"SAPI/TTS 合成失敗: {e}", cause=e) from e
        finally:
            self._remove_quietly(wav_path)

        if pcm.size == 0:
            raise SkipError("合成された音声が空です")

        utterance.tts_pcm = pcm
        utterance.tts_samplerate = samplerate
        return utterance

    # ----------------------------------------------------------
    def capabilities(self) -> BackendCapabilities:
        return BackendCapabilities(
            supported_languages=(),  # SAPI 側にインストールされた声に依存
            notes="Windows SAPI(pyttsx3)。Mac/Linux では別TTS推奨。WAV経由でPCM取得。",
        )

    # ---- 内部 ----
    @staticmethod
    def _make_temp_wav_path() -> str:
        """一時 WAV ファイルパスを作成(中身は空のまま返す)。"""
        fd, path = tempfile.mkstemp(suffix=".wav", prefix="vt_sapi_")
        os.close(fd)  # 中身は pyttsx3 が書く
        return path

    @staticmethod
    def _remove_quietly(path: str) -> None:
        """ファイル削除。失敗しても無視。"""
        try:
            os.unlink(path)
        except OSError:
            pass

    @staticmethod
    def _try_set_voice_for_lang(engine: Any, lang_iso: str) -> None:
        """ヒント言語に近いボイスを選ぶ。見つからなければ既定のまま。"""
        try:
            voices = engine.getProperty("voices")
        except Exception:  # noqa: BLE001
            return
        # 1) languages 属性で一致を探す
        for v in voices:
            langs = getattr(v, "languages", None) or []
            if any(lang_iso.lower() in str(lang).lower() for lang in langs):
                try:
                    engine.setProperty("voice", v.id)
                    return
                except Exception:  # noqa: BLE001
                    continue
        # 2) 名前ベースの推測(Windows SAPI は languages が空のことが多い)
        ja_hints = ("japanese", "haruka", "ayumi", "ichiro", "sayaka")
        en_hints = ("english", "zira", "david", "mark", "hazel")
        hints = ja_hints if lang_iso.lower().startswith("ja") else en_hints if lang_iso.lower().startswith("en") else ()
        for v in voices:
            name = getattr(v, "name", "") or ""
            if any(h in name.lower() for h in hints):
                try:
                    engine.setProperty("voice", v.id)
                    return
                except Exception:  # noqa: BLE001
                    continue


def _read_wav_as_float32(path: str) -> tuple[np.ndarray, int]:
    """WAV を float32 PCM (1次元 or (N,ch)) として読み込む。"""
    with wave.open(path, "rb") as wf:
        sr = wf.getframerate()
        nch = wf.getnchannels()
        sampwidth = wf.getsampwidth()
        frames = wf.readframes(wf.getnframes())

    if sampwidth == 2:
        pcm = np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0
    elif sampwidth == 4:
        pcm = np.frombuffer(frames, dtype=np.int32).astype(np.float32) / 2_147_483_648.0
    elif sampwidth == 1:
        pcm = np.frombuffer(frames, dtype=np.uint8).astype(np.float32) / 128.0 - 1.0
    else:
        raise FatalError(f"未対応のWAVサンプル幅: {sampwidth}")

    if nch == 2 and pcm.size % 2 == 0:
        pcm = pcm.reshape(-1, 2)
    return pcm, sr
