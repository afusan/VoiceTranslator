# feature/tts-lang-support テスト項目

## small テスト(モック中心、< 1秒/件)

### `tests/test_app_controller.py::TestTtsSupportedOutputLanguages`
TTS の対応読み上げ言語の問い合わせ口。

- `test_returns_languages_from_registered_backend_class` — `backend_cls` 登録済みなら
  classmethod の返り値が出る
- `test_unregistered_returns_empty` — 未登録 backend は空リスト
- `test_backend_class_not_provided_returns_empty` — `backend_cls` 引数なしで登録した
  backend は空リスト(populated_registry の sapi が該当)
- `test_exception_returns_empty` — `supported_output_languages` が例外を吐いても
  AppController は空リストを返す(防御)

### `tests/test_settings_panel_lang.py::TestCheckTtsOutputLangCompatibility`
`_check_tts_output_lang_compatibility` の振る舞い検証(UI 警告連動)。

- `test_warns_when_tts_does_not_support_current_tgt` — TTS が現在の tgt に非対応
  なら警告バナーが 1 回出る
- `test_no_warn_when_tts_supports_current_tgt` — 対応していれば警告は出ない
- `test_no_warn_when_supported_list_empty` — 空リスト(未知)backend は黙る
  (誤検知より沈黙)
- `test_no_warn_when_notify_fallback_false` — 起動時の初期化用(notify_fallback=
  False)は対応外でもバナーを出さない
- `test_no_warn_when_no_tts_backend` — TTS backend 未選択時は何もしない
- `test_falls_back_to_show_message_when_banner_missing` — banner=None でも
  例外にならず `_show_message` に落ちる

### `tests/test_sapi_tts.py::TestSupportedOutputLanguages`
SAPI の対応言語宣言。

- `test_returns_japanese_and_english` — `["ja", "en"]` を返す
- `test_classmethod_callable_without_instance` — pyttsx3 が無くてもクラスメソッド
  だけは呼べる(UI 設定ダイアログ用)

## 既存テストの追従

### `tests/test_pipeline.py` / `tests/test_pipeline_e2e.py`
`FakeTts` / `SilentTts` が `TtsBackend` の新 abstract method を実装する追加修正。
既存テストの挙動には影響なし(言語チェックは pipeline では行わない)。

## 手動確認(GUI)

`feature/tts-picks` で複数 backend が揃ってから verify.md に統合するが、
本ブランチ単独でも以下は SAPI のみで確認可能:

1. アプリ起動 → 出力言語(tgt)を `fr`(SAPI 非対応想定)に切替
   → 警告バナー「TTS バックエンド sapi は読み上げ言語 fr (French) に対応していません」
2. tgt を `ja` に戻すと警告が消える
3. tgt を `en` でも警告が出ない
4. 起動直後(notify_fallback=False)は対応外設定でも警告が出ない

## 対象外
- TTS 互換チェックで実際に `synthesize()` を試す動的検証(これは backend 内部の
  voice fallback と区別がつきにくく、誤検知になる)
- TTS 切替時に Translator 出力言語を自動変更する機能(因果関係が遠いため警告のみ)
