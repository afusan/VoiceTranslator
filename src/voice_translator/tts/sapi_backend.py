"""SapiTtsBackend: Windows SAPI(pyttsx3 経由)による音声合成。

役割: 翻訳テキストを SAPI で WAV に保存 → 読み込んで PCM 化し
(pcm, samplerate) を返す。pyttsx3 は通常はデフォルトデバイスへ直接再生するが、
ここでは出力デバイス指定をサポートするため WAV 経由の迂回方式を取る。
"""

from __future__ import annotations

import os
import tempfile
import time
import wave
from typing import Any

import numpy as np

from voice_translator.common.errors import FatalError, SkipError
from voice_translator.common.types import BackendCapabilities, ModelStatus

from .backend import TtsBackend


class SapiTtsBackend(TtsBackend):
    """pyttsx3 + SAPI による合成。

    役割: synthesize() のたびにエンジンを初期化 → 一時 WAV へ書き出し →
    読み込んで (pcm, samplerate) を返す。
    エンジンを毎回作り直すのは pyttsx3 の既知の問題(save_to_file の状態が残る)対策。
    """

    def __init__(
        self,
        *,
        rate: int = 180,
        voice_lang_hint: str = "ja",
        flush_delay_sec: float = 0.1,
    ) -> None:
        super().__init__()  # BackendBase: status=INIT
        # SAPI は OS 同梱、DL なし。直接 LOADING → LOADED で十分。
        self._set_status(ModelStatus.LOADING)
        # init はテストのために遅延もしない(失敗を即時 FATAL にしたい)
        try:
            import pyttsx3  # type: ignore  # noqa: F401
        except Exception as e:  # noqa: BLE001
            self.record_error(e, context="pyttsx3 import")
            raise FatalError(f"pyttsx3 のロードに失敗: {e}", cause=e) from e
        self._rate = rate
        self._voice_lang_hint = voice_lang_hint
        # 暫定対処: pyttsx3/SAPI の flush 不整合で WAV 末尾が壊れ、
        # 特定音節が長時間繰り返される現象が低頻度で発生する。
        # `runAndWait()` 直後に短時間 sleep を挟むことで再現頻度が下がる。
        # 詳細は docs/design/pendList.md [2026-05-27] SAPI 音節繰り返し を参照。
        # 将来 TTS バックエンドを差し替えたら本パラメータごと削除する。
        self._flush_delay_sec = max(0.0, float(flush_delay_sec))
        self._set_status(ModelStatus.LOADED)

    # ----------------------------------------------------------
    def synthesize(self, text: str, tgt_lang: str) -> tuple[np.ndarray, int]:
        """text を音声合成し、(pcm, samplerate) を返す。"""
        text = (text or "").strip()
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
                self._try_set_voice_for_lang(engine, tgt_lang or self._voice_lang_hint)
                engine.save_to_file(text, wav_path)
                engine.runAndWait()
            finally:
                try:
                    engine.stop()
                except Exception:  # noqa: BLE001
                    pass

            # 暫定対処(pendList [2026-05-27] SAPI 音節繰り返し):
            # runAndWait 直後に少し待って WAV のフラッシュを確実にする。
            if self._flush_delay_sec > 0:
                time.sleep(self._flush_delay_sec)

            pcm, samplerate = _read_wav_as_float32(wav_path)
        except Exception as e:  # noqa: BLE001
            raise FatalError(f"SAPI/TTS 合成失敗: {e}", cause=e) from e
        finally:
            self._remove_quietly(wav_path)

        if pcm.size == 0:
            raise SkipError("合成された音声が空です")

        return pcm, samplerate

    # ----------------------------------------------------------
    @classmethod
    def supported_output_languages(cls) -> list[str]:
        """Windows SAPI が読み上げ可能な言語(保守的な宣言)。

        Windows 10/11 標準では日本語(Haruka 等)/英語(Zira/David 等)の voice が
        プリインストールされているため、この 2 つを宣言する。

        他言語 voice を追加インストールしている環境でも「対応外」と表示される
        ことになるが、SAPI は voice 列挙時に言語コードを安定して取れないケースが
        多く、動的検出は信頼性が低い(`_try_set_voice_for_lang` のヒューリスティック
        と同じ理由)。明示宣言ベースに割り切る。
        """
        return ["ja", "en"]

    # ----------------------------------------------------------
    def capabilities(self) -> BackendCapabilities:
        return BackendCapabilities(
            supported_languages=(),  # SAPI 側にインストールされた声に依存
            is_cloud=False,
            requires_credentials=False,
            notes=(
                "Windows SAPI(pyttsx3)。Mac/Linux では別TTS推奨。WAV経由でPCM取得。"
                f" flush_delay_sec={self._flush_delay_sec} で暫定 flush 待機中。"
            ),
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
