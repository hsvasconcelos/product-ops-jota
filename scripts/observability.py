"""Observabilidade — o resumo do trace de decisões do motor (data/traces.jsonl).
=============================================================================
Cada turno do bot vira uma linha no trace (store facts); aqui a gente DERIVA a
visão: taxa de contenção, distribuição de ação/tema, blocks do guardrail, e as
últimas decisões. É o "como está a operação agora" — debugável e mensurável.

Uso:
    .venv/bin/python scripts/observability.py            # resumo + últimas 10
    .venv/bin/python scripts/observability.py --tail 30  # últimas N
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from rich.box import ROUNDED, SIMPLE_HEAVY
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from product_ops_jota.trace import load, summarize

console = Console()


def main():
    n_tail = 10
    if "--tail" in sys.argv:
        try:
            n_tail = int(sys.argv[sys.argv.index("--tail") + 1])
        except Exception:
            pass
    s = summarize()
    if not s.get("n"):
        console.print("[yellow]Sem traces ainda. Use o bot (ou o simulador) e volte aqui.[/yellow]")
        return

    console.print(Panel(
        f"[bold]{s['n']}[/bold] decisões registradas   ·   "
        f"contenção IA [bold green]{s['pct_contido']}%[/bold green]   ·   "
        f"humano [bold]{s['pct_humano']}%[/bold]   ·   "
        f"confiança média [bold]{s['conf_media']}[/bold]",
        title="OBSERVABILIDADE · motor de atendimento", border_style="cyan", box=ROUNDED))

    a = Table(box=SIMPLE_HEAVY, title="Ações"); a.add_column("ação"); a.add_column("n", justify="right")
    for k, v in s["acoes"].items():
        a.add_row(str(k), str(v))
    t = Table(box=SIMPLE_HEAVY, title="Temas"); t.add_column("tema"); t.add_column("n", justify="right")
    for k, v in s["temas"].items():
        t.add_row(str(k), str(v))
    g = Table(box=SIMPLE_HEAVY, title="Guardrail de saída"); g.add_column("resultado"); g.add_column("n", justify="right")
    for k, v in (s["guardrail"] or {"—": 0}).items():
        g.add_row(str(k), str(v))
    console.print(a); console.print(t); console.print(g)

    rows = load()[-n_tail:]
    lt = Table(box=SIMPLE_HEAVY, expand=True, title=f"Últimas {len(rows)} decisões")
    lt.add_column("hora", style="dim"); lt.add_column("cliente"); lt.add_column("tema")
    lt.add_column("ação"); lt.add_column("guardrail", justify="center")
    for r in rows:
        lt.add_row(r.get("ts", "")[11:19], (r.get("cliente_msg") or "")[:34],
                   r.get("tema", "—"), r.get("acao", "—"), r.get("guardrail") or "—")
    console.print(lt)


if __name__ == "__main__":
    main()
