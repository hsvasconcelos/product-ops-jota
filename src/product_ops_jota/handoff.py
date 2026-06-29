"""Operação humana — o handoff QUENTE (Mundo 1 ↔ time de atendimento).
=============================================================================
Quando o roteador decide humano, a transferência não pode ser um "bounce frio".
Este módulo monta o PACOTE DE CONTEXTO que vai junto (combate direto ao
context_lost — o atendente não pergunta tudo de novo), roteia pra fila da
ESPECIALIDADE certa com ciência de HORÁRIO, e aplica a regra de não interceptar
a mesma dor duas vezes (anti-spam, com escalonamento se repetir).

store facts, derive views: o pacote é DERIVADO da detecção + decisão na hora,
não persistido. Políticas (especialidade, horário, janela) são config nomeada.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from pydantic import BaseModel, Field

from .channels import WhatsAppLine, proactive_line
from .classifier import ClassifierOutput
from .decision import Decision
from .friction_model import FrictionNature, SupportTheme

# ─── POLICY (config nomeada) ─────────────────────────────────────────────────
SPECIALTY_BY_THEME: dict[SupportTheme, str] = {
    SupportTheme.PIX: "Pagamentos/Pix",
    SupportTheme.KYC: "Onboarding/KYC",
    SupportTheme.FALA_TAP: "Recebimento (PJ)",
    SupportTheme.BOLETO: "Cobranças/Boleto",
    SupportTheme.ACCOUNT_ACCESS: "Acesso & Segurança",
    SupportTheme.ACCOUNT_DATA: "Cadastro & LGPD",
    SupportTheme.YIELD_OPEN_FINANCE: "Investimentos/Open Finance",
    SupportTheme.OTHER: "Geral",
}
BUSINESS_HOURS = (9, 20)                      # atendimento humano 9h–20h (KB-SUP-001)
REINTERCEPT_WINDOW = timedelta(hours=24)      # não insistir na mesma dor dentro disso


class QueueRouting(BaseModel):
    specialty: str
    priority: float = Field(ge=0.0, le=1.0)
    in_hours: bool
    note: str


class ContextPack(BaseModel):
    """O que o atendente humano recebe junto com a transferência."""
    segment: str
    line: WhatsAppLine
    theme: SupportTheme
    nature: FrictionNature
    criticality: float
    evidence: str                  # evento de sistema OU padrões no texto
    signals: list[str]             # sinais comportamentais já detectados
    ai_offered: str | None         # o que a IA já tentou (pra não repetir)
    routing: QueueRouting
    reason: str                    # por que virou humano (do decision)


def route_to_queue(theme: SupportTheme, priority: float, hour: int) -> QueueRouting:
    """Fila por especialidade + ciência de horário. Fora do horário, registra
    e prioriza no próximo turno — nunca um silêncio."""
    in_hours = BUSINESS_HOURS[0] <= hour < BUSINESS_HOURS[1]
    specialty = SPECIALTY_BY_THEME.get(theme, "Geral")
    note = (f"em horário → entra na fila {specialty}"
            if in_hours else
            f"fora do horário ({BUSINESS_HOURS[0]}h–{BUSINESS_HOURS[1]}h) → registrado, "
            f"retorno prioritário no próximo turno")
    return QueueRouting(specialty=specialty, priority=priority, in_hours=in_hours, note=note)


def build_context_pack(detection: ClassifierOutput, decision: Decision, criticality: float,
                       segment: str, hour: int, ai_offered: str | None = None) -> ContextPack:
    """Monta o pacote quente a partir do que já foi detectado/decidido."""
    signals = [n for n, v in (("pediu humano", detection.asked_for_human),
                              ("frustrado", detection.frustrated),
                              ("em loop", detection.in_loop)) if v]
    evidence = (f"evento de sistema: {detection.correlated_event}"
                if detection.correlated_event
                else f"padrões no texto: {', '.join(detection.matched_keywords) or '—'}")
    return ContextPack(
        segment=segment, line=proactive_line(segment),
        theme=detection.predicted_theme, nature=detection.predicted_nature,
        criticality=round(criticality, 2), evidence=evidence, signals=signals,
        ai_offered=ai_offered,
        routing=route_to_queue(detection.predicted_theme, decision.priority, hour),
        reason=decision.reason)


def should_reintercept(theme: SupportTheme, last_intercept_at: datetime | None,
                       now: datetime, escalating: bool = False) -> tuple[bool, str]:
    """Interceptar a MESMA dor de novo? Só se for novo, se escalou, ou se a
    janela passou — senão é spam (e atrito de confiança)."""
    if last_intercept_at is None:
        return True, "primeiro contato sobre o tema"
    if escalating:
        return True, "o problema repetiu/piorou → intervir de novo, com prioridade"
    if now - last_intercept_at < REINTERCEPT_WINDOW:
        h = int(REINTERCEPT_WINDOW.total_seconds() // 3600)
        return False, f"já interceptado há <{h}h sobre {theme.value}; não insistir (anti-spam)"
    return True, "janela passou; pode interceptar de novo"
