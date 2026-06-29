"""Eval do ROTEADOR de interceptação — mede decision.py contra o gabarito.
=============================================================================
Espelha o eval do classificador, agora pra a DECISÃO. Roda decide() num
conjunto curado de cenários (evals/decision_golden.json) + nos 3 casos-herói
(via decide_for_case, com baseline_action como gabarito) e reporta:
  · acurácia da ação
  · precision/recall/F1 por ação (scikit-learn)
  · matriz de confusão
  · lista EXPLÍCITA de divergências (esperado vs decidido + o porquê do decide)

Honestidade: o gabarito codifica JULGAMENTO DE PRODUTO ("o que deveria
acontecer"), independente da implementação. Se um limiar for recalibrado e
quebrar uma expectativa, ela aparece aqui — é um guarda de regressão da policy,
não um número decorativo.

Uso:
    python evals/run_decision_eval.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from sklearn.metrics import (
    accuracy_score, confusion_matrix, f1_score, precision_recall_fscore_support,
)
from rich.box import ROUNDED, SIMPLE_HEAVY
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from product_ops_jota.decision import DecisionInput, decide, decide_for_case
from product_ops_jota.friction_model import HERO_CASES

GOLDEN = Path(__file__).resolve().parent / "decision_golden.json"
# resolubilidade dos casos-herói (vem de fora dos 4 fatos do card — há KB/tool?)
HERO_RESOLVABILITY = {"pix_key_loop": 0.85, "kyc_failed_onboarding": 0.45, "fala_tap_receipt_anxiety": 0.90}
console = Console()


def coletar():
    """(id, esperado, decidido, reason) para cada cenário do gabarito + herói."""
    rows = []
    for s in json.loads(GOLDEN.read_text(encoding="utf-8")):
        d = decide(DecisionInput(criticality=s["criticality"], trust_risk=s["trust_risk"],
                                 resolvability=s["resolvability"], detection_confidence=s["detection_confidence"]))
        rows.append((s["id"], s["expected_action"], d.action.value, d.reason))
    for c in HERO_CASES:
        d = decide_for_case(c, HERO_RESOLVABILITY[c.case_id])
        rows.append((f"hero:{c.case_id}", c.baseline_action.value, d.action.value, d.reason))
    return rows


def tabela_metricas(y_true, y_pred):
    labels = sorted(set(y_true) | set(y_pred))
    p, r, f1, sup = precision_recall_fscore_support(y_true, y_pred, labels=labels, zero_division=0)
    acc = accuracy_score(y_true, y_pred)
    macro = f1_score(y_true, y_pred, labels=labels, average="macro", zero_division=0)
    t = Table(box=SIMPLE_HEAVY, expand=True,
              title=f"AÇÃO — acurácia {acc*100:.1f}%  ·  F1-macro {macro*100:.1f}%")
    t.add_column("Ação"); t.add_column("Precision", justify="right")
    t.add_column("Recall", justify="right"); t.add_column("F1", justify="right")
    t.add_column("Suporte", justify="right", style="dim")
    for i, lab in enumerate(labels):
        cor = "green" if f1[i] >= 0.8 else ("yellow" if f1[i] >= 0.5 else "red")
        t.add_row(lab, f"{p[i]:.2f}", f"{r[i]:.2f}", Text(f"{f1[i]:.2f}", style=cor), str(int(sup[i])))
    return t, labels


def matriz_confusao(y_true, y_pred, labels):
    cm = confusion_matrix(y_true, y_pred, labels=labels)
    t = Table(box=SIMPLE_HEAVY, expand=True, title="Matriz de confusão (linha = esperado, coluna = decidido)")
    t.add_column("esper ╲ decid", style="bold")
    for lab in labels:
        t.add_column(lab[:11], justify="right")
    for i, lab in enumerate(labels):
        row = [lab[:16]]
        for j in range(len(labels)):
            v = cm[i][j]
            cell = Text(str(v), style="bold green") if (i == j and v) else (Text(str(v), style="red") if v else "0")
            row.append(cell)
        t.add_row(*row)
    return t


def main():
    rows = coletar()
    y_true = [r[1] for r in rows]
    y_pred = [r[2] for r in rows]
    mismatches = [r for r in rows if r[1] != r[2]]

    console.print(Panel(
        f"Roteador (decision.py) avaliado em [bold]{len(rows)}[/bold] cenários "
        f"({len(rows)-len(HERO_CASES)} do gabarito curado + {len(HERO_CASES)} casos-herói).\n"
        "Gabarito = julgamento de produto, independente dos limiares. Guarda de regressão da policy.",
        title="EVAL · roteamento de interceptação vs gabarito", border_style="cyan", box=ROUNDED))

    t, labels = tabela_metricas(y_true, y_pred)
    console.print(t)
    console.print(matriz_confusao(y_true, y_pred, labels))

    if mismatches:
        mt = Table(box=SIMPLE_HEAVY, expand=True, title=f"⚠ DIVERGÊNCIAS ({len(mismatches)}) — produto esperava ≠ roteador decidiu")
        mt.add_column("cenário"); mt.add_column("esperado", style="green"); mt.add_column("decidiu", style="red")
        mt.add_column("porquê do roteador", style="dim")
        for cid, exp, got, reason in mismatches:
            mt.add_row(cid, exp, got, reason[:50])
        console.print(mt)
    else:
        console.print(Panel("✓ Zero divergências: a policy atual bate 100% com o julgamento de produto curado.",
                            border_style="green", box=ROUNDED))


if __name__ == "__main__":
    main()
