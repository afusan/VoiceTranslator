# pyannote.audio 4.x へ移行して実モデルロードを通すまで (2026-05-30)

## 結論(先に)

`vad-extra` の **pyannote.audio を 3.x → 4.x に上げる**ことで、3.x 系で連発していた
複数の場当たり対応(monkey-patch / shim / safe_globals 等)を **すべて消去**して、
公式 API だけで実モデルが読めるようになった。

```python
from pyannote.audio import Model
from pyannote.audio.pipelines import VoiceActivityDetection

segmentation = Model.from_pretrained("pyannote/segmentation-3.0", token=hf_token)
pipeline = VoiceActivityDetection(segmentation=segmentation)
pipeline.instantiate({"min_duration_on": 0.2, "min_duration_off": 0.5})
```

→ これだけで動く。3.x 時代の `use_auth_token` / weights_only / speechbrain 経路は
全部不要。

## 経緯

### 3.x 系で発覚した壁の話
別ブランチ `feature/vad-picks-phase-f1` で 3.x ベースで実装した際、4 段の壁に遭遇:

1. `use_auth_token` が huggingface_hub 新版で削除済み → TypeError
2. `pyannote/voice-activity-detection` pipeline が 2 段 gated UX
3. torch 2.6 `weights_only=True` 既定で checkpoint が unpickle 不可
4. speechbrain LazyModule(k2_fsa)が `inspect.getmodule` 経由で発火 → ImportError

これらをそれぞれ monkey-patch / shim / safe_globals / inspect 差し替えで凌いだが、
**全部 3.x 系が現代の torch / hf_hub に追従できていないことに起因**していた。
3.x 系は 3.4.0(2025-09)が最終で **実質メンテ終了**。

### 4.x への移行を選択
ドライバ更新(525→610)で cu126 が解禁になったタイミングで、本ブランチ
`feature/vad-picks-pyannote-4x` を `cb1ddb4`(壁対応前のクリーン状態)から切り、
依存を全面的に上げ直し。

### 移行で起きた 1 件の追加発見
`Pipeline.from_pretrained("pyannote/voice-activity-detection")` を pyannote 4.x で
呼ぶと、

```
ValueError: Revisions must be passed with `revision` keyword argument.
```

で落ちる。これは pyannote 4.x が `model@revision` 構文を廃止したため、HF 上の
pipeline config(中で `pyannote/segmentation@2022.07` を参照している)を読み込んだ
時点でエラーになる。HF の config がまだ 4.x に追従していない状態。

これは **band-aid で隠せる類の問題ではない**(我々のコードではなく HF 上の config が
古い)。代わりに pyannote 4.x の README で推奨されている **手動構築パターン**
(`Model.from_pretrained` → `VoiceActivityDetection(segmentation=model)` →
`pipeline.instantiate({...})`)に切り替えて解決。これは pipeline 構造を「諦めた」
わけではなく、4.x で正規ルートとされている組み立て方。

## 依存バージョン

`pyproject.toml` で揃えた:

| パッケージ | 旧 | 新 | 理由 |
|---|---|---|---|
| `torch` | ≥2.2 | **≥2.8** | pyannote.audio 4.x 要求 |
| `torchaudio` | ≥2.2 | **≥2.8** | 同上 |
| PyTorch CUDA index | cu124 | **cu126** | torchaudio 2.8 が cu124 にないため |
| `pyannote.audio` | ≥3.1 | **≥4.0** | 本件の主役 |
| Python | ≥3.11 | そのまま | pyannote 4.x の ≥3.10 をクリア |
| `torchcodec` | なし | (≥0.13 が transitive で入る) | pyannote 4.x の音声 I/O |
| `huggingface_hub` | (制約なし) | (≥0.28 が transitive で入る) | pyannote 4.x の token 引数 |

副産物として:
- `speechbrain` が依存から **消えた**(3.x 系の壁 4 が消滅)
- `transformers` が 4.x → 5.x にメジャー更新
- `pyannote-core` 5 → 6 / `pyannote-pipeline` 3 → 4

NVIDIA ドライバ要件は cu126 = **555.85+**。本件では ドライバ 610.47 で確認済み。

## 起動時の warning(無視可)

実ロード時に 3 つの warning が出るが、いずれも本処理に影響しない:

1. `torchcodec libtorchcodec_core4.dll loading failed` — FFmpeg 未導入。我々は
   decoded tensor を pyannote に直接渡すのでこの経路は通らない。FFmpeg を入れる
   必要は無い。
2. `huggingface_hub symlinks not supported` — Windows 開発者モード OFF だと
   HF cache が増えるだけ。disable は `HF_HUB_DISABLE_SYMLINKS_WARNING=1` で可。
3. `TensorFloat-32 disabled (pyannote)` — pyannote 公式の再現性関連 warning。

## テスト

### small(モック)
`tests/test_pyannote_vad_backend.py` — pyannote.audio / pyannote.audio.pipelines を
モックし、`Model.from_pretrained` の引数 / `VoiceActivityDetection` への segmentation
受け渡し / `instantiate` 呼び出しを検証。12 件 pass。

### large(実モデル)
`tests/test_pyannote_vad_large.py` — `@pytest.mark.large`。`local.secrets` に
`pyannote.hf_token` があれば実モデルを DL してロード + 動作確認:
- LOADED 状態到達
- 3 秒無音 → segment 0 件
- 3 秒サイン波 → 1 件以上の VadSegment
- reset() でバッファクリア

4 件 pass。HF token / 利用同意未済の場合は skip。

## 補足: 元ブランチの場当たり対応の扱い

`feature/vad-picks-phase-f1` ブランチは指示通り **保存**(参考用)。
そのブランチで採った 4 つの場当たりは:

| 場当たり対応 | 4.x 移行後 |
|---|---|
| `use_auth_token` → `token` 翻訳 shim | 不要(pyannote 4.x が `token=` を受ける) |
| Pipeline → Model+VAD 構築の切替 | **引き続き必要**(4.x でも HF config が古いので Pipeline 直読みは×) |
| `torch.load(weights_only=False)` 強制 | 不要(4.x の checkpoint 形式が新 weights_only 既定に追従) |
| `inspect.getmodule` の ImportError 握り | 不要(4.x が speechbrain 依存を捨てた) |

つまり「Model+VAD 構築」だけは band-aid ではなく **4.x の正規パターン**で、
残り 3 つの場当たりは 4.x が解決した。
