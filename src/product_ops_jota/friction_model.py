"""
Atalaia · Friction Model
=========================
Modelo de domínio do "mapa de problemas": como um atrito é detectado,
quão crítico ele é, quanto risco de confiança carrega, e qual a ação típica.

Princípios de engenharia aplicados aqui:
  · store facts, derive views — o banco guarda o que aconteceu (*_at, contadores);
    janelas/idades/scores são funções puras calculadas em runtime.
  · policy vs mechanism — os PESOS dos scores são configuração tipada e
    versionável; as funções de score são o mecanismo, estável.
  · fail-fast — uma DetectionRule inconsistente quebra na construção, não em prod.
"""
from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field, model_validator


# ───────────────────────────── Enums de domínio ─────────────────────────────
class FrictionNature(str, Enum):
    """Como o sinal de atrito chega ao radar — define a disciplina de detecção."""
    SYSTEM_SIGNALED = "system_signaled"      # um evento de falha foi emitido (EDA)
    BEHAVIOR_INFERRED = "behavior_inferred"  # deduzido do comportamento (classificador)
    ABSENCE_DETECTED = "absence_detected"    # ausência de um evento esperado (anomaly)


class SupportTheme(str, Enum):
    """Temas do canal de suporte — fonte ÚNICA da lista (schema, painel e
    classificador referenciam isto, não strings soltas). Ancorados no
    ReclameAqui real do Jota."""
    ACCOUNT_ACCESS = "account_access"
    PIX = "pix"
    KYC = "kyc"
    FALA_TAP = "fala_tap"
    BOLETO = "boleto"
    ACCOUNT_DATA = "account_data"
    YIELD_OPEN_FINANCE = "yield_open_finance"
    OTHER = "other"


class Product(str, Enum):
    ACCOUNT = "account"
    PIX = "pix"
    FALA_TAP = "fala_tap"
    YIELD = "yield"
    TASKS = "tasks"
    OPEN_FINANCE = "open_finance"
    BILL_RADAR = "bill_radar"


class JourneyStage(str, Enum):
    ONBOARDING = "onboarding"
    ACTIVATION = "activation"
    FIRST_VALUE = "first_value"
    HABITUAL = "habitual"
    EXPANSION = "expansion"
    AT_RISK = "at_risk"


class Reversibility(str, Enum):
    REVERSIBLE = "reversible"
    RECOVERABLE = "recoverable"
    IRREVERSIBLE = "irreversible"


class ExitBarrier(str, Enum):
    """Quão fácil é o cliente trocar pela alternativa (maquininha/banco tradicional)."""
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class ProfileFlag(str, Enum):
    """Proxies observáveis que agravam o atrito ou mudam a FORMA de responder."""
    LOW_DIGITAL_LITERACY = "low_digital_literacy"
    AUDIO_USER = "audio_user"
    FIRST_WEEK = "first_week"
    ALREADY_RETRIED = "already_retried"
    PJ_MID_SALE = "pj_mid_sale"
    NEW_SELLER = "new_seller"
    D1_PLAN = "d1_plan"


class InterceptionAction(str, Enum):
    NO_INTERCEPT = "no_intercept"
    AI_RESOLVE = "ai_resolve"
    AI_RESOLVE_SILENT = "ai_resolve_silent"  # resolve em background, sem incomodar
    AI_ASSIST = "ai_assist"
    HUMAN_HANDOFF = "human_handoff"


# ─────────────── Detecção: COMO o atrito é detectado (+ confiança) ──────────
# Confiança-base padrão por natureza. Um evento de sistema é fato (1.0);
# inferência de comportamento é probabilística (<1.0) e calibrável.
DEFAULT_CONFIDENCE: dict[FrictionNature, float] = {
    FrictionNature.SYSTEM_SIGNALED: 1.0,
    FrictionNature.BEHAVIOR_INFERRED: 0.6,
    FrictionNature.ABSENCE_DETECTED: 0.75,
}


class DetectionRule(BaseModel):
    nature: FrictionNature
    # base_confidence: se None, é derivada da natureza (DRY).
    base_confidence: float | None = Field(default=None, ge=0.0, le=1.0)

    # SYSTEM_SIGNALED
    event_type: str | None = None                 # "pix.returned"
    # BEHAVIOR_INFERRED
    behavior_feature: str | None = None           # "key_not_found_count"
    threshold: float | None = None                # >= 2
    window_minutes: int | None = None             # janela de observação
    # ABSENCE_DETECTED
    expected_event: str | None = None             # "onboarding.completed"
    baseline_window_minutes: int | None = None    # esperado dentro de N min

    @model_validator(mode="after")
    def _enforce_consistency(self) -> "DetectionRule":
        """fail-fast: cada natureza exige os campos que a tornam acionável."""
        if self.base_confidence is None:
            self.base_confidence = DEFAULT_CONFIDENCE[self.nature]

        if self.nature is FrictionNature.SYSTEM_SIGNALED and not self.event_type:
            raise ValueError("SYSTEM_SIGNALED exige event_type")
        if self.nature is FrictionNature.BEHAVIOR_INFERRED and (
            not self.behavior_feature or self.threshold is None
        ):
            raise ValueError("BEHAVIOR_INFERRED exige behavior_feature e threshold")
        if self.nature is FrictionNature.ABSENCE_DETECTED and (
            not self.expected_event or self.baseline_window_minutes is None
        ):
            raise ValueError("ABSENCE_DETECTED exige expected_event e baseline_window_minutes")
        return self


# ─────────────── Pesos como configuração tipada (policy, não mecanismo) ─────
class TrustRiskWeights(BaseModel):
    touches_money: float = 0.35
    irreversible: float = 0.30
    recoverable: float = 0.15
    exit_barrier_low: float = 0.25
    exit_barrier_medium: float = 0.10
    journey_fragility: float = 0.10


class CriticalityWeights(BaseModel):
    in_active_sale: float = 1.6
    irreversible: float = 1.4
    fragile_stage: float = 1.3
    recurring: float = 1.2


DEFAULT_TRUST_WEIGHTS = TrustRiskWeights()
DEFAULT_CRITICALITY_WEIGHTS = CriticalityWeights()


# ─────────────── Risco de confiança: decomposto e calculável ────────────────
class TrustRiskFactors(BaseModel):
    touches_money: bool                           # envolve saldo/transação/recebimento?
    reversibility: Reversibility
    exit_barrier: ExitBarrier                     # facilidade de ir pro concorrente
    journey_fragility: float = Field(ge=0.0, le=1.0)


def trust_risk_score(
    f: TrustRiskFactors, w: TrustRiskWeights = DEFAULT_TRUST_WEIGHTS
) -> float:
    """
    Composição ADITIVA dos fatores de risco de confiança → 0..1.
    Limitação conhecida: o clamp final em 1.0 satura se muitos fatores
    somarem alto. Revisar para média ponderada se acrescentarmos fatores.
    """
    score = 0.0
    if f.touches_money:
        score += w.touches_money
    score += {
        Reversibility.REVERSIBLE: 0.0,
        Reversibility.RECOVERABLE: w.recoverable,
        Reversibility.IRREVERSIBLE: w.irreversible,
    }[f.reversibility]
    score += {
        ExitBarrier.HIGH: 0.0,
        ExitBarrier.MEDIUM: w.exit_barrier_medium,
        ExitBarrier.LOW: w.exit_barrier_low,
    }[f.exit_barrier]
    score += w.journey_fragility * f.journey_fragility
    return min(score, 1.0)


# ─────────────── Criticidade: severidade base × multiplicadores ─────────────
class CriticalityFactors(BaseModel):
    base: int = Field(ge=1, le=5)
    in_active_sale: bool = False                  # atendendo cliente AGORA (feira)
    is_irreversible: bool = False
    is_fragile_stage: bool = False                # onboarding / first_value
    is_recurring: bool = False                    # já travou nisso antes


def criticality_score(
    c: CriticalityFactors, w: CriticalityWeights = DEFAULT_CRITICALITY_WEIGHTS
) -> float:
    """Severidade base amplificada pelo contexto. Teto em 5."""
    mult = 1.0
    if c.in_active_sale:
        mult *= w.in_active_sale
    if c.is_irreversible:
        mult *= w.irreversible
    if c.is_fragile_stage:
        mult *= w.fragile_stage
    if c.is_recurring:
        mult *= w.recurring
    return min(c.base * mult, 5.0)


# ─────────────────────────── O card de atrito ──────────────────────────────
class FrictionCase(BaseModel):
    """Uma entrada do mapa de problemas. baseline_action é a ação TÍPICA;
    o score de priorização pode sobrepô-la em runtime conforme o contexto real
    do cliente (criticidade, confiança da detecção, perfil)."""
    case_id: str
    product: Product
    journey_stage: JourneyStage
    detection: DetectionRule
    aggravating_profile: list[ProfileFlag] = Field(default_factory=list)
    criticality: CriticalityFactors
    trust_risk: TrustRiskFactors
    baseline_action: InterceptionAction
    eval_golden_id: str

    # atalhos calculados (não persistidos) — conveniência de leitura
    @property
    def trust_risk_value(self) -> float:
        return trust_risk_score(self.trust_risk)

    @property
    def criticality_value(self) -> float:
        return criticality_score(self.criticality)


# ═══════════════════════════════════════════════════════════════════════════
# Os 3 casos-herói — instâncias do schema = golden cases do sistema
# ═══════════════════════════════════════════════════════════════════════════
LOOP_CHAVE_PIX = FrictionCase(
    case_id="pix_key_loop",
    product=Product.PIX,
    journey_stage=JourneyStage.FIRST_VALUE,
    detection=DetectionRule(
        nature=FrictionNature.BEHAVIOR_INFERRED,   # nada apitou — é o padrão que denuncia
        behavior_feature="key_not_found_count",
        threshold=2,
        window_minutes=10,
    ),
    aggravating_profile=[ProfileFlag.LOW_DIGITAL_LITERACY, ProfileFlag.FIRST_WEEK],
    criticality=CriticalityFactors(base=2, is_fragile_stage=True, is_recurring=True),
    trust_risk=TrustRiskFactors(
        touches_money=True,
        reversibility=Reversibility.REVERSIBLE,
        exit_barrier=ExitBarrier.MEDIUM,
        journey_fragility=0.7,
    ),
    baseline_action=InterceptionAction.AI_RESOLVE,
    eval_golden_id="REAL-CHAVE-LOOP",
)

KYC_FALHOU = FrictionCase(
    case_id="kyc_failed_onboarding",
    product=Product.ACCOUNT,
    journey_stage=JourneyStage.ONBOARDING,
    detection=DetectionRule(
        nature=FrictionNature.SYSTEM_SIGNALED,
        event_type="kyc.failed",
    ),
    aggravating_profile=[ProfileFlag.LOW_DIGITAL_LITERACY, ProfileFlag.ALREADY_RETRIED],
    criticality=CriticalityFactors(base=3, is_fragile_stage=True),
    trust_risk=TrustRiskFactors(
        touches_money=False,
        reversibility=Reversibility.RECOVERABLE,
        exit_barrier=ExitBarrier.MEDIUM,
        journey_fragility=0.9,
    ),
    baseline_action=InterceptionAction.AI_ASSIST,
    eval_golden_id="REAL-KYC",
)

FALA_TAP_INSEGURANCA = FrictionCase(
    case_id="fala_tap_receipt_anxiety",
    product=Product.FALA_TAP,
    journey_stage=JourneyStage.HABITUAL,
    detection=DetectionRule(
        nature=FrictionNature.BEHAVIOR_INFERRED,   # a ansiedade lida no chat,
        behavior_feature="receipt_anxiety_intent",  # correlacionada com
        threshold=0.7,                               # tap_to_pay.payment_approved
        window_minutes=15,                           # para responder com PROVA
    ),
    aggravating_profile=[ProfileFlag.PJ_MID_SALE, ProfileFlag.NEW_SELLER, ProfileFlag.D1_PLAN],
    criticality=CriticalityFactors(base=2, in_active_sale=True),
    trust_risk=TrustRiskFactors(
        touches_money=True,
        reversibility=Reversibility.REVERSIBLE,
        exit_barrier=ExitBarrier.LOW,   # custo de troca quase zero = o vilão
        journey_fragility=0.5,
    ),
    baseline_action=InterceptionAction.AI_RESOLVE,
    eval_golden_id="SYN-FALA-TAP",
)

HERO_CASES = [LOOP_CHAVE_PIX, KYC_FALHOU, FALA_TAP_INSEGURANCA]
