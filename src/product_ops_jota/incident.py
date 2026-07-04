"""Modo incidente — quando o atrito é de TODOS, interceptar um a um é spam.
=============================================================================
Um bug de Pix que atinge 50 mil clientes de uma vez quebraria o desenho
individual: a proativa viraria enxurrada e a fila humana afogaria. A v1 aqui:

  · DETECÇÃO (determinística): pico de eventos do MESMO tipo numa janela curta
    → aquilo não é o atrito de um cliente, é um incidente.
  · RESPOSTA: congela a interceptação individual do tema e muda a comunicação
    para o modo incidente (uma mensagem informativa, honesta, sem pedir ação),
    até o pico cessar.

O que a v1 NÃO cobre (dito com todas as letras): o playbook de comunicação em
massa (quem aprova o texto, para quais coortes) e o regime especial das filas
humanas durante o incidente. É o desenho da semana extra, não o fim da conversa.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta

# ─── POLICY (🟡 a calibrar com o time — volumes reais de produção) ────────────
INCIDENT_MIN_EVENTS = 30        # eventos do MESMO tipo…
INCIDENT_WINDOW = timedelta(minutes=15)   # …dentro desta janela = pico


def detect_incident(events, now: datetime,
                    min_events: int = INCIDENT_MIN_EVENTS,
                    window: timedelta = INCIDENT_WINDOW) -> str | None:
    """Há um pico de um MESMO tipo de evento na janela recente?
    events: (event_type, occurred_at ISO) · devolve o event_type em pico, ou None.
    Determinístico: contagem em janela, sem interpretação."""
    ini = now - window
    recentes = Counter()
    for et, oa in events:
        try:
            ts = datetime.fromisoformat(oa)
        except ValueError:
            continue
        if ini <= ts <= now:
            recentes[et] += 1
    for et, n in recentes.most_common(1):
        if n >= min_events:
            return et
    return None


def incident_message(event_type: str) -> str:
    """Comunicação de incidente: informa, assume, e tira o peso do cliente.
    Canônica (sem LLM): em incidente, previsibilidade vale mais que prosa."""
    area = {"pix": "no Pix", "settlement": "no recebimento de vendas",
            "session": "no acesso ao app", "kyc": "na abertura de contas",
            "payment": "em cobranças", "boleto": "em boletos"}
    chave = next((v for k, v in area.items() if event_type.startswith(k)), "em um dos nossos serviços")
    return (f"⚠️ Estamos com uma instabilidade {chave} afetando vários clientes neste momento. "
            f"Seu caso já está identificado no grupo — você não precisa fazer nada agora. "
            f"Assim que normalizar, eu te aviso aqui mesmo, e se algo tiver saído errado do seu lado, "
            f"a correção é automática. Obrigado pela paciência. 🙏")
