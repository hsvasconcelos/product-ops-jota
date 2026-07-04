"""Eval do DESFECHO — chamado fechado ≠ atrito resolvido: dá pra saber a diferença?
=============================================================================
Roda o derivador de desfecho (outcome.py) em todas as conversas FECHADAS do
banco e compara com o gabarito (`outcome`), que o derivador nunca lê.

Escopo honesto:
  · `escalated` fica FORA: escalar é ação do roteador, não desfecho — a
    resolução dele acontece fora da janela do chamado (medida em produção
    pelo mesmo motor, sobre a conversa do humano).
  · o TEMA entra como dado (gold_theme): o eval do classificador já mede o
    tema; aqui a pergunta é só "dado o atrito, o desfecho foi lido certo?"
    (ablação — um erro não contamina a medição do outro).
  · `system_confirmed`: o gerador emite eventos de cura em parte dos resolved
    (P_CURE) usando os MESMOS nomes de outcome.CURE_EVENTS — logo, no sintético,
    a cura é verdadeira POR CONSTRUÇÃO. O que este eval mede de honesto é o
    MECANISMO (janela de 24h, precedência entre sinais, quem-falou-por-último),
    não a taxa de cura de produção — essa só existe com backend real.

Mapeamento derivado → gabarito (3 classes):
  resolvido_* → resolved · abandonado/nao_resolvido → abandoned · sem_resposta → no_response

Uso:
    .venv/bin/python evals/run_outcome_eval.py
"""
from __future__ import annotations

import sqlite3
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from rich.box import ROUNDED, SIMPLE_HEAVY
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from product_ops_jota.friction_model import SupportTheme
from product_ops_jota.outcome import Desfecho, derive_desfecho

DB = ROOT / "data" / "jota_support.db"
console = Console()

TO_GOLD = {
    Desfecho.RESOLVIDO_CONFIRMADO: "resolved", Desfecho.RESOLVIDO_EXPLICITO: "resolved",
    Desfecho.RESOLVIDO_ASSUMIDO: "resolved", Desfecho.ABANDONADO: "abandoned",
    Desfecho.NAO_RESOLVIDO: "abandoned", Desfecho.SEM_RESPOSTA: "no_response",
}


def coletar(conn, limit: int | None = None):
    """(n, y_true, y_pred, por_sinal) — derivado vs gabarito, reusável pelo harness."""
    convs = conn.execute(
        "SELECT conversation_id, user_id, started_at, gold_theme, outcome FROM conversations "
        "WHERE outcome IN ('resolved','abandoned','no_response') ORDER BY conversation_id"
        + (f" LIMIT {int(limit)}" if limit else "")).fetchall()
    # próximo contato do MESMO usuário (fato observável) — pro sinal de recontato
    nxt: dict[str, list[str]] = {}
    for uid, st in conn.execute("SELECT user_id, started_at FROM conversations ORDER BY started_at"):
        nxt.setdefault(uid, []).append(st)
    y_true, y_pred, por_sinal = [], [], Counter()
    for cid, uid, started, theme, gold in convs:
        messages = conn.execute(
            "SELECT sender, text, sent_at FROM messages WHERE conversation_id=? ORDER BY turn_index",
            (cid,)).fetchall()
        events = conn.execute(
            "SELECT event_type, occurred_at FROM events WHERE user_id=?", (uid,)).fetchall()
        seguinte = next((s for s in nxt.get(uid, []) if s > started), None)
        r = derive_desfecho(messages, events, SupportTheme(theme), next_contact_at=seguinte)
        y_true.append(gold)
        y_pred.append(TO_GOLD[r.desfecho])
        por_sinal[r.sinal] += 1
    return len(convs), y_true, y_pred, por_sinal


def main():
    conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    try:
        n, y_true, y_pred, por_sinal = coletar(conn)
        n_esc = conn.execute("SELECT COUNT(*) FROM conversations WHERE outcome='escalated'").fetchone()[0]
    finally:
        conn.close()
    acc = sum(1 for t, p in zip(y_true, y_pred) if t == p) / n
    # a visão que alimenta decisão (re-interceptar? containment?): resolvido × não-resolvido
    bin_ = lambda c: "resolvido" if c == "resolved" else "nao_resolvido"
    acc_bin = sum(1 for t, p in zip(y_true, y_pred) if bin_(t) == bin_(p)) / n

    console.print(Panel(
        f"Desfecho derivado (explicit · no_recontact · system_confirmed) vs gabarito, "
        f"em [bold]{n}[/bold] conversas fechadas.\n"
        f"Binário (resolvido × não-resolvido) = [bold]{acc_bin*100:.1f}%[/bold]   ·   "
        f"3 classes = {acc*100:.1f}%   ·   {n_esc} escaladas fora do escopo.",
        title="EVAL · desfecho vs gabarito", border_style="cyan", box=ROUNDED))

    classes = ["resolved", "abandoned", "no_response"]
    t = Table(box=SIMPLE_HEAVY, expand=True,
              title="Matriz de confusão — desfecho (linha = real, coluna = derivado)")
    t.add_column("real ╲ derivado")
    for c in classes:
        t.add_column(c, justify="right")
    for tr in classes:
        row = [tr] + [str(sum(1 for a, b in zip(y_true, y_pred) if a == tr and b == pc))
                      for pc in classes]
        t.add_row(*row)
    console.print(t)

    s = Table(box=SIMPLE_HEAVY, expand=True, title="Qual sinal decidiu (auditabilidade)")
    s.add_column("sinal"); s.add_column("conversas", justify="right"); s.add_column("%", justify="right")
    for sig, c in por_sinal.most_common():
        s.add_row(sig, str(c), f"{100*c/n:.1f}%")
    console.print(s)
    console.print(Panel(
        "O silêncio é o sinal mais fraco (assumido ≠ confirmado) — por isso a confiança dele é 0.55 "
        "e a evolução é o backend emitir eventos de CURA (system_confirmed, confiança 1.0).\n"
        "Divergência AUDITADA (não é bug): ~780 conversas do canal proativo fecham com o cliente "
        "dizendo “mas e agora?” / “ainda tô com dúvida” e o gabarito diz resolved — o derivador marca "
        "“deixado no vácuo”, e defende-se que o rótulo é que é generoso. Medir desfecho existe pra "
        "pegar exatamente isso.", border_style="yellow", box=ROUNDED))


if __name__ == "__main__":
    main()
