"""Observabilidade — cada decisão do motor vira uma linha auditável (JSONL).
=============================================================================
store facts, derive views: o trace é FATO append-only (o que o motor decidiu,
com que sinais); o resumo é DERIVADO. É o que deixa debugar produção ("por que
esse cliente foi pra humano?") e medir (taxa de contenção, blocks do guardrail).

Fail-safe: logar NUNCA derruba o bot.
"""
from __future__ import annotations

import json
from collections import Counter
from datetime import datetime
from pathlib import Path

TRACE_PATH = Path(__file__).resolve().parents[2] / "data" / "traces.jsonl"


def trace(record: dict, path: Path = TRACE_PATH) -> None:
    """Append-only, fail-safe — uma decisão do motor por linha."""
    try:
        row = {"ts": datetime.now().isoformat(timespec="seconds"), **record}
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    except Exception:
        pass


def load(path: Path = TRACE_PATH) -> list[dict]:
    p = Path(path)
    if not p.exists():
        return []
    rows = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            try:
                rows.append(json.loads(line))
            except Exception:
                pass
    return rows


def summarize(path: Path = TRACE_PATH) -> dict:
    """Métricas derivadas do trace — a visão de observabilidade."""
    rows = load(path)
    n = len(rows)
    if not n:
        return {"n": 0}
    acoes = Counter(r.get("acao") for r in rows)
    temas = Counter(r.get("tema") for r in rows)
    fontes = Counter(r.get("fonte") for r in rows if r.get("fonte"))
    confs = [r["confianca"] for r in rows if isinstance(r.get("confianca"), (int, float))]
    guard = Counter(r.get("guardrail") for r in rows if r.get("guardrail"))
    humano = acoes.get("human_handoff", 0)
    return {
        "n": n,
        "acoes": dict(acoes.most_common()),
        "pct_contido": round(100 * (n - humano) / n, 1),   # IA resolveu/assistiu
        "pct_humano": round(100 * humano / n, 1),
        "temas": dict(temas.most_common()),
        "fontes_top": dict(fontes.most_common(5)),
        "conf_media": round(sum(confs) / len(confs), 3) if confs else None,
        "guardrail": dict(guard),
    }
