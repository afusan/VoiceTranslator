"""レイヤ抽象基底のパイプライン編成申告(covers/consumes/produces)の既定値テスト。

編成(build_pipeline_plan)はこの申告を前提に組まれるため、既定値の対応表を
ここで固定する(変えたら編成が変わる = ふるまい変更としてテスト差分に出す)。
"""

from __future__ import annotations

import numpy as np
import pytest

from voice_translator.asr.backend import AsrBackend
from voice_translator.capture.backend import AudioCaptureBackend
from voice_translator.common.messages import (
    PayloadKind,
    RawPayload,
    SynthesizedPayload,
    TranscribedPayload,
    TranslatedPayload,
    payload_kind_of,
)
from voice_translator.common.types import LayerKind
from voice_translator.output.backend import AudioOutputBackend
from voice_translator.translator.backend import TranslatorBackend
from voice_translator.tts.backend import TtsBackend
from voice_translator.vad.backend import VadBackend


class TestDefaultRoleDeclarations:
    """各レイヤ ABC の既定申告が Plan §2 の表どおりであること。"""

    @pytest.mark.parametrize(
        ("abc", "covers", "consumes", "produces"),
        [
            (AudioCaptureBackend, (LayerKind.CAPTURE,), PayloadKind.NONE, PayloadKind.NONE),
            (VadBackend, (LayerKind.VAD,), PayloadKind.NONE, PayloadKind.RAW),
            (AsrBackend, (LayerKind.ASR,), PayloadKind.RAW, PayloadKind.TRANSCRIBED),
            (TranslatorBackend, (LayerKind.TRANSLATOR,), PayloadKind.TRANSCRIBED, PayloadKind.TRANSLATED),
            (TtsBackend, (LayerKind.TTS,), PayloadKind.TRANSLATED, PayloadKind.SYNTHESIZED),
            (AudioOutputBackend, (LayerKind.OUTPUT,), PayloadKind.SYNTHESIZED, PayloadKind.NONE),
        ],
    )
    def test_default_declaration(self, abc, covers, consumes, produces):
        assert abc.covers_roles() == covers
        assert abc.consumes_payload() == consumes
        assert abc.produces_payload() == produces


class TestPayloadKindOf:
    """payload インスタンス → PayloadKind の対応が全 payload 型をカバーすること。"""

    @pytest.mark.parametrize(
        ("payload", "kind"),
        [
            (RawPayload(pcm=np.zeros(4, dtype=np.float32)), PayloadKind.RAW),
            (TranscribedPayload(src_text="hi", src_lang="en"), PayloadKind.TRANSCRIBED),
            (TranslatedPayload(tgt_text="こんにちは", tgt_lang="ja"), PayloadKind.TRANSLATED),
            (SynthesizedPayload(tts_pcm=np.zeros(4, dtype=np.float32), tts_samplerate=16000), PayloadKind.SYNTHESIZED),
        ],
    )
    def test_known_payloads(self, payload, kind):
        assert payload_kind_of(payload) == kind

    def test_unknown_type_raises(self):
        with pytest.raises(KeyError):
            payload_kind_of(object())
