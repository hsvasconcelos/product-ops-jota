"""Harness de eval — UM comando, UM scorecard, com PASS/FAIL.
=============================================================================
Consolida os 3 evals (detecção · retrieval · decisão) num painel único com
limiares de regressão. É a resposta a "como sei que o sistema funciona?" — e a
guarda que quebra se um ajuste de policy regredir a qualidade.

Cada métrica tem um LIMIAR (o contrato). Verde = passou, vermelho = regrediu.
Escreve data/eval_scorecard.json (o deck/lab lê daqui).

Uso:
    .venv/bin/python evals/run_all.py
    .venv/bin/python evals/run_all.py --full   # classificador nas 10k (lento); padrão amostra 2000
"""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from sklearn.metrics import accuracy_score, f1_score
from rich.box import ROUNDED, SIMPLE_HEAVY
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from product_ops_jota.rag import Retriever
from run_classifier_eval import coletar as classifier_coletar
from run_rag_eval import metrics as rag_metrics
from run_decision_eval import coletar as decision_coletar
from run_outcome_eval import coletar as outcome_coletar

DB = ROOT / "data" / "jota_support.db"
OUT = ROOT / "data" / "eval_scorecard.json"
console = Console()

# LIMIARES (o contrato de qualidade — regressão abaixo disso = vermelho)
THRESHOLDS = {
    "deteccao_natureza": 0.95,
    "deteccao_tema": 0.80,
    "rag_hit_at_k": 0.80,
    "rag_mrr": 0.70,
    "decisao_acuracia": 0.95,
    "decisao_divergencias": 0,     # exato: zero divergências com o julgamento de produto
    "desfecho_binario": 0.80,      # resolvido × não-resolvido vs gabarito (fechado ≠ resolvido)
}


def run(sample: int | None):
    retriever = Retriever()   # um modelo, reusado por detecção (tema semântico) e RAG

    # 1) DETECÇÃO (classifier vs gabarito) — tema SEMÂNTICO (o motor real)
    conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    try:
        n_det, y = classifier_coletar(conn, retriever=retriever, limit=sample)
    finally:
        conn.close()
    nat = accuracy_score(y["nature_true"], y["nature_pred"])
    tema = accuracy_score(y["theme_true"], y["theme_pred"])
    human_f1 = f1_score(y["human_true"], y["human_pred"], zero_division=0)

    # 2) RETRIEVAL (RAG vs gabarito de queries)
    rag = rag_metrics(retriever)

    # 3) DECISÃO (roteador vs julgamento de produto curado)
    rows = decision_coletar()
    dec_acc = sum(1 for _, exp, got, _ in rows if exp == got) / len(rows)
    divergencias = [(cid, exp, got) for cid, exp, got, _ in rows if exp != got]

    # 4) DESFECHO (chamado fechado ≠ atrito resolvido) — binário resolvido × não-resolvido
    conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    try:
        n_out, o_true, o_pred, _ = outcome_coletar(conn)
    finally:
        conn.close()
    bin_ = lambda c: c == "resolved"
    out_bin = sum(1 for t, p in zip(o_true, o_pred) if bin_(t) == bin_(p)) / n_out

    return {
        "amostra_deteccao": n_det,
        "metrics": {
            "deteccao_natureza": round(nat, 4),
            "deteccao_tema": round(tema, 4),
            "deteccao_pediu_humano_f1": round(human_f1, 4),
            "rag_modo": rag["mode"],
            "rag_hit_at_k": round(rag["hit_at_k"], 4),
            "rag_p_at_1": round(rag["p_at_1"], 4),
            "rag_mrr": round(rag["mrr"], 4),
            "rag_queries": rag["n"],
            "decisao_acuracia": round(dec_acc, 4),
            "decisao_cenarios": len(rows),
            "decisao_divergencias": len(divergencias),
            "desfecho_binario": round(out_bin, 4),
            "desfecho_amostra": n_out,
        },
        "divergencias": divergencias,
    }


def _pass(key, val) -> bool:
    thr = THRESHOLDS[key]
    return val <= thr if key == "decisao_divergencias" else val >= thr


def render(res):
    m = res["metrics"]
    scorecard = [
        ("Detecção · natureza", "deteccao_natureza", m["deteccao_natureza"], "acurácia vs gabarito"),
        ("Detecção · tema", "deteccao_tema", m["deteccao_tema"], f"semântico · amostra {res['amostra_deteccao']}"),
        ("Retrieval · Hit@3", "rag_hit_at_k", m["rag_hit_at_k"], f"{m['rag_modo']} · {m['rag_queries']} queries"),
        ("Retrieval · MRR", "rag_mrr", m["rag_mrr"], "ranking do doc certo"),
        ("Decisão · acurácia", "decisao_acuracia", m["decisao_acuracia"], f"{m['decisao_cenarios']} cenários"),
        ("Decisão · divergências", "decisao_divergencias", m["decisao_divergencias"], "vs julgamento de produto"),
        ("Desfecho · resolvido×não", "desfecho_binario", m["desfecho_binario"],
         f"fechado ≠ resolvido · {m['desfecho_amostra']} conversas"),
    ]
    t = Table(box=SIMPLE_HEAVY, expand=True, title="SCORECARD — qualidade do motor (com limiares de regressão)")
    t.add_column("Métrica"); t.add_column("Valor", justify="right")
    t.add_column("Limiar", justify="right", style="dim"); t.add_column("", justify="center")
    t.add_column("O que mede", style="dim")
    all_pass = True
    for nome, key, val, desc in scorecard:
        ok = _pass(key, val)
        all_pass = all_pass and ok
        is_int = key == "decisao_divergencias"
        vtxt = str(val) if is_int else f"{val*100:.1f}%"
        thr = THRESHOLDS[key]
        ttxt = ("=" if is_int else "≥") + (str(thr) if is_int else f"{thr*100:.0f}%")
        t.add_row(nome, Text(vtxt, style="bold " + ("green" if ok else "red")),
                  ttxt, Text("✓" if ok else "✗", style="green" if ok else "bold red"), desc)
    console.print(Panel("Um comando → um scorecard. Cada linha é um contrato; vermelho = regrediu.",
                        title="EVAL HARNESS · Jota", border_style="cyan", box=ROUNDED))
    console.print(t)
    veredito = ("✓ TODOS os contratos passaram — o motor está dentro do esperado."
                if all_pass else "✗ REGRESSÃO — uma métrica caiu abaixo do limiar (ver vermelho).")
    console.print(Panel(veredito, border_style="green" if all_pass else "red", box=ROUNDED))
    if res["divergencias"]:
        console.print("  divergências de decisão: " +
                      ", ".join(f"{c}({e}→{g})" for c, e, g in res["divergencias"]))
    return all_pass


def main():
    if not DB.exists():
        console.print("[red]banco não encontrado — rode: python scripts/build_db.py[/red]"); sys.exit(1)
    sample = None if "--full" in sys.argv else 2000
    res = run(sample)
    all_pass = render(res)
    OUT.write_text(json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8")
    console.print(f"  scorecard salvo em {OUT}")
    sys.exit(0 if all_pass else 1)


if __name__ == "__main__":
    main()
