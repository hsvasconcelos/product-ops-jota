"""Eval do classificador — mede a DETECÇÃO contra o GABARITO (gold_*).
=============================================================================
Roda o classifier.py (regras/heurística, v1) nas 1000 conversas, compara o
rótulo PREVISTO com o gabarito escondido (gold_theme, gold_nature) e reporta:
  · acurácia por dimensão (nature, theme)
  · precision / recall / F1 por classe (scikit-learn)
  · matriz de confusão (nature e theme)
  · bônus: sinal comportamental asked_for_human previsto (do texto) × o fato
    observável registrado na coluna asked_for_human.

Honestidade: o gabarito SÓ é lido AQUI (no avaliador), nunca pelo classificador.
Se uma dimensão vier baixa, o número aparece — é sinal de calibração, não de
fracasso. É justamente o que o eval existe pra expor.

Uso:
    python evals/run_classifier_eval.py
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from sklearn.metrics import (
    accuracy_score, confusion_matrix, f1_score, precision_recall_fscore_support,
)

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.box import ROUNDED, SIMPLE_HEAVY

from product_ops_jota.classifier import classify_conversation, load_raw_conversation

DB = Path(__file__).resolve().parents[1] / "data" / "jota_support.db"
console = Console()


def coletar(conn):
    """Para cada conversa: prevê (do bruto) e lê o gabarito (só aqui)."""
    ids = [r[0] for r in conn.execute("SELECT conversation_id FROM conversations").fetchall()]
    y = {"theme_true": [], "theme_pred": [], "nature_true": [], "nature_pred": [],
         "human_true": [], "human_pred": []}
    for cid in ids:
        messages, events, started_at = load_raw_conversation(conn, cid)
        pred = classify_conversation(messages, events, started_at)
        # gabarito + fato observável (lidos só no avaliador)
        gold_theme, gold_nature, asked_human = conn.execute(
            "SELECT gold_theme, gold_nature, asked_for_human FROM conversations WHERE conversation_id=?",
            (cid,),
        ).fetchone()
        y["theme_true"].append(gold_theme)
        y["theme_pred"].append(pred.predicted_theme.value)
        y["nature_true"].append(gold_nature)
        y["nature_pred"].append(pred.predicted_nature.value)
        y["human_true"].append(int(asked_human))
        y["human_pred"].append(int(pred.asked_for_human))
    return len(ids), y


def tabela_metricas(titulo, y_true, y_pred):
    labels = sorted(set(y_true) | set(y_pred))
    p, r, f1, sup = precision_recall_fscore_support(
        y_true, y_pred, labels=labels, zero_division=0)
    acc = accuracy_score(y_true, y_pred)
    t = Table(box=SIMPLE_HEAVY, expand=True,
              title=f"{titulo} — acurácia {acc*100:.1f}%  ·  F1-macro {f1_score(y_true, y_pred, labels=labels, average='macro', zero_division=0)*100:.1f}%")
    t.add_column("Classe"); t.add_column("Precision", justify="right")
    t.add_column("Recall", justify="right"); t.add_column("F1", justify="right")
    t.add_column("Suporte", justify="right", style="dim")
    for i, lab in enumerate(labels):
        cor = "green" if f1[i] >= 0.8 else ("yellow" if f1[i] >= 0.5 else "red")
        from rich.text import Text
        t.add_row(lab, f"{p[i]:.2f}", f"{r[i]:.2f}",
                  Text(f"{f1[i]:.2f}", style=cor), str(int(sup[i])))
    return t, acc, labels


def matriz_confusao(titulo, y_true, y_pred, labels):
    cm = confusion_matrix(y_true, y_pred, labels=labels)
    t = Table(box=SIMPLE_HEAVY, expand=True, title=f"Matriz de confusão — {titulo}  (linha = real, coluna = previsto)")
    t.add_column("real ╲ prev", style="bold")
    short = [l[:10] for l in labels]
    for s in short:
        t.add_column(s, justify="right")
    for i, lab in enumerate(labels):
        row = [lab[:14]]
        for j in range(len(labels)):
            v = cm[i][j]
            cell = str(v)
            if i == j and v:
                from rich.text import Text
                cell = Text(str(v), style="bold green")
            elif v:
                from rich.text import Text
                cell = Text(str(v), style="red")
            row.append(cell)
        t.add_row(*row)
    return t


def bonus_human(y_true, y_pred):
    acc = accuracy_score(y_true, y_pred)
    p, r, f1, _ = precision_recall_fscore_support(
        y_true, y_pred, labels=[1], average="binary", zero_division=0)
    t = Table(box=SIMPLE_HEAVY, expand=True,
              title="BÔNUS — sinal 'pediu humano' previsto (do texto) × fato registrado")
    t.add_column("Métrica"); t.add_column("Valor", justify="right", style="bold")
    t.add_row("Acurácia", f"{acc*100:.1f}%")
    t.add_row("Precision (classe pediu=1)", f"{p:.2f}")
    t.add_row("Recall (classe pediu=1)", f"{r:.2f}")
    t.add_row("F1 (classe pediu=1)", f"{f1:.2f}")
    return t


def main():
    if not DB.exists():
        console.print("[red]banco não encontrado — rode: python scripts/build_db.py[/red]")
        sys.exit(1)
    conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    try:
        n, y = coletar(conn)
    finally:
        conn.close()

    console.print(Panel(
        f"Classificador v1 (regras) avaliado em [bold]{n}[/bold] conversas.\n"
        "O classificador NÃO leu o gabarito (gold_*); correlacionou eventos por "
        "user_id + tempo, ignorando o FK conversation_id.",
        title="EVAL · detecção de atrito vs gabarito", border_style="cyan", box=ROUNDED))

    nat_t, _, nat_labels = tabela_metricas("NATUREZA", y["nature_true"], y["nature_pred"])
    console.print(nat_t)
    console.print(matriz_confusao("natureza", y["nature_true"], y["nature_pred"], nat_labels))

    th_t, _, th_labels = tabela_metricas("TEMA", y["theme_true"], y["theme_pred"])
    console.print(th_t)
    console.print(matriz_confusao("tema", y["theme_true"], y["theme_pred"], th_labels))

    console.print(bonus_human(y["human_true"], y["human_pred"]))


if __name__ == "__main__":
    main()
