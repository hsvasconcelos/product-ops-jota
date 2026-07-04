"""Desfecho — chamado FECHADO ≠ atrito RESOLVIDO.
=============================================================================
Fechar a conversa é um fato OPERACIONAL (ela terminou). Desfecho é uma
inferência sobre o MUNDO (o atrito sumiu?). Um cliente que desiste calado
fecha o chamado — e é o pior desfecho possível disfarçado de sucesso (#3).

Três sinais, do mais forte pro mais fraco (a caixa "desfecho" do diagrama):
  · system_confirmed — o evento de CURA chegou depois da intervenção
    (determinístico, confiança 1.0). O espelho da detecção por ausência:
    lá o atrito é o evento que NÃO veio; aqui a cura é o que veio.
  · explicit — o cliente DISSE que resolveu ("consegui, obrigado") ou que
    desistiu ("deixa pra la") — léxico calibrado no fecho da conversa.
  · no_recontact — silêncio sem voltar ao tema na janela. O MAIS FRACO:
    silêncio também é abandono; por isso confiança baixa, nunca "sucesso".

store facts, derive views: nada disto é carimbado no fechamento — é derivado
dos fatos (mensagens, eventos, recontato) e recalculável quando a policy muda.
"""
from __future__ import annotations

import unicodedata
from datetime import datetime, timedelta
from enum import Enum

from pydantic import BaseModel, Field

from .friction_model import SupportTheme

# ─── POLICY (config nomeada; recalibrável sem tocar no mecanismo) ────────────
# Evento de CURA por tema — o backend confirma que o atrito sumiu.
# 🟡 nomes a confirmar com o time; session.started é o único emitido no lab hoje.
CURE_EVENTS: dict[SupportTheme, list[str]] = {
    SupportTheme.KYC: ["onboarding.completed", "kyc.approved"],
    SupportTheme.PIX: ["pix.sent", "pix.completed"],
    SupportTheme.FALA_TAP: ["settlement.released", "payout.completed"],
    SupportTheme.BOLETO: ["refund.completed", "boleto.paid"],
    SupportTheme.ACCOUNT_ACCESS: ["session.started"],
    SupportTheme.YIELD_OPEN_FINANCE: ["open_finance.consent_renewed", "bank.sync_ok"],
    SupportTheme.ACCOUNT_DATA: ["data_change.completed", "deletion.completed"],
}
CONFIRM_WINDOW = timedelta(hours=24)     # cura vale se chegar até 24h após a intervenção
RECONTACT_WINDOW = timedelta(hours=72)   # sem voltar em 72h → assumido (fraco)

# Léxico de FECHO (última mensagem do cliente), normalizado sem acento.
POSITIVE_CLOSE = ["obrigad", "obg", "valeu", "vlw", "show", "perfeito", "otimo",
                  "era isso", "resolveu", "resolvido", "consegui", "deu certo",
                  "funcionou", "ate que enfim", "demorou mas resolveu", "beleza",
                  "maravilha", "top", "ajudou"]
QUIT_CLOSE = ["esquece", "deixa pra la", "vou cancelar", "perdi meu tempo",
              "desisto", "desisti", "nao aguento mais", "ninguem me ajuda",
              "ninguem resolve", "nao adianta", "..."]
# aceite CURTO — só como mensagem inteira (substring seria perigoso: "ok" ⊂ "ok aguardo").
ACCEPT_SHORT = {"ok", "okay", "okk", "ta", "ta bom", "blz", "certo", "entendi",
                "ah entendi", "ah sim", "ah ta", "entendido", "combinado"}

# confiança-base por fonte do sinal (paralelo do DEFAULT_CONFIDENCE da detecção)
CONF_BY_SOURCE = {"system_confirmed": 1.0, "explicit": 0.90, "quit": 0.85,
                  "sem_resposta": 1.0, "recontact": 0.70, "assumed": 0.55}


class Desfecho(str, Enum):
    RESOLVIDO_CONFIRMADO = "resolvido_confirmado"  # evento de cura (o mais forte)
    RESOLVIDO_EXPLICITO = "resolvido_explicito"    # o cliente disse que resolveu
    RESOLVIDO_ASSUMIDO = "resolvido_assumido"      # silêncio sem recontato (FRACO)
    NAO_RESOLVIDO = "nao_resolvido"                # voltou ao tema na janela (re-interceptar)
    ABANDONADO = "abandonado"                      # desistiu — o pior, disfarçado de fechado
    SEM_RESPOSTA = "sem_resposta"                  # o atendimento nunca respondeu


class DesfechoResult(BaseModel):
    desfecho: Desfecho
    confianca: float = Field(ge=0.0, le=1.0)
    sinal: str          # qual das fontes decidiu (auditabilidade)
    detalhe: str        # evidência legível (evento, trecho, janela)

    @property
    def resolvido(self) -> bool:
        return self.desfecho in (Desfecho.RESOLVIDO_CONFIRMADO,
                                 Desfecho.RESOLVIDO_EXPLICITO,
                                 Desfecho.RESOLVIDO_ASSUMIDO)


def _norm(t: str) -> str:
    t = unicodedata.normalize("NFKD", t or "")
    return "".join(c for c in t if not unicodedata.combining(c)).lower()


def _parse(ts: str) -> datetime | None:
    """Timestamp de produção é dado sujo: inválido vira None, nunca exceção."""
    try:
        return datetime.fromisoformat(ts)
    except (ValueError, TypeError):
        return None


def explicit_close_signal(text: str) -> str | None:
    """Léxico de fecho numa mensagem do cliente: 'positivo' | 'desistiu' | None.
    Reusável ao vivo (o bot loga o desfecho quando o cliente agradece/some)."""
    n = _norm(text)
    if any(p in n for p in QUIT_CLOSE):
        return "desistiu"
    if any(p in n for p in POSITIVE_CLOSE):
        return "positivo"
    if n.strip(" .!,🙂👍") in ACCEPT_SHORT:      # aceite curto = mensagem INTEIRA
        return "positivo"
    return None


def derive_desfecho(messages, events, theme: SupportTheme,
                    next_contact_at: str | None = None) -> DesfechoResult:
    """Deriva o desfecho de uma conversa FECHADA, só do bruto (sem gold).

    messages: (sender, text, sent_at) em ordem · events: (event_type, occurred_at)
    do MESMO usuário · theme: tema detectado (define o evento de cura) ·
    next_contact_at: início da PRÓXIMA conversa do usuário (fato), se houver.
    Precedência: sem_resposta > cura confirmada > fecho explícito > recontato/assumido.
    """
    replied = [m for m in messages if m[0] in ("bot", "human_agent")]
    if not replied:
        return DesfechoResult(desfecho=Desfecho.SEM_RESPOSTA,
                              confianca=CONF_BY_SOURCE["sem_resposta"], sinal="sem_resposta",
                              detalhe="o atendimento nunca respondeu — o silêncio que mais dói")

    # 1) cura confirmada pelo sistema: evento de cura APÓS a última intervenção.
    # Timestamps sujos são ignorados (nunca derrubam a leitura do desfecho).
    # A cura só vale ATÉ o próximo contato do usuário: se ele voltou, o evento
    # posterior pertence ao episódio novo (evita cura cruzada entre conversas —
    # achado do review adversarial round 2).
    last_reply_at = _parse(replied[-1][2])
    nc = _parse(next_contact_at) if next_contact_at is not None else None
    for et, oa in events:
        ts = _parse(oa)
        if last_reply_at is not None and ts is not None and \
                et in CURE_EVENTS.get(theme, []) and \
                (nc is None or ts < nc) and \
                last_reply_at <= ts <= last_reply_at + CONFIRM_WINDOW:
            return DesfechoResult(desfecho=Desfecho.RESOLVIDO_CONFIRMADO,
                                  confianca=CONF_BY_SOURCE["system_confirmed"],
                                  sinal="system_confirmed",
                                  detalhe=f"evento de cura: {et}")

    # 2) fecho explícito do cliente (última mensagem dele)
    customer = [m for m in messages if m[0] == "customer"]
    if customer:
        sig = explicit_close_signal(customer[-1][1])
        if sig == "positivo":
            return DesfechoResult(desfecho=Desfecho.RESOLVIDO_EXPLICITO,
                                  confianca=CONF_BY_SOURCE["explicit"], sinal="explicit",
                                  detalhe=f"cliente confirmou: “{customer[-1][1][:60]}”")
        if sig == "desistiu":
            return DesfechoResult(desfecho=Desfecho.ABANDONADO,
                                  confianca=CONF_BY_SOURCE["quit"], sinal="explicit",
                                  detalhe=f"cliente desistiu: “{customer[-1][1][:60]}”")

    # 3) recontato: voltou (nova conversa) dentro da janela → NÃO resolveu (re-interceptar)
    if nc is not None and last_reply_at is not None:
        gap = nc - last_reply_at
        if timedelta(0) <= gap <= RECONTACT_WINDOW:
            h = int(gap.total_seconds() // 3600)
            return DesfechoResult(desfecho=Desfecho.NAO_RESOLVIDO,
                                  confianca=CONF_BY_SOURCE["recontact"], sinal="no_recontact",
                                  detalhe=f"voltou ao atendimento {h}h depois — o atrito não sumiu")

    # 4) silêncio — QUEM falou por último muda tudo:
    #    · a última palavra é do CLIENTE (ficou sem resposta) → o atendimento deixou no
    #      vácuo; sem fecho não é sucesso — é o silêncio que mais dói (#3).
    #    · a última palavra é do ATENDIMENTO (entregou e o cliente sumiu) → assumido
    #      resolvido, o sinal mais FRACO (0.55) — assumido nunca vira meta.
    if messages and messages[-1][0] == "customer":
        return DesfechoResult(desfecho=Desfecho.SEM_RESPOSTA,
                              confianca=CONF_BY_SOURCE["recontact"], sinal="no_recontact",
                              detalhe="a última palavra é do cliente e ninguém respondeu — deixado no vácuo")
    return DesfechoResult(desfecho=Desfecho.RESOLVIDO_ASSUMIDO,
                          confianca=CONF_BY_SOURCE["assumed"], sinal="no_recontact",
                          detalhe="atendimento respondeu, cliente sumiu sem recontato em 72h — assumido, não confirmado")
