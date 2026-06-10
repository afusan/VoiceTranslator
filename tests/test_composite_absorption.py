"""複合 backend(ASR+Translator)選択時の AppController 吸収連動テスト。

- 吸収ロールがロード対象 / 編成対象から外れる
- 翻訳先言語の問い合わせが複合 backend 側へ切り替わる
- 単体 ASR へ戻すと従来どおり Translator が対象に戻る
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from voice_translator.asr.backend import AsrTranslatorBackend
from voice_translator.common.app_controller import AppController
from voice_translator.common.backend_registry import BackendRegistry
from voice_translator.common.config_store import ConfigStore
from voice_translator.common.types import LayerKind
from voice_translator.translator.backend import TranslatorBackend


class StubComposite(AsrTranslatorBackend):
    """ASR+翻訳複合のスタブ(ロードカウント付き)。"""

    created = 0

    def __init__(self) -> None:
        super().__init__()
        type(self).created += 1

    def transcribe_translate(
        self, pcm: Any, src_lang_hint: str = "auto", tgt_lang: str = "en"
    ) -> tuple[str, str, str, str]:
        return "", "ja", "hi", "en"

    @classmethod
    def supported_input_languages(cls) -> list[str]:
        return ["ja", "en"]

    @classmethod
    def supported_target_languages(cls) -> list[str]:
        return ["en"]


class StubTranslator(TranslatorBackend):
    """単体 Translator のスタブ(ロードカウント付き)。"""

    created = 0

    def __init__(self) -> None:
        super().__init__()
        type(self).created += 1

    def translate(self, src_text: str, src_lang: str, tgt_lang: str) -> str:
        return src_text

    @classmethod
    def supported_target_languages(cls) -> list[str]:
        return ["ja", "fr"]


@pytest.fixture()
def controller(tmp_path):
    StubComposite.created = 0
    StubTranslator.created = 0

    cfg = ConfigStore(tmp_path / "cfg.yaml")
    cfg.set("backends", "capture", "cap")
    cfg.set("backends", "vad", "vad")
    cfg.set("backends", "asr", "fwt")
    cfg.set("backends", "translator", "tr")
    cfg.set("backends", "tts", "tts")
    cfg.set("backends", "output", "out")

    reg = BackendRegistry()
    reg.register(LayerKind.CAPTURE, "cap", MagicMock)
    reg.register(LayerKind.VAD, "vad", MagicMock)
    reg.register(LayerKind.ASR, "fwt", StubComposite, backend_cls=StubComposite)
    reg.register(LayerKind.ASR, "plain", MagicMock)
    reg.register(LayerKind.TRANSLATOR, "tr", StubTranslator, backend_cls=StubTranslator)
    reg.register(LayerKind.TTS, "tts", MagicMock)
    reg.register(LayerKind.OUTPUT, "out", MagicMock)

    return AppController(registry=reg, config=cfg)


class TestAbsorbedRoles:
    def test_composite_absorbs_translator(self, controller) -> None:
        assert controller.get_absorbed_roles() == {
            LayerKind.TRANSLATOR: LayerKind.ASR
        }

    def test_single_asr_absorbs_nothing(self, controller) -> None:
        controller.set_setting("backends", "asr", "plain")
        assert controller.get_absorbed_roles() == {}

    def test_active_layers_exclude_absorbed(self, controller) -> None:
        active = controller._active_layers()
        assert LayerKind.TRANSLATOR not in active
        assert LayerKind.ASR in active

    def test_active_layers_restore_on_single_asr(self, controller) -> None:
        controller.set_setting("backends", "asr", "plain")
        assert LayerKind.TRANSLATOR in controller._active_layers()


class TestAbsorbedLoading:
    def test_load_models_skips_absorbed_translator(self, controller) -> None:
        controller.load_models()
        assert StubComposite.created == 1
        assert StubTranslator.created == 0  # 吸収中はロードされない
        assert LayerKind.TRANSLATOR not in controller._backends


class TestEffectiveTargetLanguages:
    def test_composite_provides_target_languages(self, controller) -> None:
        assert controller.get_target_language_provider() == (LayerKind.ASR, "fwt")
        assert controller.get_effective_target_languages() == ["en"]

    def test_translator_provides_when_not_absorbed(self, controller) -> None:
        controller.set_setting("backends", "asr", "plain")
        assert controller.get_target_language_provider() == (
            LayerKind.TRANSLATOR, "tr",
        )
        assert controller.get_effective_target_languages() == ["ja", "fr"]
