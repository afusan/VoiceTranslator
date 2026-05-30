# 補足: 本ブランチの設計は **採用されませんでした** (2026-05-30)

`feature/vad-picks-phase-f1` ブランチで進めた pyannote.audio 3.x ベースの実装は、
torch 2.6 の `weights_only` 既定変更や `huggingface_hub` の `use_auth_token` 廃止に
追従できず、4 つの場当たり対応(monkey-patch / shim / safe_globals / inspect 差し替え)を
重ねる必要があった。

その後 NVIDIA ドライバ更新で cu126 が解禁となり、**pyannote.audio 4.x へ移行**
することで場当たり対応をすべて消去できた。最終的にユーザに届いた実装は
`feature/vad-picks-pyannote-4x` ブランチからの merge による。

経緯と判断は下記参照:
- `docs/troubleAndShooting/2026-05-30_pyannote_audio_4x_migration.md`
- 旧ブランチ `feature/vad-picks-phase-f1` は学習履歴として保存(削除していない)

本フォルダの `Plan.md` / `testPlan.md` は当時の計画書として残置。実装は 4.x 移行で
ほぼ作り直しになった点に注意。
