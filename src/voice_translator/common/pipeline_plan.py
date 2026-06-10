"""パイプライン編成表(plan)の構築。

役割: 各 backend の申告(covers_roles / consumes_payload / produces_payload)から、
起動前に一度だけステージ編成(どのステージが存在し、誰が誰の次で、間をどの payload
形式が流れるか)を確定し、矛盾(申告の欠落・連続性違反・型不整合)を起動拒否として
検出する純関数群。走行中の動的ルーティングは行わない — 編成を変える要因は設定だけで、
設定は走行中に変わらないため(設計判断の詳細:
docs/design/refactor-pipeline-composite-backend/Plan.md)。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Protocol

from .errors import FatalError
from .messages import PayloadKind, PipelineMessage
from .types import LayerKind

# パイプラインの全ロール(処理順)。編成はこの順序の部分列として組まれる。
ROLE_CHAIN: tuple[LayerKind, ...] = (
    LayerKind.CAPTURE,
    LayerKind.VAD,
    LayerKind.ASR,
    LayerKind.TRANSLATOR,
    LayerKind.TTS,
    LayerKind.OUTPUT,
)

# エラー通知・ドロップ通知・スレッド名で使うロール表示名(従来文字列を踏襲)
ROLE_LABELS: dict[LayerKind, str] = {
    LayerKind.CAPTURE: "Capture",
    LayerKind.VAD: "VAD",
    LayerKind.ASR: "ASR",
    LayerKind.TRANSLATOR: "Translator",
    LayerKind.TTS: "TTS",
    LayerKind.OUTPUT: "Output",
}

# ステージ間キューの基本名(payload 形式から導出。従来のキュー名を踏襲)
QUEUE_BASENAMES: dict[PayloadKind, str] = {
    PayloadKind.RAW: "captured_queue",
    PayloadKind.TRANSCRIBED: "recognized_queue",
    PayloadKind.TRANSLATED: "translated_queue",
    PayloadKind.SYNTHESIZED: "synthesized_queue",
}


class PlanError(FatalError):
    """編成不能(申告の欠落・連続性違反・payload 型不整合)。起動拒否に使う。"""


@dataclass(frozen=True)
class RoleDeclaration:
    """backend 1 つぶんの編成申告(クラスの申告 classmethod のスナップショット)。"""

    covers: tuple[LayerKind, ...]
    consumes: PayloadKind
    produces: PayloadKind


def declaration_of(backend_or_cls: Any) -> RoleDeclaration:
    """backend クラス/インスタンスの申告 classmethod を `RoleDeclaration` に写し取る。"""
    return RoleDeclaration(
        covers=tuple(backend_or_cls.covers_roles()),
        consumes=backend_or_cls.consumes_payload(),
        produces=backend_or_cls.produces_payload(),
    )


# レイヤ既定の申告(registry に backend_cls が無い登録の fallback)。
# 各レイヤ ABC の既定 classmethod と同値であること(test_pipeline_plan で同期を固定)。
DEFAULT_DECLARATIONS: dict[LayerKind, RoleDeclaration] = {
    LayerKind.CAPTURE: RoleDeclaration(
        (LayerKind.CAPTURE,), PayloadKind.NONE, PayloadKind.NONE
    ),
    LayerKind.VAD: RoleDeclaration(
        (LayerKind.VAD,), PayloadKind.NONE, PayloadKind.RAW
    ),
    LayerKind.ASR: RoleDeclaration(
        (LayerKind.ASR,), PayloadKind.RAW, PayloadKind.TRANSCRIBED
    ),
    LayerKind.TRANSLATOR: RoleDeclaration(
        (LayerKind.TRANSLATOR,), PayloadKind.TRANSCRIBED, PayloadKind.TRANSLATED
    ),
    LayerKind.TTS: RoleDeclaration(
        (LayerKind.TTS,), PayloadKind.TRANSLATED, PayloadKind.SYNTHESIZED
    ),
    LayerKind.OUTPUT: RoleDeclaration(
        (LayerKind.OUTPUT,), PayloadKind.SYNTHESIZED, PayloadKind.NONE
    ),
}


@dataclass(frozen=True)
class StageUnit:
    """ステージ内で 1 backend が担当する区間。`lead` が backend 選択キー。"""

    lead: LayerKind
    roles: tuple[LayerKind, ...]


@dataclass(frozen=True)
class StageSpec:
    """編成表の 1 ステージ(= 1 スレッド)。

    入力ステージ(consumes=NONE)のみ複数 unit を持ちうる(Capture + VAD の融合)。
    それ以外は 1 unit = 1 backend。
    """

    units: tuple[StageUnit, ...]
    consumes: PayloadKind
    produces: PayloadKind

    @property
    def roles(self) -> tuple[LayerKind, ...]:
        """このステージが担う全ロール(処理順)。"""
        return tuple(r for u in self.units for r in u.roles)

    @property
    def lead(self) -> LayerKind:
        """先頭ロール(エラー文脈・スレッド名のキー)。"""
        return self.units[0].lead

    @property
    def is_input(self) -> bool:
        """編成先頭の入力ステージ(キューからではなくデバイスから読む)か。"""
        return self.consumes is PayloadKind.NONE

    @property
    def label(self) -> str:
        """エラー stage 名・スレッド名に使う表示名。入力ステージは従来どおり Input。"""
        if self.is_input:
            return "Input"
        return "+".join(ROLE_LABELS[r] for r in self.roles)


@dataclass(frozen=True)
class PipelinePlan:
    """起動時に確定する編成表。走行中は不変。"""

    stages: tuple[StageSpec, ...]
    absorbed: tuple[tuple[LayerKind, LayerKind], ...]  # (吸収ロール, 吸収先 lead)

    @property
    def active_layers(self) -> tuple[LayerKind, ...]:
        """編成に載る全ロール(処理順)。吸収ロールも含む。"""
        return tuple(r for s in self.stages for r in s.roles)

    @property
    def lead_layers(self) -> tuple[LayerKind, ...]:
        """backend 実体が必要なレイヤ(ロード・認証 gate・ステータスの対象)。"""
        return tuple(u.lead for s in self.stages for u in s.units)

    def has_role(self, role: LayerKind) -> bool:
        return role in self.active_layers

    @property
    def output_mode(self) -> str:
        """Output を含む編成 = audio、含まない = text_only(従来表現との互換)。"""
        return "audio" if self.has_role(LayerKind.OUTPUT) else "text_only"

    @property
    def absorbed_map(self) -> dict[LayerKind, LayerKind]:
        """吸収ロール → 吸収先 lead の dict ビュー。"""
        return dict(self.absorbed)


def build_pipeline_plan(
    declarations: Mapping[LayerKind, RoleDeclaration],
    *,
    text_only: bool = False,
) -> PipelinePlan:
    """申告から編成表を組む(純関数)。組めない場合は `PlanError`(起動拒否)。

    - `declarations` は「lead ロール → そのロールに選択された backend の申告」。
      吸収されるロールのエントリは参照されない(あっても無視 = 設定されていても起動時は無効)。
    - `text_only=True` なら TTS / Output ロールを編成に含めない。
    - 先頭の「発話 payload を産むまで」の区間(Capture〜VAD)は 1 つの入力ステージに融合する
      (VAD より前には発話単位が存在せず、ステージ間キューを置けないため)。
    - 隣接ステージの produces / consumes が一致しない編成は組めない。
    """
    if text_only:
        chain = tuple(
            r for r in ROLE_CHAIN if r not in (LayerKind.TTS, LayerKind.OUTPUT)
        )
    else:
        chain = ROLE_CHAIN

    # 1) ロールを順に backend 申告で覆い、unit 列を作る
    units: list[tuple[StageUnit, RoleDeclaration]] = []
    absorbed: list[tuple[LayerKind, LayerKind]] = []
    i = 0
    while i < len(chain):
        role = chain[i]
        decl = declarations.get(role)
        if decl is None:
            raise PlanError(
                f"編成に必要なロール {role.value} の backend 申告がありません"
            )
        covers = tuple(decl.covers)
        if not covers or covers[0] != role:
            raise PlanError(
                f"ロール {role.value} の backend は covers_roles の先頭が"
                f" {role.value} である必要があります: {tuple(c.value for c in covers)}"
            )
        if chain[i : i + len(covers)] != covers:
            raise PlanError(
                f"ロール {role.value} の backend の covers_roles が編成順で連続していないか、"
                f"現在の編成対象外のロールを含みます: {tuple(c.value for c in covers)}"
            )
        units.append((StageUnit(lead=role, roles=covers), decl))
        for extra in covers[1:]:
            absorbed.append((extra, role))
        i += len(covers)

    # 2) 先頭融合: 最初に発話 payload(NONE 以外)を産む unit までを入力ステージに束ねる
    stages: list[StageSpec] = []
    head_units: list[StageUnit] = []
    head_produces = PayloadKind.NONE
    head_end = -1
    for idx, (unit, decl) in enumerate(units):
        if decl.consumes is not PayloadKind.NONE:
            raise PlanError(
                f"入力ステージ区間の backend({unit.lead.value})が payload "
                f"{decl.consumes.value} を要求していますが、前段は何も産んでいません"
            )
        head_units.append(unit)
        if decl.produces is not PayloadKind.NONE:
            head_produces = decl.produces
            head_end = idx
            break
    if head_end < 0:
        raise PlanError(
            "編成の先頭区間が発話 payload を産みません(VAD 相当の申告が必要です)"
        )
    stages.append(
        StageSpec(
            units=tuple(head_units),
            consumes=PayloadKind.NONE,
            produces=head_produces,
        )
    )

    # 3) 残りは 1 unit = 1 ステージ。隣接の payload 型整合を検証
    prev_produces = head_produces
    for unit, decl in units[head_end + 1 :]:
        if decl.consumes != prev_produces:
            raise PlanError(
                f"ステージ間の payload 形式が一致しません: 前段は {prev_produces.value} を"
                f"産みますが、{unit.lead.value} の backend は {decl.consumes.value} を要求します"
            )
        stages.append(
            StageSpec(units=(unit,), consumes=decl.consumes, produces=decl.produces)
        )
        prev_produces = decl.produces

    return PipelinePlan(stages=tuple(stages), absorbed=tuple(absorbed))


# ============================================================
# 整流役(PayloadAdapter)
# ============================================================
class PayloadAdapter(Protocol):
    """前段の出力 payload を次段の入力形式に整える整流役。

    役割: 「バッファ A 形式 → バッファ B 形式」の変換が必要になった時の差し込み口。
    現在の編成は build 時に隣接形式の一致を強制しているため、実装は素通しのみ。
    """

    def adapt(self, msg: PipelineMessage) -> PipelineMessage: ...


class IdentityAdapter:
    """無変換の整流役(隣接ステージの形式が一致している通常ケース)。"""

    def adapt(self, msg: PipelineMessage) -> PipelineMessage:
        return msg


_IDENTITY = IdentityAdapter()


def select_adapter(produces: PayloadKind, consumes: PayloadKind) -> PayloadAdapter:
    """ステージ間の整流役を選ぶ。形式一致なら素通し、不一致は未実装として拒否。"""
    if produces == consumes:
        return _IDENTITY
    raise PlanError(
        f"payload 形式 {produces.value} → {consumes.value} の整流役は未実装です"
    )
