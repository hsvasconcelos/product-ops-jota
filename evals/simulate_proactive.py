"""Teste em volume dos cenários PROATIVOS (/demo-*) — pega os erros antes da demo.
=============================================================================
Roda cada /demo-* pelo fluxo REAL (armar o evento → cliente abre o chat → opener) e
checa, por cenário:
  · natureza esperada (evento de sistema / comportamento / ausência)
  · doc ancorado certo (ex.: duplicidade → KB-ESTORNO, não KB-BOLETO)
  · guardrail PASSOU (opener grounded, não caiu no fallback)
  · opener NÃO se re-apresenta (a saudação já vai separada) e não vaza "seu banco"

É o análogo do simulate_conversations, pro Mundo 2 (proativo).

    .venv/bin/python evals/simulate_proactive.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "apps"))

from rich.box import ROUNDED, SIMPLE_HEAVY
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

import telegram_bot as bot
from product_ops_jota.trace import load as trace_load

console = Console()

# o que ESPERAR de cada demo (tema · natureza · prefixo do doc ancorado)
EXPECT = {
    "/demo-kyc": {"theme": "kyc", "nature": "system_signaled", "doc": "KB-KYC"},
    "/demo-estorno": {"theme": "boleto", "nature": "system_signaled", "doc": "KB-ESTORNO"},
    "/demo-boleto": {"theme": "boleto", "nature": "system_signaled", "doc": "KB-ESTORNO"},
    "/demo-openfinance": {"theme": "yield_open_finance", "nature": "system_signaled", "doc": "KB-CONECTA"},
    "/demo-falatap": {"theme": "fala_tap", "nature": "behavior_inferred", "doc": "KB-FALATAP"},
    "/demo-pix": {"theme": "pix", "nature": "behavior_inferred", "doc": "KB-PIX"},
    "/demo-limbo": {"theme": "kyc", "nature": "absence_detected", "doc": "KB-"},
}
# temas onde referenciar um banco EXTERNO é legítimo (não é tratar o Jota como "outro banco")
EXTERNAL_BANK_OK = {"yield_open_finance"}
GREET_MARK = "sou a"     # opener NÃO deve se apresentar de novo


def run_demo(cmd):
    cid = 900000 + abs(hash(cmd)) % 1000
    bot.SESSIONS.pop(cid, None)
    bot.DEBUG.discard(cid)
    sent = []
    bot.send = lambda c, t, **k: sent.append(t)
    bot.typing = lambda c: None
    bot.handle_command(cid, cmd, "Teste")     # arma o cenário (banner)
    sent.clear()
    bot.handle_message(cid, "oi, e agora?", "Teste")   # cliente abre o chat
    # opener = último send que não é debug (após a saudação)
    non_debug = [s for s in sent if not s.startswith("🕵")]
    opener = non_debug[-1] if non_debug else ""
    tr = trace_load()[-1] if trace_load() else {}
    return opener, tr


def main():
    demos = {c: s for c, s in bot.COMMANDS.items() if c.startswith("/demo-")}
    console.print(Panel(f"Rodando [bold]{len(demos)}[/bold] cenários proativos (/demo-*) pelo fluxo real.",
                        title="TESTE EM VOLUME · Mundo 2 (proativo)", border_style="cyan", box=ROUNDED))

    t = Table(box=SIMPLE_HEAVY, expand=True, title="Resultado por cenário proativo")
    t.add_column("demo"); t.add_column("tema"); t.add_column("natureza"); t.add_column("doc")
    t.add_column("guardrail", justify="center"); t.add_column("sem re-saudação", justify="center")
    t.add_column("ok", justify="center")
    fails = []
    for cmd in demos:
        exp = EXPECT.get(cmd, {})
        opener, tr = run_demo(cmd)
        theme, nature, doc = tr.get("tema", "—"), tr.get("natureza", "—"), tr.get("fonte") or "—"
        guard = tr.get("guardrail", "—")
        theme_ok = theme == exp.get("theme")
        nat_ok = nature == exp.get("nature")
        doc_ok = str(doc).startswith(exp.get("doc", "\0"))
        guard_ok = guard == "pass"
        greet_ok = GREET_MARK not in opener.lower()
        clean = ("seu banco" not in opener.lower()) or (theme in EXTERNAL_BANK_OK)
        ok = theme_ok and nat_ok and doc_ok and guard_ok and greet_ok and clean
        if not ok:
            fails.append((cmd, theme, theme_ok, nature, nat_ok, doc, doc_ok, guard, greet_ok, clean))
        mk = lambda b: Text("✓", style="green") if b else Text("✗", style="bold red")
        t.add_row(cmd, Text(theme, style="" if theme_ok else "red"),
                  Text(nature, style="" if nat_ok else "red"), Text(str(doc), style="" if doc_ok else "red"),
                  Text(guard, style="green" if guard_ok else "red"), mk(greet_ok), mk(ok))
    console.print(t)
    n, nok = len(demos), len(demos) - len(fails)
    console.print(Panel(("✓ " if not fails else "✗ ") + f"OK: [bold]{nok}/{n}[/bold] cenários proativos",
                        border_style="green" if not fails else "red", box=ROUNDED))
    for cmd, th, tho, nat, nato, doc, doco, g, gr, cl in fails:
        prob = []
        if not tho: prob.append(f"tema={th}")
        if not nato: prob.append(f"natureza={nat}")
        if not doco: prob.append(f"doc={doc}")
        if g != "pass": prob.append(f"guardrail={g}")
        if not gr: prob.append("re-saudou")
        if not cl: prob.append("vazou 'seu banco'")
        console.print(f"  [red]✗[/red] {cmd}: " + " · ".join(prob))


if __name__ == "__main__":
    main()
