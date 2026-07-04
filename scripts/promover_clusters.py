"""Esteira de promoção — o §8 da FUNDACAO mecanizado (cluster → destino).
=============================================================================
O loop diz: atrito tratado por humano ou não-resolvido é registrado, CLUSTERIZADO,
e quando um cluster cruza o limiar de frequência ele é PROMOVIDO para um de três
destinos: regra de detecção · doc de KB · ticket de produto. Este script faz isso
sobre os 5k chamados do laboratório (via data/lab_casos.jsonl, exportado pelo
lab_atendimento.py) e cruza com as lacunas de KB registradas pelo bot.

Critério de promoção: FREQUÊNCIA (dor real), não opinião — policy nomeada abaixo.

    .venv/bin/python scripts/promover_clusters.py
"""
from __future__ import annotations

import json
import sys
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

CASOS = ROOT / "data" / "lab_casos.jsonl"
GAPS = ROOT / "data" / "kb_gaps.jsonl"
OUT = ROOT / "data" / "promocao.json"

# ─── POLICY ──────────────────────────────────────────────────────────────────
LIMIAR_PROMOCAO = 30      # cluster só entra na esteira com ≥N casos (dor real)
TOP = 15                  # candidatos exibidos

# motivo do caso não-contido → destino da promoção (§8: os 3 destinos + o "por desenho")
DESTINO = {
    "lacuna_kb": ("doc de KB", "não há procedimento que responda: escrever o doc cura o cluster inteiro"),
    "nao_executavel": ("ticket de produto", "a IA orienta mas não executa (privilégio): automatizar com segurança"),
    "esgotamento": ("revisão do doc de KB", "o procedimento existe e NÃO resolve: o conteúdo precisa mudar"),
    "confianca_em_jogo": ("regra de detecção proativa", "confiança em risco sem prova: detectar antes e agir com evidência"),
    "seguranca": ("mantém humano (por desenho)", "vulnerabilidade não se automatiza: o humano é o produto aqui"),
    "politica_kb": ("mantém humano (por desenho)", "LGPD/privilégio é decisão de gente, por escolha"),
    "palpite_fraco": ("regra de detecção", "o motor não teve certeza: novas âncoras/eventos aumentam a certeza"),
}


def _norm(t: str) -> str:
    t = unicodedata.normalize("NFKD", t or "")
    return "".join(ch for ch in t if not unicodedata.combining(ch)).lower()


def motivo_do_caso(r: dict) -> str | None:
    """Por que este chamado NÃO foi contido — na ordem dos gates."""
    if r["acao"] in ("ai_resolve", "ai_resolve_silent", "ai_assist"):
        return None
    inp, garg = r["inp"], r.get("gargalo") or ""
    if inp.get("safety_flag"):
        return "seguranca"
    if inp.get("stuck"):
        return "esgotamento"
    if inp.get("requires_human"):
        return "politica_kb"
    if r["acao"] == "no_intercept":
        return "palpite_fraco"
    if garg.startswith("sem procedimento"):
        return "lacuna_kb"
    if garg.startswith("não executável"):
        return "nao_executavel"
    return "confianca_em_jogo"


def main():
    clusters: Counter = Counter()
    exemplos: dict = defaultdict(list)
    total = nao_contidos = 0
    for line in CASOS.read_text("utf-8").splitlines():
        r = json.loads(line)
        total += 1
        m = motivo_do_caso(r)
        if m is None:
            continue
        nao_contidos += 1
        chave = (r["tema"], m)
        clusters[chave] += 1
        if len(exemplos[chave]) < 2:
            exemplos[chave].append(r["cid"])

    # lacunas registradas pelo bot entram como evidência dos clusters de lacuna
    gap_por_tema: Counter = Counter()
    gap_exemplo: dict = {}
    if GAPS.exists():
        for line in GAPS.read_text("utf-8").splitlines():
            try:
                g = json.loads(line)
            except Exception:
                continue
            gap_por_tema[g.get("tema", "other")] += 1
            gap_exemplo.setdefault(g.get("tema", "other"), g.get("pergunta", ""))

    candidatos = []
    for (tema, motivo), n in clusters.most_common():
        if n < LIMIAR_PROMOCAO:
            continue
        destino, porque = DESTINO[motivo]
        cand = {"tema": tema, "motivo": motivo, "n": n,
                "pct_do_suporte": round(100.0 * n / total, 1),
                "destino": destino, "porque": porque,
                "exemplos": exemplos[(tema, motivo)]}
        if motivo == "lacuna_kb" and gap_por_tema.get(tema):
            cand["evidencia_bot"] = (f"{gap_por_tema[tema]} lacunas registradas pelo bot no tema "
                                     f"(ex.: “{gap_exemplo.get(tema, '')[:60]}”)")
        candidatos.append(cand)

    out = {
        "amostra": total,
        "nao_contidos": nao_contidos,
        "limiar_promocao": LIMIAR_PROMOCAO,
        "clusters_acima_do_limiar": len(candidatos),
        "candidatos": candidatos[:TOP],
        "leitura": ("cada candidato PROMOVÍVEL derruba um cluster inteiro do 'vai para humano'; "
                    "os marcados 'por desenho' ficam com gente de propósito — derrubá-los seria errar"),
    }
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"✓ {OUT} · {nao_contidos}/{total} não contidos · {len(candidatos)} clusters ≥{LIMIAR_PROMOCAO}")
    for c in candidatos[:TOP]:
        print(f"  {c['n']:>4}  {c['tema']:<20} {c['motivo']:<18} → {c['destino']}")


if __name__ == "__main__":
    main()
