"""
Atalaia · Decision Engine
=========================
O score de priorização: dados quatro números sobre um atrito, decide a AÇÃO
de interceptação (resolver / assistir / humano) e a PRIORIDADE na fila.

É a tradução dos 8 princípios em mecanismo:
  · P4 (confiança vem antes) → trust_risk que a IA não pode resolver COM PROVA
    empurra para humano; e nunca agimos sobre um palpite fraco (conf_floor).
  · P1 (resolver sem escalar) → se a IA tem capacidade real, resolve in-thread.
  · P7 (criticidade contextual) → criticidade define a PRIORIDADE na fila.
  · P8 (ROI do cuidado) → atrito trivial não vira interceptação ativa.

Os limiares são CONFIGURAÇÃO TIPADA (policy), não mágica no código
(mechanism) — recalibráveis sem tocar na função.

Canais: AI_RESOLVE / AI_ASSIST acontecem in-thread no número do Jota.
HUMAN_HANDOFF é uma transferência QUENTE para o número de atendimento,
carregando o pacote de contexto — nunca um "bounce frio".
"""
from __future__ import annotations

from pydantic import BaseModel, Field

from product_ops_jota.friction_model import FrictionCase, InterceptionAction


# ─────────────── Limiares como política versionável (não mágica) ────────────
class PolicyThresholds(BaseModel):
    conf_floor: float = 0.50                # abaixo disto: não age (palpite fraco)
    roi_crit_floor: float = 2.0             # criticidade trivial...
    roi_trust_floor: float = 0.20           # ...e trust baixo → não incomoda
    handoff_ceiling: float = 0.40           # trust em jogo que a IA não prova → humano
    resolve_floor: float = 0.50             # capacidade suficiente p/ resolver sozinho
    assist_floor: float = 0.35              # capacidade parcial → assiste


DEFAULT_THRESHOLDS = PolicyThresholds()


# ─────────────────────────── Entrada e saída ───────────────────────────────
class DecisionInput(BaseModel):
    """Os quatro sinais que alimentam a decisão."""
    criticality: float = Field(ge=1.0, le=5.0)        # severidade contextual (P7)
    trust_risk: float = Field(ge=0.0, le=1.0)         # confiança em jogo (P4)
    resolvability: float = Field(ge=0.0, le=1.0)      # a IA tem resolução ANCORADA?
    detection_confidence: float = Field(ge=0.0, le=1.0)  # quão certo é o atrito?


class Decision(BaseModel):
    action: InterceptionAction
    priority: float = Field(ge=0.0, le=1.0)   # ordenação na fila (1 = mais urgente)
    reason: str                               # auditabilidade: por que esta ação
    handoff_pressure: float                   # trust que a IA não resolve com prova
    ai_capability: float                      # resolubilidade × confiança da detecção
    is_warm_handoff: bool = False             # humano = transferência COM contexto


# ─────────────────────────── A função de decisão ───────────────────────────
def decide(inp: DecisionInput, t: PolicyThresholds = DEFAULT_THRESHOLDS) -> Decision:
    # Dois compostos que carregam a lógica dos princípios:
    # trust em jogo que NÃO dá para resolver com prova → pressão por humano (P4)
    handoff_pressure = inp.trust_risk * (1.0 - inp.resolvability)
    # a IA consegue agir bem E temos certeza de que o atrito é real (P1)
    ai_capability = inp.resolvability * inp.detection_confidence

    # prioridade na fila: criticidade domina, trust não-resolvido empurra (P7)
    norm_crit = (inp.criticality - 1.0) / 4.0
    priority = min(0.6 * norm_crit + 0.4 * handoff_pressure, 1.0)

    def out(action: InterceptionAction, reason: str, warm: bool = False) -> Decision:
        return Decision(
            action=action, priority=round(priority, 3), reason=reason,
            handoff_pressure=round(handoff_pressure, 3),
            ai_capability=round(ai_capability, 3), is_warm_handoff=warm,
        )

    # 1) Gate de confiança (P4/P8): não se age sobre um palpite fraco.
    if inp.detection_confidence < t.conf_floor:
        return out(InterceptionAction.NO_INTERCEPT,
                   f"detecção incerta ({inp.detection_confidence:.2f} < {t.conf_floor}); observar mais")

    # 2) Gate de ROI (P8): atrito trivial não vira interceptação ativa.
    if inp.criticality < t.roi_crit_floor and inp.trust_risk < t.roi_trust_floor:
        if inp.resolvability >= t.resolve_floor:
            return out(InterceptionAction.AI_RESOLVE_SILENT,
                       "trivial e resolúvel; resolver em background sem incomodar")
        return out(InterceptionAction.NO_INTERCEPT,
                   "trivial e de baixo risco; custo de interceptar não se justifica")

    # 3) Gate de confiança/P4: trust em jogo que a IA não resolve com prova → humano quente.
    if handoff_pressure >= t.handoff_ceiling:
        return out(InterceptionAction.HUMAN_HANDOFF,
                   f"confiança em jogo sem resolução ancorada (pressão={handoff_pressure:.2f} ≥ {t.handoff_ceiling}); "
                   f"humano de propósito com contexto", warm=True)

    # 4) Capacidade da IA decide resolver vs assistir (P1).
    if ai_capability >= t.resolve_floor:
        return out(InterceptionAction.AI_RESOLVE,
                   f"IA resolve in-thread (capacidade={ai_capability:.2f} ≥ {t.resolve_floor})")
    if ai_capability >= t.assist_floor:
        return out(InterceptionAction.AI_ASSIST,
                   f"resolução parcial (capacidade={ai_capability:.2f}); IA assiste, humano no loop")

    # 5) Pouca capacidade → humano quente.
    return out(InterceptionAction.HUMAN_HANDOFF,
               f"baixa capacidade da IA ({ai_capability:.2f}); humano com contexto", warm=True)


# ─────────── Ponte com o mapa de problemas: decidir a partir de um case ─────
# resolvability vem de fora dos 4 fatos do card (depende de termos KB/tool p/ resolver).
def decide_for_case(case: FrictionCase, resolvability: float,
                    t: PolicyThresholds = DEFAULT_THRESHOLDS) -> Decision:
    inp = DecisionInput(
        criticality=case.criticality_value,
        trust_risk=case.trust_risk_value,
        resolvability=resolvability,
        detection_confidence=case.detection.base_confidence,
    )
    return decide(inp, t)
