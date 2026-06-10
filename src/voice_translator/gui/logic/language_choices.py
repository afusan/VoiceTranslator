"""language_choices: 言語プルダウンの候補・選択・fallback を決める純関数。

役割: backend の対応言語と現在の設定値から「プルダウン候補」「選択すべきコード」
「fallback が起きたか」を計算して返す。通知バナーの文言整形もここで行う。
ConfigStore への書き込み・banner 表示・dropdown 操作は View 側の責務。

移行元(P1 / refactor-ui-3move): settings_panel.py の
`_refresh_input_language_choices` / `_refresh_target_language_choices` /
`_check_tts_output_lang_compatibility` の判断部と `_notify_*` の文言部。
候補の順序・fallback 規則・メッセージ文言は移行元と一字一句同一に保つこと。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from voice_translator.common.languages import format_language


@dataclass(frozen=True)
class LanguageSelection:
    """言語プルダウン 1 つ分の計算結果。

    codes は順序確定済みの候補(表示変換前のコード)。fallback_from は
    現在値が候補に無く自動変更が起きたときの元コード(起きなければ None)。
    """

    codes: list[str]
    selected: str
    fallback_from: str | None


def compute_src_selection(
    supported: Sequence[str],
    *,
    supports_auto: bool,
    current: str,
    fallback_pool: Sequence[str],
) -> LanguageSelection:
    """ASR の入力言語(src)プルダウンを計算する。

    - supported が空なら fallback_pool を候補にする
    - 重複除去 + ソート(UI 表示の安定性)。auto 対応 backend なら先頭に "auto"
    - current が候補に含まれればそのまま。無ければ "auto" 優先 → 先頭、で fallback
    """
    codes = list(supported) if supported else list(fallback_pool)
    codes = sorted(set(codes))
    if supports_auto:
        codes = ["auto"] + codes

    if current in codes:
        return LanguageSelection(codes=codes, selected=current, fallback_from=None)

    new_code = "auto" if "auto" in codes else codes[0]
    return LanguageSelection(codes=codes, selected=new_code, fallback_from=current)


def compute_tgt_selection(
    supported: Sequence[str],
    *,
    current: str,
    fallback_pool: Sequence[str],
) -> LanguageSelection:
    """Translator の出力言語(tgt)プルダウンを計算する。

    - supported が空なら fallback_pool を候補にする
    - "auto" は除外(出力言語に「自動」は意味を持たない)+ 重複除去 + ソート
    - current が候補に無ければ ja > en > 先頭 の順で fallback
      (本アプリは日本語主用途のため ja 優先)
    """
    codes = list(supported) if supported else list(fallback_pool)
    codes = sorted(set(c for c in codes if c != "auto"))

    if current in codes:
        return LanguageSelection(codes=codes, selected=current, fallback_from=None)

    if "ja" in codes:
        new_code = "ja"
    elif "en" in codes:
        new_code = "en"
    else:
        new_code = codes[0]
    return LanguageSelection(codes=codes, selected=new_code, fallback_from=current)


def tts_warning_needed(
    *,
    tts_backend: str,
    supported: Sequence[str],
    current_tgt: str,
    none_internal: str = "none",
) -> bool:
    """TTS が現在の出力言語を読み上げられないとき True(警告を出すべき)。

    - backend が空 / "none"(text_only)→ False
    - supported が空(対応言語不明)→ False(誤検知より沈黙)
    - current_tgt が非空かつ supported に含まれる → False
    - それ以外 → True
    """
    if not tts_backend or tts_backend == none_internal:
        return False
    if not supported:
        return False
    if current_tgt and current_tgt in supported:
        return False
    return True


# ============================================================
# 通知バナーの文言(移行元と一字一句同一に保つ)
# ============================================================
def format_src_fallback_message(old_code: str, new_code: str, backend_name: str) -> str:
    """入力言語の自動 fallback を伝えるバナー文言。"""
    return (
        f"入力言語を {format_language(old_code)} から {format_language(new_code)} に変更しました"
        f"({backend_name} が {old_code} に対応していないため)"
    )


def format_tgt_fallback_message(old_code: str, new_code: str, backend_name: str) -> str:
    """出力言語の自動 fallback を伝えるバナー文言。"""
    return (
        f"出力言語を {format_language(old_code)} から {format_language(new_code)} に変更しました"
        f"({backend_name} が {old_code} に対応していないため)"
    )


def format_tts_warning_message(tgt_code: str, backend_name: str) -> str:
    """TTS 非対応言語の警告バナー文言。"""
    return (
        f"TTS バックエンド {backend_name} は読み上げ言語 "
        f"{format_language(tgt_code)} に対応していません"
        "(Translator 出力言語を変えるか、別の TTS バックエンドに切り替えてください)"
    )
