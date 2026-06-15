"""多言語対応の統合 small テスト(639-3 正準化後の AND 連携)。

Phase 2 の核心: MMS-TTS と NLLB-200 が**同じ内部正準コード(ISO 639-3)**で対応言語を
申告するため、既存の「翻訳 ∩ TTS」AND(`restrict_to_tts`)がそのまま機能し、低資源言語が
出力言語候補に残る。新しい仕組みを足さずに連携できていることを固定で守る。
"""

from __future__ import annotations

from voice_translator.gui.logic.language_choices import restrict_to_tts
from voice_translator.translator.nllb200_backend import Nllb200TranslatorBackend
from voice_translator.tts.mms_backend import MmsTtsBackend


class TestMmsNllbIntersection:
    def test_both_declare_canonical_iso639_3(self) -> None:
        tts = MmsTtsBackend.supported_output_languages()
        tr = Nllb200TranslatorBackend.supported_target_languages()
        # いずれも正準 639-3(3 文字 or それ以上)。legacy 2 文字が混ざっていない。
        assert all(len(c) >= 3 for c in tts)
        assert all(len(c) >= 3 for c in tr)

    def test_low_resource_languages_survive_and(self) -> None:
        """翻訳 ∩ TTS の積に、低資源言語(スワヒリ/ヨルバ/ハウサ/アムハラ)が残る。"""
        tts = MmsTtsBackend.supported_output_languages()
        tr = Nllb200TranslatorBackend.supported_target_languages()
        inter = restrict_to_tts(tr, tts)
        for code in ("swh", "yor", "hau", "amh"):
            assert code in inter, f"{code} が翻訳∩TTS の積から抜けている"
        # 積は TTS 対応言語の部分集合(TTS が読めない言語は残さない)
        assert set(inter) <= set(tts)

    def test_nllb_still_covers_japanese(self) -> None:
        assert "jpn" in Nllb200TranslatorBackend.supported_target_languages()
