"""messages.py の単体テスト(PipelineMessage と各 payload)。"""

from __future__ import annotations

import numpy as np

from voice_translator.common.messages import (
    PipelineMessage,
    RawPayload,
    SynthesizedPayload,
    TranscribedPayload,
    TranslatedPayload,
)


class TestRawPayload:
    def test_fields(self) -> None:
        pcm = np.zeros(16000, dtype=np.float32)
        p = RawPayload(pcm=pcm, src_lang_hint="en")
        assert p.pcm is pcm
        assert p.src_lang_hint == "en"

    def test_default_lang_hint_is_auto(self) -> None:
        p = RawPayload(pcm=np.zeros(1, dtype=np.float32))
        assert p.src_lang_hint == "auto"

    def test_frozen(self) -> None:
        p = RawPayload(pcm=np.zeros(1, dtype=np.float32))
        try:
            p.src_lang_hint = "ja"  # type: ignore[misc]
        except Exception:
            return
        raise AssertionError("frozen dataclass should reject field assignment")


class TestTranscribedPayload:
    def test_fields(self) -> None:
        p = TranscribedPayload(src_text="hello", src_lang="en")
        assert p.src_text == "hello"
        assert p.src_lang == "en"


class TestTranslatedPayload:
    def test_fields(self) -> None:
        p = TranslatedPayload(tgt_text="こんにちは", tgt_lang="ja")
        assert p.tgt_text == "こんにちは"
        assert p.tgt_lang == "ja"


class TestSynthesizedPayload:
    def test_fields(self) -> None:
        pcm = np.zeros(100, dtype=np.float32)
        p = SynthesizedPayload(tts_pcm=pcm, tts_samplerate=22050)
        assert p.tts_pcm is pcm
        assert p.tts_samplerate == 22050


class TestPipelineMessage:
    def test_envelope_holds_seq_id_and_payload(self) -> None:
        payload = TranscribedPayload(src_text="hi", src_lang="en")
        msg = PipelineMessage(seq_id=42, payload=payload)
        assert msg.seq_id == 42
        assert msg.payload is payload

    def test_envelope_works_with_each_payload_kind(self) -> None:
        # Raw
        m1 = PipelineMessage(
            seq_id=1, payload=RawPayload(pcm=np.zeros(1, dtype=np.float32))
        )
        assert isinstance(m1.payload, RawPayload)

        # Transcribed
        m2 = PipelineMessage(seq_id=2, payload=TranscribedPayload("hi", "en"))
        assert isinstance(m2.payload, TranscribedPayload)

        # Translated
        m3 = PipelineMessage(seq_id=3, payload=TranslatedPayload("はい", "ja"))
        assert isinstance(m3.payload, TranslatedPayload)

        # Synthesized
        m4 = PipelineMessage(
            seq_id=4,
            payload=SynthesizedPayload(tts_pcm=np.zeros(1, dtype=np.float32), tts_samplerate=16000),
        )
        assert isinstance(m4.payload, SynthesizedPayload)

    def test_envelope_is_frozen(self) -> None:
        msg = PipelineMessage(seq_id=1, payload=TranscribedPayload("a", "en"))
        try:
            msg.seq_id = 99  # type: ignore[misc]
        except Exception:
            return
        raise AssertionError("frozen dataclass should reject field assignment")
