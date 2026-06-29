"""Copiloto do atendimento — mockup da Pergunta 5 do case.
=============================================================================
Lê de um JSON exemplos de "conversas ruins", aplica DETECÇÃO objetiva de
atrito (classifier.py), consulta o RAG (rag.py) e gera uma SUGESTÃO PRO
ATENDENTE: resposta recomendada + passo a passo, ANCORADA no doc recuperado
(não inventada). Mostra também o roteamento (decision.py).

ENQUADRAMENTO (decisão de produto): o output NÃO é "a IA responde ao cliente".
É o ESTÁGIO 1 — COPILOTO: a IA sugere, o atendente humano copia/edita/decide.
É o degrau mais seguro da escada (copiloto → automação no suporte → proativo
no produto): protege a confiança, que é o produto.

Geração SEM alucinação: a resposta é montada por TEMPLATE a partir do doc
recuperado (offline, determinístico). Evolução documentada: um LLM fazendo
grounded generation EM CIMA do doc — com fallback pro template (mesmo padrão
de degradação graciosa do RAG).

Uso:
    python apps/copiloto.py                       # todas as conversas
    python apps/copiloto.py ex_fala_tap_inseguranca   # uma conversa por id
"""
from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from rich.box import ROUNDED, SIMPLE_HEAVY
from rich.console import Console, Group
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from product_ops_jota.classifier import classify_conversation
from product_ops_jota.decision import DecisionInput, decide
from product_ops_jota.friction_model import InterceptionAction, SupportTheme
from product_ops_jota.rag import Retriever

DATA = Path(__file__).resolve().parents[1] / "data" / "conversas_ruins.json"
console = Console()

# ─── POLICY (config nomeada) ─────────────────────────────────────────────────
TOP_K = 2                      # docs recuperados por conversa
RETRIEVAL_SCORE_FLOOR = 0.01   # abaixo disso, retrieval fraco → derruba resolvability

# Sinais de IRREVERSIBILIDADE (dinheiro que já saiu pra destino errado/real). Mapeia
# ao conceito de Reversibility do friction_model: a IA NÃO pode desfazer um Pix
# concluído → resolubilidade despenca e trust dispara → o decide() manda pra humano.
IRREVERSIBLE_PATTERNS = ["chave errada", "pessoa errada", "cpf errado", "numero errado",
                         "destinatario errado", "mandei pra pessoa errada", "era pra ser pro",
                         "pix errado", "transferi errado"]
RESOLVABILITY_IRREVERSIBLE = 0.15
TRUST_IRREVERSIBLE = 0.95

# Inputs do decision.py derivados por HEURÍSTICA do mockup (documentado: não são
# fatos do banco — o copiloto roda sobre JSON avulso). Base por tema, 1–5 / 0–1.
THEME_CRITICALITY = {
    SupportTheme.FALA_TAP: 4.0, SupportTheme.KYC: 3.6, SupportTheme.BOLETO: 3.5,
    SupportTheme.PIX: 3.0, SupportTheme.ACCOUNT_ACCESS: 3.0,
    SupportTheme.YIELD_OPEN_FINANCE: 2.2, SupportTheme.ACCOUNT_DATA: 2.0,
    SupportTheme.OTHER: 2.0,
}
THEME_TRUST_RISK = {  # quanto de confiança está em jogo (dinheiro/segurança ↑)
    SupportTheme.FALA_TAP: 0.7, SupportTheme.PIX: 0.6, SupportTheme.BOLETO: 0.6,
    SupportTheme.ACCOUNT_ACCESS: 0.5, SupportTheme.KYC: 0.4,
    SupportTheme.YIELD_OPEN_FINANCE: 0.3, SupportTheme.ACCOUNT_DATA: 0.4,
    SupportTheme.OTHER: 0.3,
}
THEME_RESOLVABILITY = {  # quão bem a IA resolve ANCORADA num doc (account/security ↓)
    SupportTheme.PIX: 0.85, SupportTheme.FALA_TAP: 0.85, SupportTheme.YIELD_OPEN_FINANCE: 0.85,
    SupportTheme.BOLETO: 0.7, SupportTheme.KYC: 0.6, SupportTheme.ACCOUNT_DATA: 0.7,
    SupportTheme.ACCOUNT_ACCESS: 0.5, SupportTheme.OTHER: 0.5,
}

# Abertura empática por tema (pergunta, não afirmação — se errar, o cliente corrige).
ACK_BY_THEME = {
    SupportTheme.PIX: "vi que você pode estar tendo dificuldade com um Pix, é isso?",
    SupportTheme.FALA_TAP: "vi que pode ser sobre o recebimento de uma venda no Fala Tap, certo?",
    SupportTheme.KYC: "vi que pode estar travando na abertura/verificação da conta, é isso?",
    SupportTheme.BOLETO: "vi que pode ser sobre uma cobrança de boleto, certo?",
    SupportTheme.ACCOUNT_ACCESS: "vi que pode estar com dificuldade pra acessar o app, é isso?",
    SupportTheme.ACCOUNT_DATA: "vi que é sobre alterar dados ou a sua conta, certo?",
    SupportTheme.YIELD_OPEN_FINANCE: "vi que pode ser sobre rendimento ou Open Finance, certo?",
    SupportTheme.OTHER: "me conta um pouco mais pra eu te ajudar certo?",
}

SENDER_STYLE = {"customer": "bold white", "human_agent": "green", "bot": "cyan"}
ACTION_LABEL = {
    InterceptionAction.AI_RESOLVE: ("IA RESOLVE in-thread", "green"),
    InterceptionAction.AI_RESOLVE_SILENT: ("IA resolve em background", "green"),
    InterceptionAction.AI_ASSIST: ("IA ASSISTE (humano no loop)", "yellow"),
    InterceptionAction.HUMAN_HANDOFF: ("HUMANO (handoff quente)", "red"),
    InterceptionAction.NO_INTERCEPT: ("não interceptar (observar)", "dim"),
}


def _adaptar(conv: dict):
    """JSON → tuplas que o classifier espera (sender, text, sent_at) / (event_type, occurred_at)."""
    messages = [(m["sender"], m["text"], m["sent_at"]) for m in conv["mensagens"]]
    events = [(e["event_type"], e["occurred_at"]) for e in conv.get("eventos", [])]
    return messages, events, conv["started_at"]


def montar_query(conv: dict, theme: SupportTheme) -> str:
    """Query do RAG = texto do cliente + o tema (reforço de termo)."""
    txt = " ".join(m["text"] for m in conv["mensagens"] if m["sender"] == "customer")
    return f"{txt} {theme.value.replace('_', ' ')}"


def montar_sugestao(theme: SupportTheme, docs):
    """Resposta recomendada + passo a passo, ANCORADOS no doc (sem inventar)."""
    if not docs:
        return None
    doc = docs[0]
    ack = ACK_BY_THEME.get(theme, ACK_BY_THEME[SupportTheme.OTHER])
    resposta = f"Oi! {ack}\n\n{doc.content}"
    return {"resposta": resposta, "passos": doc.steps, "fontes": [d.id for d in docs]}


def derivar_decisao(theme, detection, docs, customer_text=""):
    """Monta os 4 sinais do decide() — heurística documentada do mockup."""
    crit = THEME_CRITICALITY.get(theme, 2.0)
    trust = THEME_TRUST_RISK.get(theme, 0.3)
    if detection.frustrated:
        trust = min(1.0, trust + 0.15)
    if detection.asked_for_human:
        trust = min(1.0, trust + 0.15)
        crit = min(5.0, crit + 0.3)
    resolv = THEME_RESOLVABILITY.get(theme, 0.5)
    # sem doc forte recuperado, a IA não tem como resolver ancorada → derruba
    if not docs or docs[0].score < RETRIEVAL_SCORE_FLOOR:
        resolv = min(resolv, 0.3)
    # irreversível (dinheiro pra destino errado): a IA não desfaz → humano
    if any(p in customer_text.lower() for p in IRREVERSIBLE_PATTERNS):
        resolv = min(resolv, RESOLVABILITY_IRREVERSIBLE)
        trust = max(trust, TRUST_IRREVERSIBLE)
        crit = min(5.0, max(crit, 4.0))
    det_conf = min(detection.theme_confidence, detection.nature_confidence)
    return DecisionInput(criticality=round(crit, 2), trust_risk=round(trust, 2),
                         resolvability=round(resolv, 2), detection_confidence=round(det_conf, 2))


def render_conversa(conv, detection, docs, sugestao, decisao, rag_mode) -> Panel:
    blocos = []

    # 1) a conversa
    corpo = Text()
    for m in conv["mensagens"]:
        corpo.append(f"  {m['sent_at'][11:16]}  ", style="dim")
        corpo.append(f"{m['sender']}: ", style=SENDER_STYLE.get(m["sender"], "white"))
        corpo.append(m["text"] + "\n")
    blocos.append(Panel(corpo, title="conversa (cliente × atendimento)", border_style="dim", box=ROUNDED))

    # 2) atrito detectado
    det = Text()
    det.append("  tema: ", style="dim")
    det.append(f"{detection.predicted_theme.value} ", style="bold")
    det.append(f"({detection.theme_confidence:.2f})   ", style="dim")
    det.append("natureza: ", style="dim")
    det.append(f"{detection.predicted_nature.value} ", style="bold")
    det.append(f"({detection.nature_confidence:.2f})\n", style="dim")
    sinais = [n for n, v in [("pediu humano", detection.asked_for_human),
                             ("frustrado", detection.frustrated), ("em loop", detection.in_loop)] if v]
    det.append("  sinais: ", style="dim")
    det.append(", ".join(sinais) if sinais else "—")
    if detection.correlated_event:
        det.append(f"\n  evento correlacionado: {detection.correlated_event}", style="magenta")
    blocos.append(Panel(det, title="atrito detectado (classifier — lógica objetiva)",
                        border_style="cyan", box=ROUNDED))

    # 3) docs recuperados
    tab = Table(box=SIMPLE_HEAVY, expand=True, title=f"RAG · {rag_mode} · top {len(docs)}")
    tab.add_column("Doc"); tab.add_column("Título"); tab.add_column("Score", justify="right")
    for d in docs:
        tab.add_row(d.id, d.title, f"{d.score:.3f}")
    blocos.append(tab)

    # 4) a sugestão (o coração do copiloto)
    if sugestao:
        sug = Text()
        sug.append(sugestao["resposta"] + "\n\n", style="white")
        sug.append("Passo a passo:\n", style="bold")
        for i, passo in enumerate(sugestao["passos"], 1):
            sug.append(f"  {i}. {passo}\n")
        sug.append(f"\nfontes (ancoragem): {', '.join(sugestao['fontes'])}", style="dim italic")
        conf = min(detection.theme_confidence, detection.nature_confidence)
        titulo = f"💡 SUGESTÃO PRO ATENDENTE (Estágio 1: copiloto) · confiança {conf:.0%} · a IA sugere, você decide"
        blocos.append(Panel(sug, title=titulo, border_style="yellow", box=ROUNDED))
    else:
        blocos.append(Panel("Sem doc relevante na base — encaminhar a humano.",
                            title="SUGESTÃO", border_style="red", box=ROUNDED))

    # 5) roteamento
    label, cor = ACTION_LABEL.get(decisao.action, (decisao.action.value, "white"))
    rot = Text()
    rot.append("  ação: ", style="dim"); rot.append(f"{label}\n", style=f"bold {cor}")
    rot.append(f"  prioridade {decisao.priority:.2f}  ·  ", style="dim")
    rot.append(f"{decisao.reason}", style="dim")
    blocos.append(Panel(rot, title="roteamento (decision.py)", border_style="blue", box=ROUNDED))

    return Panel(Group(*blocos),
                 title=f"[{conv['id']}]  {conv['descricao']}", border_style="bright_blue", box=ROUNDED)


def main():
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    if not DATA.exists():
        console.print(f"[red]não achei {DATA}[/red]"); sys.exit(1)

    conversas = json.loads(DATA.read_text(encoding="utf-8"))
    alvo = sys.argv[1] if len(sys.argv) > 1 else None
    if alvo:
        conversas = [c for c in conversas if c["id"] == alvo]

    retriever = Retriever()                 # loga o modo (híbrido / BM25 fallback) aqui
    rag_mode = "modo híbrido" if retriever.mode == "hibrido" else "modo BM25 fallback"

    console.print(Panel(
        "COPILOTO DO ATENDIMENTO — mockup da Pergunta 5\n"
        "conversa → detecção objetiva → RAG → sugestão pro atendente + roteamento.\n"
        "Estágio 1 da escada de adoção: a IA sugere, o humano decide.",
        title="Product Ops · Jota", border_style="bright_blue", box=ROUNDED))

    for conv in conversas:
        messages, events, started_at = _adaptar(conv)
        detection = classify_conversation(messages, events, started_at)
        docs = retriever.retrieve(montar_query(conv, detection.predicted_theme), top_k=TOP_K)
        sugestao = montar_sugestao(detection.predicted_theme, docs)
        customer_text = " ".join(m["text"] for m in conv["mensagens"] if m["sender"] == "customer")
        decisao = decide(derivar_decisao(detection.predicted_theme, detection, docs, customer_text))
        console.print(render_conversa(conv, detection, docs, sugestao, decisao, rag_mode))


if __name__ == "__main__":
    main()
