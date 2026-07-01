"""Métricas de interceptação (Pergunta 4 do case).
=============================================================================
Simula o PROGRAMA de interceptação rodando o pipeline completo
(classifier → RAG → decision) sobre as 1001 conversas do laboratório, e
consolida os KPIs que o case pede:
  · # intercepts feitos
  · % de casos resolvidos pela IA (containment)
  · tempo médio de interceptação (latência do motor) vs SLA reativo real
mais os recortes operacionais (por ação, tema, natureza, segmento) que dizem
ONDE concentrar o cuidado e quanta carga sobra pro time humano.

Honestidade: os 4 sinais do decisor vêm de heurística por tema (como no
copiloto), então "% resolvido pela IA" é a % que o ROTEADOR encaminharia à IA
— não um outcome medido de sucesso (não há gold de "resolveu de fato"). E a
latência é de processo único em Python: serve pra mostrar a ORDEM DE GRANDEZA
(ms vs horas de espera), não um SLA de produção.

Uso:
    python scripts/metricas_interceptacao.py
"""
from __future__ import annotations

import sqlite3
import sys
from collections import Counter
from pathlib import Path
from time import perf_counter

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "apps"))

import copiloto as C  # reusa THEME_*, derivar_decisao, montar_query, LINE_LABEL
from product_ops_jota.classifier import classify_conversation, load_raw_conversation
from product_ops_jota.decision import decide
from product_ops_jota.channels import proactive_line
from product_ops_jota.rag import Retriever

from rich.box import ROUNDED, SIMPLE_HEAVY
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

DB = ROOT / "data" / "jota_support.db"
AI_ACTIONS = {"ai_resolve", "ai_resolve_silent"}
console = Console()


def sla_reativo(conn):
    """Baseline reativo (médio, pior) em horas — a espera que a interceptação evita."""
    return conn.execute(
        """WITH g AS (SELECT conversation_id, sender, sent_at,
                  LAG(sent_at) OVER (PARTITION BY conversation_id ORDER BY turn_index) prev,
                  LAG(sender)  OVER (PARTITION BY conversation_id ORDER BY turn_index) ps
           FROM messages)
           SELECT ROUND(AVG((strftime('%s',sent_at)-strftime('%s',prev))/3600.0),1),
                  ROUND(MAX((strftime('%s',sent_at)-strftime('%s',prev))/3600.0),1)
           FROM g WHERE ps='customer' AND sender='human_agent'"""
    ).fetchone()


def coletar(conn):
    seg_map = dict(conn.execute("SELECT conversation_id, u.segment FROM conversations c "
                                "JOIN users u ON u.user_id=c.user_id").fetchall())
    ids = [r[0] for r in conn.execute("SELECT conversation_id FROM conversations").fetchall()]
    r = Retriever()
    por_acao, por_tema_humano, por_natureza, por_linha = Counter(), Counter(), Counter(), Counter()
    latencias = []
    for cid in ids:
        messages, events, started_at = load_raw_conversation(conn, cid)
        t0 = perf_counter()
        det = classify_conversation(messages, events, started_at)
        conv = {"mensagens": [{"sender": s, "text": t} for s, t, _ in messages],
                "segment": seg_map.get(cid, "pf")}
        docs = r.retrieve(C.montar_query(conv, det.predicted_theme), top_k=C.TOP_K)
        ct = " ".join(m["text"] for m in conv["mensagens"] if m["sender"] == "customer")
        dec = decide(C.derivar_decisao(det.predicted_theme, det, docs, ct))
        latencias.append(perf_counter() - t0)

        por_acao[dec.action.value] += 1
        por_natureza[(det.predicted_nature.value, dec.action.value in AI_ACTIONS)] += 1
        if dec.action.value == "human_handoff":
            por_tema_humano[det.predicted_theme.value] += 1
        por_linha[proactive_line(conv["segment"]).value] += 1
    return {"n": len(ids), "por_acao": por_acao, "por_tema_humano": por_tema_humano,
            "por_natureza": por_natureza, "por_linha": por_linha,
            "latencia_ms": sum(latencias) / len(latencias) * 1000, "mode": r.mode}


def render(d, sla):
    n = d["n"]
    intercepts = n - d["por_acao"].get("no_intercept", 0)
    ia = sum(d["por_acao"].get(a, 0) for a in AI_ACTIONS)
    humano = d["por_acao"].get("human_handoff", 0)
    assist = d["por_acao"].get("ai_assist", 0)

    topo = Table(box=SIMPLE_HEAVY, expand=True, title="KPIs do programa de interceptação")
    topo.add_column("Indicador"); topo.add_column("Valor", justify="right", style="bold"); topo.add_column("Leitura", style="dim")
    topo.add_row("# intercepts feitos", f"{intercepts}/{n}", f"{intercepts/n*100:.0f}% das conversas viram ação")
    topo.add_row("% resolvido pela IA (containment)", f"{ia/n*100:.1f}%", "resolve/silent — sem tocar no time")
    topo.add_row("% assistido (IA + humano no loop)", f"{assist/n*100:.1f}%", "copiloto: sugere, humano envia")
    topo.add_row("% escalado p/ humano", f"{humano/n*100:.1f}%", "a carga real na fila humana")
    topo.add_row("tempo médio de interceptação", f"{d['latencia_ms']:.1f} ms", f"vs SLA reativo: médio {sla[0]}h, pior {sla[1]}h")

    acoes = Table(box=SIMPLE_HEAVY, expand=True, title="Distribuição de ações (a lógica da interceptação em escala)")
    acoes.add_column("Ação"); acoes.add_column("N", justify="right", style="bold"); acoes.add_column("%", justify="right")
    for a, _ in d["por_acao"].most_common():
        label = C.ACTION_LABEL.get(next(k for k in C.ACTION_LABEL if k.value == a), (a, ""))[0]
        acoes.add_row(label, str(d["por_acao"][a]), f"{d['por_acao'][a]/n*100:.1f}%")

    carga = Table(box=SIMPLE_HEAVY, expand=True, title="Onde a fila humana concentra (priorizar especialidade)")
    carga.add_column("Tema"); carga.add_column("→ humano", justify="right", style="bold")
    for tema, q in d["por_tema_humano"].most_common(5):
        carga.add_row(tema, str(q))

    linha = Table(box=SIMPLE_HEAVY, expand=True, title="Linha proativa de destino (os 3 números)")
    linha.add_column("Linha"); linha.add_column("N", justify="right", style="bold")
    for ln, q in d["por_linha"].most_common():
        linha.add_row(C.LINE_LABEL.get(next(k for k in C.LINE_LABEL if k.value == ln), ln), str(q))

    console.print(Panel(
        f"Programa de interceptação simulado em [bold]{n}[/bold] conversas — pipeline completo "
        f"(classifier → RAG [{d['mode']}] → decision).\n"
        "Honestidade: % pela IA = o que o ROTEADOR encaminharia (heurística por tema), não outcome medido.",
        title="MÉTRICAS DE INTERCEPTAÇÃO · Pergunta 4", border_style="cyan", box=ROUNDED))
    console.print(topo); console.print(acoes); console.print(carga); console.print(linha)


def main():
    if not DB.exists():
        console.print("[red]banco não encontrado — rode: python scripts/build_db.py[/red]"); sys.exit(1)
    conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    try:
        render(coletar(conn), sla_reativo(conn))
    finally:
        conn.close()


if __name__ == "__main__":
    main()
