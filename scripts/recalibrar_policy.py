"""Recalibração da policy contra o DESFECHO — o loop fechando de verdade.
=============================================================================
O deck afirma: "cada régua começa como julgamento de produto e recalibra contra
o desfecho real". Este script FAZ isso, com os contratos como cinto:

  1. Lê os 4 sinais + desfecho de cada chamado de data/lab_casos.jsonl
     (exportado pelo lab_atendimento.py — a passada cara roda UMA vez, lá).
  2. Varre um grid de PolicyThresholds re-decidindo só a cascata (barata).
  3. Um candidato só é elegível se:
       a) os 15 cenários golden de decisão continuam EXATOS (contrato);
       b) a PRECISÃO DA CONTENÇÃO não cai: % dos casos contidos pela IA cujo
          desfecho derivado é "resolvido" (é o desfecho vigiando a ganância —
          conter mais só vale se continuar resolvendo de verdade);
       c) os gates de segurança/esgotamento/política nem entram no grid
          (não são thresholds: são recusas por desenho).
  4. Entre os elegíveis, vence o de MAIOR contenção. Se ninguém superar a
     policy atual, o resultado honesto é "a atual já está no ótimo do grid".

Saída: data/recalibracao.json (a página lê de lá — nada de número copiado).

    .venv/bin/python scripts/recalibrar_policy.py
"""
from __future__ import annotations

import itertools
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "evals"))

from datetime import datetime

from product_ops_jota.decision import (
    DEFAULT_THRESHOLDS, DecisionInput, PolicyThresholds, decide,
)
from product_ops_jota.friction_model import InterceptionAction

CASOS = ROOT / "data" / "lab_casos.jsonl"
OUT = ROOT / "data" / "recalibracao.json"

# ─── O GRID (policy candidata) — só as réguas numéricas; segurança fica fora ──
GRID = {
    "conf_floor": [0.45, 0.50, 0.55],
    "roi_crit_floor": [1.5, 2.0, 2.5],
    "roi_trust_floor": [0.15, 0.20, 0.25],
    "handoff_ceiling": [0.35, 0.40, 0.45, 0.50],
    "resolve_floor": [0.40, 0.45, 0.50, 0.55],
    "assist_floor": [0.30, 0.35, 0.40],
}
CONTIDO = {InterceptionAction.AI_RESOLVE, InterceptionAction.AI_RESOLVE_SILENT,
           InterceptionAction.AI_ASSIST}


def golden_ok(t: PolicyThresholds) -> bool:
    """Contrato: os 15 cenários curados decidem IGUAL sob a policy candidata."""
    golden = json.loads((ROOT / "evals" / "decision_golden.json").read_text("utf-8"))
    for s in golden:
        d = decide(DecisionInput(criticality=s["criticality"], trust_risk=s["trust_risk"],
                                 resolvability=s["resolvability"],
                                 detection_confidence=s["detection_confidence"]), t)
        if d.action.value != s["expected_action"]:
            return False
    from product_ops_jota.friction_model import HERO_CASES
    from product_ops_jota.decision import decide_for_case
    resolv = {"pix_key_loop": 0.85, "kyc_failed_onboarding": 0.45, "fala_tap_receipt_anxiety": 0.90}
    esperado = {"pix_key_loop": InterceptionAction.AI_RESOLVE,
                "kyc_failed_onboarding": InterceptionAction.AI_ASSIST,
                "fala_tap_receipt_anxiety": InterceptionAction.AI_RESOLVE}
    return all(decide_for_case(c, resolv[c.case_id], t).action == esperado[c.case_id]
               for c in HERO_CASES)


def coletar_sinais():
    """Lê os sinais cacheados pelo lab (a passada cara já rodou lá)."""
    casos = []
    for line in CASOS.read_text("utf-8").splitlines():
        r = json.loads(line)
        casos.append((DecisionInput(**r["inp"]), bool(r["resolvido"])))
    return casos


def avaliar(casos, t: PolicyThresholds) -> dict:
    """Contenção e precisão-da-contenção (vs desfecho) da policy `t` sobre o cache."""
    contidos = contidos_resolvidos = 0
    for inp, resolvido in casos:
        if decide(inp, t).action in CONTIDO:
            contidos += 1
            contidos_resolvidos += resolvido
    n = len(casos)
    return {"contencao_pct": round(100.0 * contidos / n, 1),
            "precisao_contencao_pct": round(100.0 * contidos_resolvidos / max(1, contidos), 1),
            "contidos": contidos}


def main():
    print("① lendo sinais + desfecho de data/lab_casos.jsonl…")
    casos = coletar_sinais()
    base = avaliar(casos, DEFAULT_THRESHOLDS)
    print(f"   baseline: contenção {base['contencao_pct']}% · precisão da contenção {base['precisao_contencao_pct']}%")

    print("② varrendo o grid…")
    chaves = list(GRID)
    melhores = []
    testados = elegiveis = 0
    for combo in itertools.product(*(GRID[k] for k in chaves)):
        params = dict(zip(chaves, combo))
        if params["assist_floor"] >= params["resolve_floor"]:
            continue                          # assiste tem que ser faixa abaixo de resolve
        t = PolicyThresholds(**params)
        testados += 1
        m = avaliar(casos, t)
        # elegível: contrato golden exato E a precisão da contenção não cai
        if m["precisao_contencao_pct"] + 1e-9 < base["precisao_contencao_pct"]:
            continue
        if not golden_ok(t):
            continue
        elegiveis += 1
        melhores.append((m["contencao_pct"], m["precisao_contencao_pct"], params, m))
    melhores.sort(key=lambda x: (-x[0], -x[1]))

    venceu = bool(melhores) and melhores[0][0] > base["contencao_pct"]
    best = melhores[0] if melhores else None
    out = {
        "gerado_em": datetime.now().isoformat(timespec="seconds"),
        "amostra": len(casos),
        "grid_testado": testados,
        "grid_elegivel": elegiveis,
        "baseline": {"policy": DEFAULT_THRESHOLDS.model_dump(), **base},
        "melhor": ({"policy": best[2], **best[3]} if best else None),
        "ganho_real": venceu,
        "leitura": ("o grid achou policy com mais contenção SEM perder precisão nem contrato"
                    if venceu else
                    "a policy atual já está no ótimo do grid — recalibração confirmou o desenho"),
    }
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"③ {out['leitura']}")
    if best:
        print(f"   melhor: contenção {best[0]}% · precisão {best[1]}% · policy {best[2]}")
    print(f"✓ salvo em {OUT}")


if __name__ == "__main__":
    main()
