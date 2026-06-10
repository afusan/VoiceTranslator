"""build_pipeline_plan(編成表の純関数)のテスト。

標準構成の編成・text_only 縮退・複合 backend の吸収・起動拒否(PlanError)を固定する。
"""

from __future__ import annotations

import pytest

from voice_translator.asr.backend import AsrBackend
from voice_translator.capture.backend import AudioCaptureBackend
from voice_translator.common.messages import PayloadKind
from voice_translator.common.pipeline_plan import (
    DEFAULT_DECLARATIONS,
    PlanError,
    RoleDeclaration,
    build_pipeline_plan,
    declaration_of,
    select_adapter,
)
from voice_translator.common.types import LayerKind
from voice_translator.output.backend import AudioOutputBackend
from voice_translator.translator.backend import TranslatorBackend
from voice_translator.tts.backend import TtsBackend
from voice_translator.vad.backend import VadBackend


def _standard() -> dict[LayerKind, RoleDeclaration]:
    """全レイヤ単体 backend(既定申告)の declarations。"""
    return dict(DEFAULT_DECLARATIONS)


ASR_TRANSLATOR_COMPOSITE = RoleDeclaration(
    covers=(LayerKind.ASR, LayerKind.TRANSLATOR),
    consumes=PayloadKind.RAW,
    produces=PayloadKind.TRANSLATED,
)


class TestStandardPlan:
    """単体 backend×6 の従来構成 → 5 ステージ / 4 キュー相当の編成。"""

    def test_five_stages_with_fused_input(self):
        plan = build_pipeline_plan(_standard())
        assert len(plan.stages) == 5
        assert plan.stages[0].roles == (LayerKind.CAPTURE, LayerKind.VAD)
        assert plan.stages[0].is_input
        assert [s.label for s in plan.stages] == [
            "Input", "ASR", "Translator", "TTS", "Output",
        ]

    def test_adjacent_payload_chain(self):
        plan = build_pipeline_plan(_standard())
        kinds = [(s.consumes, s.produces) for s in plan.stages]
        assert kinds == [
            (PayloadKind.NONE, PayloadKind.RAW),
            (PayloadKind.RAW, PayloadKind.TRANSCRIBED),
            (PayloadKind.TRANSCRIBED, PayloadKind.TRANSLATED),
            (PayloadKind.TRANSLATED, PayloadKind.SYNTHESIZED),
            (PayloadKind.SYNTHESIZED, PayloadKind.NONE),
        ]

    def test_plan_views(self):
        plan = build_pipeline_plan(_standard())
        assert plan.active_layers == tuple(LayerKind)
        assert plan.lead_layers == tuple(LayerKind)
        assert plan.absorbed == ()
        assert plan.output_mode == "audio"
        assert plan.has_role(LayerKind.TTS)


class TestTextOnlyPlan:
    """text_only(TTS/Output を編成に載せない)の縮退。"""

    def test_three_stages_ending_with_translated(self):
        plan = build_pipeline_plan(_standard(), text_only=True)
        assert len(plan.stages) == 3
        assert [s.label for s in plan.stages] == ["Input", "ASR", "Translator"]
        assert plan.stages[-1].produces == PayloadKind.TRANSLATED
        assert plan.output_mode == "text_only"
        assert not plan.has_role(LayerKind.TTS)
        assert not plan.has_role(LayerKind.OUTPUT)


class TestCompositePlan:
    """複合 backend(ASR+Translator)による吸収。"""

    def test_translator_absorbed(self):
        decls = _standard()
        decls[LayerKind.ASR] = ASR_TRANSLATOR_COMPOSITE
        plan = build_pipeline_plan(decls)
        assert len(plan.stages) == 4
        assert [s.label for s in plan.stages] == [
            "Input", "ASR+Translator", "TTS", "Output",
        ]
        assert plan.absorbed_map == {LayerKind.TRANSLATOR: LayerKind.ASR}
        # 吸収ロールは active には載るが lead(ロード対象)には載らない
        assert LayerKind.TRANSLATOR in plan.active_layers
        assert LayerKind.TRANSLATOR not in plan.lead_layers

    def test_absorbed_role_declaration_is_ignored(self):
        """Translator レイヤに別 backend が設定されていても編成は成立する(無視)。"""
        decls = _standard()
        decls[LayerKind.ASR] = ASR_TRANSLATOR_COMPOSITE
        # Translator に矛盾した申告が残っていても参照されない
        decls[LayerKind.TRANSLATOR] = RoleDeclaration(
            (LayerKind.TRANSLATOR,), PayloadKind.SYNTHESIZED, PayloadKind.NONE
        )
        plan = build_pipeline_plan(decls)
        assert [s.label for s in plan.stages] == [
            "Input", "ASR+Translator", "TTS", "Output",
        ]

    def test_composite_with_text_only(self):
        decls = _standard()
        decls[LayerKind.ASR] = ASR_TRANSLATOR_COMPOSITE
        plan = build_pipeline_plan(decls, text_only=True)
        assert [s.label for s in plan.stages] == ["Input", "ASR+Translator"]
        assert plan.stages[-1].produces == PayloadKind.TRANSLATED


class TestPlanRejection:
    """組めない編成は PlanError で起動拒否。"""

    def test_missing_declaration(self):
        decls = _standard()
        del decls[LayerKind.ASR]
        with pytest.raises(PlanError, match="asr"):
            build_pipeline_plan(decls)

    def test_payload_mismatch(self):
        decls = _standard()
        # TTS が TRANSCRIBED を要求する(翻訳の産出 TRANSLATED と不一致)
        decls[LayerKind.TTS] = RoleDeclaration(
            (LayerKind.TTS,), PayloadKind.TRANSCRIBED, PayloadKind.SYNTHESIZED
        )
        with pytest.raises(PlanError, match="一致しません"):
            build_pipeline_plan(decls)

    def test_covers_must_start_with_lead(self):
        decls = _standard()
        decls[LayerKind.ASR] = RoleDeclaration(
            (LayerKind.TRANSLATOR, LayerKind.TTS),
            PayloadKind.TRANSCRIBED,
            PayloadKind.SYNTHESIZED,
        )
        with pytest.raises(PlanError, match="先頭"):
            build_pipeline_plan(decls)

    def test_covers_must_be_contiguous(self):
        decls = _standard()
        decls[LayerKind.ASR] = RoleDeclaration(
            (LayerKind.ASR, LayerKind.TTS),  # TRANSLATOR を飛ばす
            PayloadKind.RAW,
            PayloadKind.SYNTHESIZED,
        )
        with pytest.raises(PlanError, match="連続"):
            build_pipeline_plan(decls)

    def test_covers_outside_text_only_chain(self):
        """text_only なのに TTS まで覆う複合は編成対象外ロールを含むとして拒否。"""
        decls = _standard()
        decls[LayerKind.TRANSLATOR] = RoleDeclaration(
            (LayerKind.TRANSLATOR, LayerKind.TTS),
            PayloadKind.TRANSCRIBED,
            PayloadKind.SYNTHESIZED,
        )
        with pytest.raises(PlanError, match="連続"):
            build_pipeline_plan(decls, text_only=True)

    def test_vad_not_producing_rejected(self):
        """VAD 相当が payload を産まないと、後続(ASR)の要求と矛盾して拒否される。"""
        decls = _standard()
        decls[LayerKind.VAD] = RoleDeclaration(
            (LayerKind.VAD,), PayloadKind.NONE, PayloadKind.NONE
        )
        with pytest.raises(PlanError, match="要求"):
            build_pipeline_plan(decls)

    def test_head_never_produces(self):
        """全 unit が何も産まない編成は「先頭区間が発話 payload を産まない」として拒否。"""
        none_decl = {
            layer: RoleDeclaration((layer,), PayloadKind.NONE, PayloadKind.NONE)
            for layer in (
                LayerKind.CAPTURE, LayerKind.VAD, LayerKind.ASR, LayerKind.TRANSLATOR,
            )
        }
        with pytest.raises(PlanError, match="産みません"):
            build_pipeline_plan(none_decl, text_only=True)

    def test_head_unit_requiring_payload(self):
        """Capture が何も産んでいないのに VAD 相当が payload を要求 → 拒否。"""
        decls = _standard()
        decls[LayerKind.VAD] = RoleDeclaration(
            (LayerKind.VAD,), PayloadKind.RAW, PayloadKind.RAW
        )
        with pytest.raises(PlanError, match="要求"):
            build_pipeline_plan(decls)


class TestDefaultDeclarationSync:
    """DEFAULT_DECLARATIONS(fallback 表)がレイヤ ABC の既定申告と同値であること。"""

    @pytest.mark.parametrize(
        ("layer", "abc"),
        [
            (LayerKind.CAPTURE, AudioCaptureBackend),
            (LayerKind.VAD, VadBackend),
            (LayerKind.ASR, AsrBackend),
            (LayerKind.TRANSLATOR, TranslatorBackend),
            (LayerKind.TTS, TtsBackend),
            (LayerKind.OUTPUT, AudioOutputBackend),
        ],
    )
    def test_sync_with_abc(self, layer, abc):
        assert DEFAULT_DECLARATIONS[layer] == declaration_of(abc)


class TestSelectAdapter:
    def test_identity_when_kinds_match(self):
        adapter = select_adapter(PayloadKind.RAW, PayloadKind.RAW)
        sentinel = object()
        assert adapter.adapt(sentinel) is sentinel

    def test_mismatch_raises(self):
        with pytest.raises(PlanError, match="整流役"):
            select_adapter(PayloadKind.RAW, PayloadKind.TRANSLATED)
