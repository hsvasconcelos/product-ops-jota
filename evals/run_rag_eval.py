"""Eval do RAG — mede a qualidade do retrieval contra um gabarito de queries.
=============================================================================
Roda o Retriever num conjunto de queries reais de cliente (retrieval_golden.json),
cada uma com o(s) doc(s) relevante(s) esperado(s), e reporta as métricas
clássicas de ranking:
  · Hit@k     — o doc certo apareceu no top-k?
  · Precision@1 — o top-1 já é o doc certo?
  · MRR       — Mean Reciprocal Rank (1/posição do 1º relevante)

Retrieval é MEDIÇÃO, não especificação (igual à detecção): o número honesto
importa mais que 100%. Misses são listados — em modo BM25 fallback, queries
parafraseadas podem errar, e é exatamente o que a camada densa resolveria.
O gabarito é dado (queries + doc certo), não a implementação — não é circular.

Uso:
    python evals/run_rag_eval.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from rich.box import ROUNDED, SIMPLE_HEAVY
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from product_ops_jota.rag import Retriever

GOLDEN = Path(__file__).resolve().parent / "retrieval_golden.json"
TOP_K = 3
console = Console()


def rank_of_relevant(docs, relevant) -> int | None:
    """Posição (1-based) do 1º doc relevante no ranking, ou None se ausente."""
    for i, d in enumerate(docs, 1):
        if d.id in relevant:
            return i
    return None


def metrics(retriever=None):
    """Métricas de ranking (Hit@k, P@1, MRR) — reusável pelo harness."""
    queries = json.loads(GOLDEN.read_text(encoding="utf-8"))
    r = retriever or Retriever()
    rows, hits, p1, rr_sum = [], 0, 0, 0.0
    for q in queries:
        docs = r.retrieve(q["query"], top_k=TOP_K)
        rank = rank_of_relevant(docs, set(q["relevant"]))
        hit = rank is not None
        top1 = docs[0].id if docs else "—"
        hits += int(hit)
        p1 += int(top1 in q["relevant"])
        rr_sum += (1.0 / rank) if rank else 0.0
        rows.append((q["query"], q["relevant"][0], top1, rank, hit))
    n = len(queries)
    return {"n": n, "mode": r.mode, "hit_at_k": hits / n, "p_at_1": p1 / n,
            "mrr": rr_sum / n, "rows": rows}


def main():
    m = metrics()
    r_mode = m["mode"]
    mode = "modo híbrido" if r_mode == "hibrido" else "modo BM25 fallback"
    rows, hits, p1, rr_sum, n = m["rows"], m["hit_at_k"] * m["n"], m["p_at_1"] * m["n"], m["mrr"] * m["n"], m["n"]
    console.print(Panel(
        f"RAG ({mode}) avaliado em [bold]{n}[/bold] queries reais de cliente, top-{TOP_K}.\n"
        f"Hit@{TOP_K} = {hits/n*100:.1f}%   ·   Precision@1 = {p1/n*100:.1f}%   ·   MRR = {rr_sum/n:.3f}",
        title="EVAL · retrieval vs gabarito", border_style="cyan", box=ROUNDED))

    t = Table(box=SIMPLE_HEAVY, expand=True, title=f"Por query (top-{TOP_K})")
    t.add_column("query"); t.add_column("doc certo", style="dim")
    t.add_column("top-1 devolvido"); t.add_column("rank", justify="right"); t.add_column("hit", justify="center")
    for query, gold, top1, rank, hit in rows:
        marca = Text("✓", style="green") if hit else Text("✗", style="bold red")
        t.add_row(query[:44], gold, top1, str(rank or "—"), marca)
    console.print(t)

    misses = [r_ for r_ in rows if not r_[4]]
    if misses:
        console.print(Panel(
            "\n".join(f"  ✗ \"{q[:50]}\"  → esperava {g}, top-1 veio {t1}" for q, g, t1, _, _ in misses)
            + "\n\n  (BM25 é léxico; a camada densa/RRF — dormente offline — é o que resolve fraseado.)",
            title=f"MISSES ({len(misses)}/{n}) — honestidade do retrieval", border_style="yellow", box=ROUNDED))
    else:
        console.print(Panel("✓ Hit@k perfeito neste gabarito.", border_style="green", box=ROUNDED))


if __name__ == "__main__":
    main()
