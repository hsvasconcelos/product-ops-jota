"""
Jota · Decision Engine
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

from datetime import datetime

from pydantic import BaseModel, Field

from product_ops_jota.classifier import ClassifierOutput, DOC_THEME_PREFIX
from product_ops_jota.friction_model import (
    CriticalityFactors, ExitBarrier, FrictionCase, FrictionNature, InterceptionAction,
    Reversibility, SupportTheme, TrustRiskFactors, criticality_score, trust_risk_score,
)


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
    safety_flag: bool = False                          # vulnerável (ludopatia/autoexclusão) → gate de segurança
    stuck: bool = False                                # IA já tentou e o cliente segue travado (loop) → esgotamento


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

    # 0) Gate de SEGURANÇA (#4): cliente vulnerável (ludopatia, autoexclusão) → humano
    # SEMPRE, prioridade máxima, a IA se RECUSA de propósito. Acima de qualquer ROI.
    if inp.safety_flag:
        return Decision(
            action=InterceptionAction.HUMAN_HANDOFF, priority=1.0,
            reason="vulnerabilidade (segurança): a IA se recusa de propósito; humano com prioridade máxima",
            handoff_pressure=round(handoff_pressure, 3), ai_capability=round(ai_capability, 3),
            is_warm_handoff=True)

    # 0.5) Gate de ESGOTAMENTO (multi-toque: observou o desfecho): a IA já tentou e o
    # cliente segue travado/em loop → o procedimento não resolveu. Repetir é o pior atrito;
    # escala com contexto. Ter procedimento (resolubilidade) ≠ ele ter funcionado.
    if inp.stuck:
        return out(InterceptionAction.HUMAN_HANDOFF,
                   "a IA tentou o procedimento e o cliente segue travado (loop); humano com contexto",
                   warm=True)

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


# ─────────── Resolubilidade: os 3 fatos que dizem se a IA RESOLVE sozinha ───
# A resolubilidade não é um chute por tema — é o PRODUTO de três perguntas
# objetivas sobre o atrito. Cada fator é POLICY (inventário de KB/tool/risco),
# recalibrável sem tocar no mecanismo. O fator que falha vira o GARGALO — e o
# gargalo é backlog acionável (lacuna de conteúdo, automação a construir, ou
# decisão humana de propósito).
class ResolubilidadeFatores(BaseModel):
    kb_existe: bool        # há procedimento ANCORADO na base pra esse atrito?
    executavel: bool       # a IA consegue EXECUTAR o conserto (não só orientar)?
    reversivel: bool       # a ação é reversível / sem dano irreversível?


# valor do fator quando ele FALHA (quando passa, vale 1.0). Reversibilidade
# pesa mais: dinheiro que já foi pro destino errado a IA não desfaz.
RESOL_FAIL = {"kb": 0.25, "exec": 0.45, "rev": 0.15}


class Resolubilidade(BaseModel):
    valor: float = Field(ge=0.0, le=1.0)
    fatores: ResolubilidadeFatores
    gargalo: str | None    # qual fator derruba (None = a IA resolve sozinha)


def resolubilidade(f: ResolubilidadeFatores) -> Resolubilidade:
    kb = 1.0 if f.kb_existe else RESOL_FAIL["kb"]
    ex = 1.0 if f.executavel else RESOL_FAIL["exec"]
    rev = 1.0 if f.reversivel else RESOL_FAIL["rev"]
    # ordem de gargalo: irreversível domina (segurança), depois falta de KB, depois execução.
    if not f.reversivel:
        gargalo = "irreversível — a IA não desfaz; humano de propósito"
    elif not f.kb_existe:
        gargalo = "sem procedimento na base — lacuna de conteúdo (acionável)"
    elif not f.executavel:
        gargalo = "não executável pela IA — precisa de privilégio/decisão humana"
    else:
        gargalo = None
    return Resolubilidade(valor=round(kb * ex * rev, 3), fatores=f, gargalo=gargalo)


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


# ═══════════════════════════════════════════════════════════════════════════
# DERIVAÇÃO AO VIVO — os 4 sinais a partir do BRUTO (conversa + perfil)
# ===========================================================================
# Fecha os 2 gaps: criticidade e trust_risk deixam de ser gold/heurística-por-tema
# e passam a ser DERIVADOS da detecção + perfil do usuário (store facts, derive
# views), via as funções de score do friction_model. Tudo abaixo é POLICY nomeada
# (inventário recalibrável), não mágica no mecanismo.
# ───────────────────────────────────────────────────────────────────────────

# severidade base por tema (1–5), ANTES dos multiplicadores de contexto (§7)
BASE_SEVERITY: dict[SupportTheme, int] = {
    SupportTheme.PIX: 3, SupportTheme.FALA_TAP: 3, SupportTheme.BOLETO: 3,
    SupportTheme.ACCOUNT_ACCESS: 3, SupportTheme.KYC: 2,
    SupportTheme.YIELD_OPEN_FINANCE: 2, SupportTheme.ACCOUNT_DATA: 2, SupportTheme.OTHER: 1,
}
# resolubilidade — inventário (que temas a KB cobre / a IA executa)
KB_THEMES = {t for t in SupportTheme if t is not SupportTheme.OTHER}
# a IA consegue ENTREGAR ajuda in-thread (resolver OU orientar com procedimento da KB).
# account_access entra: "app trava/não abre" tem passo a passo (KB-ACESSO-001) — a IA guia
# primeiro; o privilégio real (desbloquear conta por segurança) escala via trust/insistência,
# não por bloquear o tema inteiro. Fora: account_data (LGPD/exclusão) e other.
EXECUTABLE_THEMES = {SupportTheme.PIX, SupportTheme.BOLETO, SupportTheme.FALA_TAP,
                     SupportTheme.KYC, SupportTheme.YIELD_OPEN_FINANCE, SupportTheme.ACCOUNT_ACCESS}
# trust — inventário por tema
MONEY_THEMES = {SupportTheme.PIX, SupportTheme.FALA_TAP, SupportTheme.BOLETO,
                SupportTheme.YIELD_OPEN_FINANCE, SupportTheme.ACCOUNT_ACCESS}
LOW_EXIT_THEMES = {SupportTheme.FALA_TAP, SupportTheme.PIX}    # concorrência a um toque
RECOVERABLE_THEMES = {SupportTheme.BOLETO}                     # estorno reverte o dano
# dano irreversível lido do TEXTO (dinheiro pro destino errado)
IRREVERSIBLE_PATTERNS = ["chave errada", "pessoa errada", "cpf errado", "numero errado",
                         "número errado", "destinatario errado", "destinatário errado",
                         "mandei pra pessoa errada", "era pra ser pro", "pix errado",
                         "transferi errado", "conta errada"]
HEAT_TRUST_BUMP = 0.15       # frustração / pedido de humano: a relação esfria AGORA (#4)
FIRST_WEEK_DAYS = 7          # primeira semana = estágio frágil (#7)


class UserProfile(BaseModel):
    """Fatos do cliente lidos do banco (users) — não derivados."""
    segment: str = "pf"                       # pf | pj | mei
    digital_literacy: str | None = None       # low | medium | high
    age_band: str | None = None
    signup_at: str | None = None              # ISO8601


def _is_irreversible(text: str) -> bool:
    t = (text or "").lower()
    return any(p in t for p in IRREVERSIBLE_PATTERNS)


def _tenure_days(signup_at: str | None, ref: datetime) -> float:
    if not signup_at:
        return 999.0
    try:
        return max(0.0, (ref - datetime.fromisoformat(signup_at)).total_seconds() / 86400.0)
    except ValueError:
        return 999.0


def derive_criticality_factors(det: ClassifierOutput, prof: UserProfile,
                               customer_text: str, ref: datetime) -> CriticalityFactors:
    """Criticidade = erro × momento × o que está em jogo (#7), derivada do bruto."""
    theme = det.predicted_theme
    primeira_semana = _tenure_days(prof.signup_at, ref) < FIRST_WEEK_DAYS
    return CriticalityFactors(
        base=BASE_SEVERITY.get(theme, 2),
        # PJ/MEI no Fala Tap = atendendo cliente na feira AGORA
        in_active_sale=(theme is SupportTheme.FALA_TAP and prof.segment in ("pj", "mei")),
        is_irreversible=_is_irreversible(customer_text),
        is_fragile_stage=(theme is SupportTheme.KYC
                          or det.predicted_nature is FrictionNature.ABSENCE_DETECTED
                          or primeira_semana),
        is_recurring=(det.in_loop or det.retry_count > 0),
    )


def derive_trust_factors(det: ClassifierOutput, prof: UserProfile,
                         customer_text: str) -> TrustRiskFactors:
    """Risco de confiança estrutural: o que está em jogo se a gente errar (#4)."""
    theme = det.predicted_theme
    if _is_irreversible(customer_text):
        rev = Reversibility.IRREVERSIBLE
    elif theme in RECOVERABLE_THEMES:
        rev = Reversibility.RECOVERABLE
    else:
        rev = Reversibility.REVERSIBLE
    frag = 0.2
    if prof.digital_literacy == "low":
        frag += 0.3
    if theme is SupportTheme.KYC or det.predicted_nature is FrictionNature.ABSENCE_DETECTED:
        frag += 0.3
    return TrustRiskFactors(
        touches_money=theme in MONEY_THEMES,
        reversibility=rev,
        exit_barrier=ExitBarrier.LOW if theme in LOW_EXIT_THEMES else ExitBarrier.MEDIUM,
        journey_fragility=min(1.0, frag),
    )


def _doc_theme(doc_id: str) -> SupportTheme | None:
    for prefix, th in DOC_THEME_PREFIX.items():
        if doc_id.startswith(prefix):
            return th
    return None


def derive_resolubilidade(det: ClassifierOutput, doc=None) -> Resolubilidade:
    """Capacidade vem da KB, NÃO de uma lista de temas no código (acaba o whack-a-mole).
      · kb_existe   = temos procedimento pro atrito (todo tema menos OTHER tem doc na KB).
      · executavel  = o procedimento NÃO está marcado `requires_human` (curado na KB pelo
                      Product Ops = o loop). Mas o flag só vale se o doc for DO TEMA detectado —
                      senão é mis-retrieval (doc errado impondo sua política).
      · reversivel  = True (a AÇÃO da IA é orientar; irreversibilidade do fato é STAKES)."""
    has_kb = det.predicted_theme is not SupportTheme.OTHER
    on_topic = doc is not None and _doc_theme(doc.id) == det.predicted_theme
    requires_human = bool(getattr(doc, "requires_human", False)) and on_topic
    return resolubilidade(ResolubilidadeFatores(
        kb_existe=has_kb,
        executavel=has_kb and not requires_human,
        reversivel=True,
    ))


def derive_decision_input(det: ClassifierOutput, prof: UserProfile, customer_text: str,
                          ref: datetime, doc=None, stuck: bool = False) -> DecisionInput:
    """Monta os 4 sinais do decide() 100% derivados — sem gold, sem número emprestado.
    `ref` = started_at (tempo de casa) · `doc` = procedimento recuperado (resolubilidade
    vem da KB) · `stuck` = esgotamento (o chamador, que tem o estado da conversa, decide)."""
    crit = criticality_score(derive_criticality_factors(det, prof, customer_text, ref))
    trust = trust_risk_score(derive_trust_factors(det, prof, customer_text))
    # calor da conversa: frustração / pedido de humano esfriam a relação agora (#4)
    if det.frustrated:
        trust = min(1.0, trust + HEAT_TRUST_BUMP)
    if det.asked_for_human:
        trust = min(1.0, trust + HEAT_TRUST_BUMP)
    resol = derive_resolubilidade(det, doc)
    return DecisionInput(
        criticality=round(crit, 2),
        trust_risk=round(trust, 2),
        resolvability=resol.valor,
        detection_confidence=round(min(det.theme_confidence, det.nature_confidence), 2),
        safety_flag=det.safety_concern,
        stuck=stuck,
    )
