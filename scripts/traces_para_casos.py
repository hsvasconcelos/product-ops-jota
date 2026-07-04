"""Adaptador traces REAIS → lab_casos (o schema que o loop offline já consome).
=============================================================================
Fecha o circuito: pega a telemetria viva do bot (data/traces.jsonl) e produz o
MESMO formato de linha que recalibrar_policy.coletar_sinais e
promover_clusters.motivo_do_caso já leem — sem tocar nesses scripts. Com isso a
recalibração e a esteira passam a rodar sobre o que ACONTECEU de verdade, não só
sobre o laboratório sintético.

O `resolvido` por conversa vem de outcome.derive_from_traces (agrupa por sessão,
recontato, fecho explícito) — a mesma disciplina do derive_desfecho, aplicada ao
trace ao vivo.

    .venv/bin/python scripts/traces_para_casos.py            # → data/casos_reais.jsonl
    .venv/bin/python scripts/traces_para_casos.py --out X    # caminho custom
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from product_ops_jota.outcome import derive_from_traces
from product_ops_jota.trace import load as trace_load

OUT = ROOT / "data" / "casos_reais.jsonl"


def build(out: Path = OUT) -> int:
    rows = trace_load()                       # data/traces.jsonl (fail-safe)
    res = derive_from_traces(rows)
    with open(out, "w", encoding="utf-8") as f:
        for c in res["casos"]:
            # schema idêntico ao lab_casos.jsonl (o que o loop consome)
            f.write(json.dumps({
                "cid": c["sessao_id"], "tema": c["tema"], "acao": c["acao"],
                "gargalo": c["gargalo"], "inp": c["inp"], "resolvido": c["resolvido"],
                "fonte": "real",
            }, ensure_ascii=False) + "\n")
    return res["conversas"]


def main():
    out = Path(sys.argv[sys.argv.index("--out") + 1]) if "--out" in sys.argv else OUT
    n = build(out)
    print(f"✓ {out} · {n} conversas reais derivadas dos traces do bot")


if __name__ == "__main__":
    main()
