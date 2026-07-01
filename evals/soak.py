"""Soak overnight — roda MUITAS conversas de clientes-LLM diversos, a noite toda.
=============================================================================
Cicla os casos × personas × estilos (informal, idoso, apressado, com typos…) pelo
cérebro real do bot + juiz-LLM, acumulando resultados. De manhã: relatório agregado
(desfecho certo %, grounding %, tom médio) + a LISTA DE FALHAS pra atacar.

Resiliente: cada conversa em try/except, resultado gravado incrementalmente (parcial
sobrevive se cair). Teto de custo: para em MAX_CONVOS ou no deadline (o que vier antes).

    .venv/bin/python evals/soak.py                 # padrão: 120 conversas ou 6h
    .venv/bin/python evals/soak.py 300 8           # 300 conversas ou 8h
    OPENAI_MODEL=gpt-4o-mini .venv/bin/python evals/soak.py 500 8   # 10x mais barato/volume
"""
from __future__ import annotations

import json
import random
import sys
import time
from collections import Counter
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "apps"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import simulate_llm_customers as sim   # reusa CASES, run_case (cérebro real + juiz)

RESULTS = ROOT / "data" / "soak_results.jsonl"
REPORT = ROOT / "data" / "soak_report.md"
MAX_CONVOS = int(sys.argv[1]) if len(sys.argv) > 1 else 120
DEADLINE_H = float(sys.argv[2]) if len(sys.argv) > 2 else 6.0

# estilos pra multiplicar a diversidade das personas (o cliente-LLM escreve assim)
STYLES = [
    "escreva de forma bem informal, com gírias do dia a dia",
    "você é idoso e tem pouca familiaridade com tecnologia; escreve devagar e simples",
    "você está com muita pressa e impaciência",
    "você escreve com vários erros de digitação e abreviações (vc, pq, tbm)",
    "você é formal e educado",
    "você é MEI e fala de trabalho o tempo todo",
]


def varied_case():
    base = dict(random.choice(sim.CASES))
    style = random.choice(STYLES)
    base["goal"] = base["goal"] + f" (ESTILO: {style})"
    base["id"] = base["id"] + "·" + style.split()[2][:6]
    return base


def summarize(rows):
    done = [r for r in rows if "error" not in r]
    n = len(done)
    if not n:
        return {"total": len(rows), "erros": len(rows) - n}
    ok = sum(r.get("outcome_ok") for r in done)
    gnd = sum(1 for r in done if r.get("grounded") is False)
    toms = [r["tom"] for r in done if isinstance(r.get("tom"), (int, float))]
    by = Counter(r["id"].split("·")[0] for r in done if not r.get("outcome_ok"))
    return {
        "total": len(rows), "avaliadas": n, "erros": len(rows) - n,
        "desfecho_certo_pct": round(100 * ok / n, 1),
        "grounding_limpo_pct": round(100 * (n - gnd) / n, 1),
        "tom_medio": round(sum(toms) / len(toms), 2) if toms else None,
        "falhas_por_caso": dict(by.most_common()),
    }


def write_report(rows, started):
    s = summarize(rows)
    fails = [r for r in rows if "error" not in r and (not r.get("outcome_ok") or r.get("grounded") is False)]
    lines = [
        "# Soak overnight — relatório", "",
        f"- início: {started}", f"- conversas: **{s['total']}** (avaliadas {s.get('avaliadas', 0)}, erros {s.get('erros', 0)})",
        f"- desfecho certo: **{s.get('desfecho_certo_pct', '—')}%**",
        f"- grounding limpo: **{s.get('grounding_limpo_pct', '—')}%**",
        f"- tom médio: **{s.get('tom_medio', '—')}/5**",
        f"- falhas por caso: {s.get('falhas_por_caso', {})}", "",
        f"## Falhas ({len(fails)}) — o que atacar", "",
    ]
    for r in fails[:80]:
        prob = "desfecho" if not r.get("outcome_ok") else "grounding"
        lines.append(f"- `{r['id']}` [{prob}] esperava {r.get('expect')}, deu {r.get('outcome')} — {r.get('nota', '')[:90]}")
    REPORT.write_text("\n".join(lines), encoding="utf-8")


def main():
    if sim.bot.LLM is None:
        print("✗ precisa de OPENAI_API_KEY no .env"); sys.exit(1)
    started = datetime.now().isoformat(timespec="seconds")
    deadline = time.time() + DEADLINE_H * 3600
    print(f"soak: até {MAX_CONVOS} conversas ou {DEADLINE_H}h · modelo {sim.MODEL} · início {started}")
    rows, i = [], 0
    with open(RESULTS, "w", encoding="utf-8") as f:
        while i < MAX_CONVOS and time.time() < deadline:
            case = varied_case()
            try:
                r = sim.run_case(case)
                r.pop("transcript", None)     # não guarda o transcript inteiro (fica leve)
            except Exception as e:
                r = {"id": case["id"], "error": f"{type(e).__name__}: {e}"}
            rows.append(r)
            f.write(json.dumps(r, ensure_ascii=False) + "\n"); f.flush()
            i += 1
            if i % 10 == 0:
                write_report(rows, started)
                s = summarize(rows)
                print(f"  [{i}] desfecho {s.get('desfecho_certo_pct')}% · grounding {s.get('grounding_limpo_pct')}% · tom {s.get('tom_medio')}")
    write_report(rows, started)
    print(f"✓ fim: {len(rows)} conversas · relatório em {REPORT}")


if __name__ == "__main__":
    main()
