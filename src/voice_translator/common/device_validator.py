"""DeviceValidator: 入出力デバイスのバリデーション。

役割: 起動時に「入力デバイス ≠ 出力デバイス」を保証する。
同一だと TTS 出力をループバックで再キャプチャしてフィードバックループが発生するため、
違反時は FatalError を投げて起動を拒否する。
"""

from __future__ import annotations

from voice_translator.common.errors import FatalError


class DeviceValidator:
    """入力と出力のデバイス分離をチェックするユーティリティ。

    役割: ID 文字列の単純比較。識別子の正規化は呼び出し側責務。
    """

    @staticmethod
    def validate(input_device_id: str | None, output_device_id: str | None) -> None:
        """入力と出力の device_id を比較し、同一なら FatalError。

        - どちらかが None/空文字なら FatalError(未選択を拒否)。
        - 比較は文字列の完全一致。
        """
        if not input_device_id:
            raise FatalError("入力デバイスが選択されていません")
        if not output_device_id:
            raise FatalError("出力デバイスが選択されていません")
        if input_device_id == output_device_id:
            raise FatalError(
                "入力デバイスと出力デバイスに同じものは指定できません(フィードバック防止)"
            )
