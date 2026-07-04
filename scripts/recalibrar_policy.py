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
  4. HOLDOUT: o grid escolhe no conjunto de TREINO (metade dos casos) e o
     resultado reportado é o do conjunto de TESTE (a outra metade, nunca vista
     na escolha) — sem isso, "venceu no grid" seria overfit de manual.
  5. Entre os elegíveis, vence o de MAIOR contenção no treino. Se no teste o
     ganho não se sustentar, o resultado honesto é exatamente esse.

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
    return True   # o contrato vive INTEIRO em decision_golden.json (heróis inclusos)


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
    # HOLDOUT determinístico: índices pares treinam, ímpares testam (reproduzível)
    treino = [c for i, c in enumerate(casos) if i % 2 == 0]
    teste = [c for i, c in enumerate(casos) if i % 2 == 1]
    base_treino = avaliar(treino, DEFAULT_THRESHOLDS)
    base_teste = avaliar(teste, DEFAULT_THRESHOLDS)
    print(f"   baseline (teste): contenção {base_teste['contencao_pct']}% · precisão {base_teste['precisao_contencao_pct']}%")

    print(f"② varrendo o grid no TREINO ({len(treino)} casos)…")
    chaves = list(GRID)
    melhores = []
    testados = elegiveis = 0
    for combo in itertools.product(*(GRID[k] for k in chaves)):
        params = dict(zip(chaves, combo))
        if params["assist_floor"] >= params["resolve_floor"]:
            continue                          # assiste tem que ser faixa abaixo de resolve
        t = PolicyThresholds(**params)
        testados += 1
        m = avaliar(treino, t)
        # elegível: contrato golden exato E a precisão da contenção não cai (no treino)
        if m["precisao_contencao_pct"] + 1e-9 < base_treino["precisao_contencao_pct"]:
            continue
        if not golden_ok(t):
            continue
        elegiveis += 1
        melhores.append((m["contencao_pct"], m["precisao_contencao_pct"], params, m))
    melhores.sort(key=lambda x: (-x[0], -x[1]))

    best = melhores[0] if melhores else None
    # o número que vale é o do TESTE (casos que a escolha nunca viu)
    m_teste = avaliar(teste, PolicyThresholds(**best[2])) if best else None
    venceu = bool(m_teste) and m_teste["contencao_pct"] > base_teste["contencao_pct"] \
        and m_teste["precisao_contencao_pct"] + 1e-9 >= base_teste["precisao_contencao_pct"]
    # PRIMEIRO TOQUE do mundo novo: os MESMOS atritos, cliente recém-chegado — o
    # esgotamento do acervo é herança do atendimento lento do mundo velho e não
    # existe no primeiro contato. É o número operacional da proatividade.
    def _pt(inp):
        d = inp.model_dump(); d["stuck"] = False
        return DecisionInput(**d)
    cont_pt = sum(1 for inp, _ in casos if decide(_pt(inp), DEFAULT_THRESHOLDS).action in CONTIDO)
    politica = sum(1 for inp, _ in casos if inp.requires_human)
    primeiro_toque = {
        "contencao_pct": round(100.0 * cont_pt / len(casos), 1),
        "teto_estrutural_pct": round(100.0 * (1 - politica / len(casos)), 1),
        "nota": "mesmos 5 mil atritos, sem o esgotamento herdado do acervo (stuck=False); "
                "o teto desconta o que é humano por política (LGPD/privilégio)",
    }

    out = {
        "gerado_em": datetime.now().isoformat(timespec="seconds"),
        "primeiro_toque": primeiro_toque,
        "amostra": len(casos), "treino": len(treino), "teste": len(teste),
        "grid_testado": testados,
        "grid_elegivel": elegiveis,
        "baseline": {"policy": DEFAULT_THRESHOLDS.model_dump(), **base_teste},
        "melhor": ({"policy": best[2], "treino": best[3], **m_teste} if best else None),
        "ganho_real": venceu,
        "leitura": ("o grid escolheu no treino e o ganho SE SUSTENTOU no teste (casos nunca vistos), "
                    "sem perder precisão nem contrato"
                    if venceu else
                    "no teste (casos nunca vistos) o ganho do grid não se sustentou — a policy atual fica; "
                    "resultado honesto é isto"),
    }
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"③ {out['leitura']}")
    if best:
        print(f"   melhor: contenção {best[0]}% · precisão {best[1]}% · policy {best[2]}")
    print(f"✓ salvo em {OUT}")


if __name__ == "__main__":
    main()
