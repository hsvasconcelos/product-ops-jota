"""O LOOP FECHADO — o "aprende" do diagrama como daemon governado.
=============================================================================
Um comando roda a cadeia inteira do §8 da FUNDACAO e produz um BOLETIM DE
APRENDIZADO (data/aprendizado.json). NADA é aplicado: o loop PROPÕE, o gate de
eval valida, e o humano (Product Ops) dá o sign-off. Human-in-the-loop é feature,
não limitação — num produto financeiro, política que muda sozinha é risco.

A cadeia:
  1. adaptador traces→casos: quantas conversas REAIS o bot já registrou (o circuito
     fecha aqui — a telemetria viva entra no mesmo schema do loop);
  2. recalibração da policy contra o desfecho (grid+holdout+contratos) → proposta;
  3. esteira de promoção (cluster→destino) → clusters promovíveis;
  4. detecção proposta: as lacunas de KB viram candidatos a âncora/keyword e a
     docs faltantes (reusa o vocabulário atual pra achar o que é NOVO);
  5. boletim: tudo com evidência + o GATE que cada proposta precisa cruzar.

Honestidade: o volume estatístico vem do laboratório (5k); os traces reais são
poucos hoje (bot recém-instrumentado) e crescem a cada conversa — o boletim
reporta os dois. Determinístico e leve: usa a decisão JÁ gravada, sem re-rodar ML.

    .venv/bin/python scripts/loop_continuo.py
"""
from __future__ import annotations

import json
import subprocess
import sys
import unicodedata
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from product_ops_jota.classifier import THEME_ANCHORS, THEME_KEYWORDS
from product_ops_jota.friction_model import SupportTheme

import os
PY = os.environ.get("JOTA_PY") or str(ROOT / ".venv" / "bin" / "python")
OUT = ROOT / "data" / "aprendizado.json"


def _norm(t: str) -> str:
    t = unicodedata.normalize("NFKD", t or "")
    return "".join(c for c in t if not unicodedata.combining(c)).lower()


def _run(script: str) -> None:
    subprocess.run([PY, str(ROOT / "scripts" / script)], check=True,
                   capture_output=True, text=True)


def _load(name: str) -> dict:
    p = ROOT / "data" / name
    return json.loads(p.read_text("utf-8")) if p.exists() else {}


def deteccao_proposta() -> dict:
    """As lacunas registradas pelo bot viram propostas de detecção — SEMPRE proposta,
    nunca aplicação (gerar conteúdo de KB financeiro exige revisão humana)."""
    gaps_p = ROOT / "data" / "kb_gaps.jsonl"
    if not gaps_p.exists():
        return {"docs_faltando": [], "ancoras_propostas": []}
    # vocabulário que JÁ existe (pra achar só o que é novo)
    ja = set()
    for frases in list(THEME_ANCHORS.values()) + list(THEME_KEYWORDS.values()):
        for f in frases:
            ja.add(_norm(f))
    por_tema: Counter = Counter()
    exemplos: dict = {}
    novas_frases: Counter = Counter()
    for line in gaps_p.read_text("utf-8").splitlines():
        try:
            g = json.loads(line)
        except Exception:
            continue
        tema = g.get("tema", "other")
        perg = (g.get("pergunta") or "").strip()
        por_tema[tema] += 1
        exemplos.setdefault(tema, perg)
        n = _norm(perg)
        if n and not any(n in v or v in n for v in ja):
            novas_frases[perg[:70]] += 1
    # tema OTHER recorrente = assunto fora da KB → candidato a doc novo
    docs_faltando = [{"tema": t, "n": n, "exemplo": exemplos.get(t, "")}
                     for t, n in por_tema.most_common() if t == "other" and n >= 3]
    ancoras = [{"frase": f, "n": n} for f, n in novas_frases.most_common(8) if n >= 2]
    return {"docs_faltando": docs_faltando, "ancoras_propostas": ancoras,
            "lacunas_por_tema": dict(por_tema.most_common())}


def main():
    print("① adaptador: telemetria viva → schema do loop…")
    r = subprocess.run([PY, str(ROOT / "scripts" / "traces_para_casos.py")],
                       capture_output=True, text=True)
    casos_reais_p = ROOT / "data" / "casos_reais.jsonl"
    n_reais = sum(1 for _ in casos_reais_p.open()) if casos_reais_p.exists() else 0

    print("② recalibração da policy contra o desfecho…")
    _run("recalibrar_policy.py")
    recal = _load("recalibracao.json")

    print("③ esteira de promoção (cluster → destino)…")
    _run("promover_clusters.py")
    promo = _load("promocao.json")

    print("④ detecção proposta a partir das lacunas de KB…")
    det = deteccao_proposta()

    scorecard = _load("eval_scorecard.json").get("metrics", {})
    contratos_verdes = (scorecard.get("decisao_divergencias") == 0
                        and (scorecard.get("desfecho_binario") or 0) >= 0.80)

    melhor = recal.get("melhor") or {}
    boletim = {
        "gerado_em": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "fonte": {"conversas_reais": n_reais, "volume_sintetico": recal.get("amostra"),
                  "nota": "o volume estatístico vem do laboratório; cada conversa real do "
                          "bot entra no mesmo circuito e ganha peso com o tempo"},
        "policy": {
            "proposta": melhor.get("policy") if recal.get("ganho_real") else None,
            "contencao_atual": recal.get("baseline", {}).get("contencao_pct"),
            "contencao_proposta": melhor.get("contencao_pct"),
            "leitura": recal.get("leitura"),
            "gate": "só entra se mantém os 15 contratos e não perde precisão no teste (holdout)",
        },
        "esteira": {
            "clusters_promoviveis": promo.get("clusters_acima_do_limiar"),
            "top": promo.get("candidatos", [])[:5],
            "gate": "frequência ≥ limiar (dor real) + curadoria de Product Ops",
        },
        "deteccao": {
            "docs_faltando": det["docs_faltando"],
            "ancoras_propostas": det["ancoras_propostas"],
            "gate": "proposta: doc de KB financeiro e âncora nova exigem revisão humana",
        },
        "contratos_verdes": contratos_verdes,
        "governanca": "o loop PROPÕE; o gate de eval valida; o humano aplica. Nada muda sozinho.",
    }
    OUT.write_text(json.dumps(boletim, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n✓ boletim de aprendizado em {OUT}")
    print(f"  reais: {n_reais} · sintético: {recal.get('amostra')} · contratos verdes: {contratos_verdes}")
    print(f"  policy: {boletim['policy']['leitura']}")
    print(f"  esteira: {promo.get('clusters_acima_do_limiar')} clusters · "
          f"detecção: {len(det['docs_faltando'])} docs faltando, {len(det['ancoras_propostas'])} âncoras propostas")


if __name__ == "__main__":
    main()
