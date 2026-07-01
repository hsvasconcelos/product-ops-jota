"""Laboratório · Atendimento (Mundo 1) — roda o MOTOR REAL nos 5k chamados.
=============================================================================
A etapa 1 da narrativa: o atendimento é o laboratório. Cada chamado é um
sintoma; aqui a gente roda detecção → resolubilidade → decisão em TODOS os
chamados rotulados e mede o que o motor FARIA:

  · funil de decisão  — resolve sozinha / assiste / humano / observar
  · cascata da resolubilidade — dos que viram humano, POR QUÊ (qual fator falha)
  · custo humano (antes×depois) — quanto trabalho o pacote de contexto poupa
  · ContextPacks reais — o handoff quente, derivado na hora

NÃO é produção: é o que o motor DECIDIRIA sobre os rótulos que temos. Honesto
de propósito (FUNDACAO §3: ser, não parecer). Roda uma vez; a página lê o JSON.

    .venv/bin/python scripts/lab_atendimento.py
"""
from __future__ import annotations

import json
import sqlite3
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from datetime import datetime

from product_ops_jota.classifier import classify_conversation
from product_ops_jota.decision import (
    decide, derive_decision_input, derive_resolubilidade, UserProfile,
)
from product_ops_jota.friction_model import InterceptionAction
from product_ops_jota.handoff import build_context_pack
from product_ops_jota.rag import Retriever

DB = ROOT / "data" / "jota_support.db"
OUT = ROOT / "data" / "lab_atendimento.json"

ACTION_ORDER = [InterceptionAction.AI_RESOLVE, InterceptionAction.AI_RESOLVE_SILENT,
                InterceptionAction.AI_ASSIST, InterceptionAction.HUMAN_HANDOFF,
                InterceptionAction.NO_INTERCEPT]


def _load(conn):
    """Cada conversa de suporte com mensagens, eventos e PERFIL do usuário (banco)."""
    convs = conn.execute(
        "SELECT c.conversation_id, c.user_id, c.started_at, c.gold_theme, c.gold_criticality, "
        "c.outcome, c.context_lost, u.segment, u.digital_literacy, u.age_band, u.signup_at "
        "FROM conversations c JOIN users u ON u.user_id=c.user_id "
        "WHERE c.channel='support' ORDER BY c.started_at"
    ).fetchall()
    for (cid, uid, started, gtheme, gcrit, outcome, clost, seg, lit, age, sign) in convs:
        msgs = conn.execute(
            "SELECT sender, text, sent_at FROM messages WHERE conversation_id=? ORDER BY turn_index",
            (cid,)).fetchall()
        evts = conn.execute(
            "SELECT event_type, occurred_at FROM events WHERE conversation_id=? ORDER BY occurred_at",
            (cid,)).fetchall()
        yield dict(cid=cid, uid=uid, started=started, gtheme=gtheme, gcrit=gcrit,
                   outcome=outcome, context_lost=clost, msgs=msgs, evts=evts,
                   profile=UserProfile(segment=seg, digital_literacy=lit, age_band=age, signup_at=sign))


def main():
    conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    retriever = Retriever()              # tema SEMÂNTICO (o motor real); cai p/ keyword se sem deps

    funnel = Counter()
    gargalos = Counter()                 # por que NÃO resolve sozinha
    resol_pass = Counter()               # quantos passam cada fator
    by_theme: dict[str, Counter] = {}    # ação × tema
    theme_hit = 0                        # acurácia de tema do classificador (honestidade)
    total = 0
    turns_when_human = []                # custo humano: nº de turnos antes do humano
    examples = []                        # ContextPacks reais (handoff)

    for c in _load(conn):
        total += 1
        messages = [(s, t, ts) for (s, t, ts) in c["msgs"]]
        det = classify_conversation(messages, c["evts"], c["started"], retriever=retriever)
        if det.predicted_theme.value == c["gtheme"]:
            theme_hit += 1
        theme = det.predicted_theme
        customer_text = " ".join(t for (s, t, _) in c["msgs"] if s == "customer")
        # decisão 100% derivada: resolubilidade vem do DOC recuperado (KB), não do tema;
        # criticidade/trust saem da conversa + perfil; stuck = loop (procedimento falhou).
        ref = datetime.fromisoformat(c["started"])
        docs = retriever.retrieve(f"{customer_text} {theme.value.replace('_', ' ')}", top_k=1)
        doc = docs[0] if docs else None
        # esgotamento (batch, sem estado por turno): loop + conversa longa = tentou e falhou
        n_cust = sum(1 for s, _, _ in c["msgs"] if s == "customer")
        stuck = det.in_loop and n_cust >= 4
        inp = derive_decision_input(det, c["profile"], customer_text, ref, doc=doc, stuck=stuck)
        dec = decide(inp)
        resol = derive_resolubilidade(det, doc)

        funnel[dec.action.value] += 1
        f = resol.fatores
        resol_pass["kb_existe"] += f.kb_existe
        resol_pass["executavel"] += f.executavel
        resol_pass["reversivel"] += f.reversivel
        if resol.gargalo:
            gargalos[resol.gargalo] += 1
        by_theme.setdefault(theme.value, Counter())[dec.action.value] += 1

        if dec.action == InterceptionAction.HUMAN_HANDOFF:
            turns_when_human.append(len(c["msgs"]))
            seen = {e["tema"] for e in examples}            # 1 exemplo por tema, texto não-trivial
            if len(examples) < 4 and theme.value not in seen and len(customer_text) > 12:
                hora = int(c["started"][11:13])
                pack = build_context_pack(det, dec, inp.criticality, c["profile"].segment, hora, ai_offered=None)
                examples.append(_pack_json(c, det, dec, resol, pack))

    # ── custo humano: antes (hoje) × depois (com o pacote) ───────────────────
    g = conn.execute
    esc = g("SELECT COUNT(*), AVG((SELECT COUNT(*) FROM messages m WHERE m.conversation_id=c.conversation_id)), "
            "ROUND(100.0*AVG(context_lost),1), ROUND(100.0*AVG(asked_for_human),1) "
            "FROM conversations c WHERE channel='support' AND outcome='escalated'").fetchone()
    ignored = g("SELECT SUM(asked_for_human)-SUM(human_handoff_done) FROM conversations WHERE channel='support'").fetchone()[0]
    conn.close()

    # ── triagem reativa: todo chamado precisa de desfecho → 3 baldes ─────────
    # (no_intercept aqui = o motor se RECUSA a agir sozinho s/ base/certeza → humano)
    resolve = funnel.get("ai_resolve", 0) + funnel.get("ai_resolve_silent", 0)
    assist = funnel.get("ai_assist", 0)
    humano = funnel.get("human_handoff", 0) + funnel.get("no_intercept", 0)
    triagem = [
        {"balde": "IA resolve sozinha", "n": resolve, "pct": round(100.0 * resolve / total, 1)},
        {"balde": "IA assiste o humano", "n": assist, "pct": round(100.0 * assist / total, 1)},
        {"balde": "Vai pra humano", "n": humano, "pct": round(100.0 * humano / total, 1)},
    ]

    out = {
        "total": total,
        "theme_accuracy": round(100.0 * theme_hit / total, 1),
        "triagem": triagem,
        "funnel": [{"acao": a.value, "n": funnel.get(a.value, 0),
                    "pct": round(100.0 * funnel.get(a.value, 0) / total, 1)} for a in ACTION_ORDER],
        "resolubilidade": {
            "passam": dict(resol_pass),
            "gargalos": [{"motivo": k, "n": v, "pct": round(100.0 * v / total, 1)}
                         for k, v in gargalos.most_common()],
        },
        "by_theme": {th: dict(cnt) for th, cnt in sorted(by_theme.items(), key=lambda kv: -sum(kv[1].values()))},
        "custo_humano": {
            "escalados": esc[0], "turnos_medios": round(esc[1] or 0, 1),
            "context_lost_pct": esc[2], "pediram_humano_pct": esc[3],
            "turnos_medios_handoff": round(sum(turns_when_human) / max(1, len(turns_when_human)), 1),
            "pedidos_humano_ignorados": ignored,
        },
        "exemplos_handoff": examples,
    }
    # exemplo mais didático primeiro: irreversível > privilégio > lacuna
    def _rank(e):
        g = e["gargalo"] or ""
        return 0 if g.startswith("irrev") else 1 if g.startswith("não ex") else 2
    examples.sort(key=_rank)
    out["exemplos_handoff"] = examples
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")

    # resumo no terminal
    print(f"✓ {OUT}  ·  {total} chamados de suporte")
    print(f"  tema (classificador vs gold): {out['theme_accuracy']}%")
    for row in out["funnel"]:
        print(f"  {row['acao']:<22} {row['n']:>5}  {row['pct']:>5}%")
    print("  gargalos (por que não resolve sozinha):")
    for row in out["resolubilidade"]["gargalos"]:
        print(f"    {row['motivo'][:48]:<50} {row['n']:>5}  {row['pct']:>5}%")
    print(f"  exemplos de handoff: {len(examples)}")


def _pack_json(c, det, dec, resol, pack) -> dict:
    cust = [t for (s, t, _) in c["msgs"] if s == "customer"]
    trecho = max(cust, key=len) if cust else ""        # a fala mais informativa do cliente
    return {
        "conversation_id": c["cid"],
        "tema": pack.theme.value, "natureza": pack.nature.value,
        "criticidade": pack.criticality, "evidencia": pack.evidence,
        "sinais": pack.signals,
        "fila": pack.routing.specialty, "prioridade": round(pack.routing.priority, 2),
        "in_hours": pack.routing.in_hours, "fila_nota": pack.routing.note,
        "motivo": pack.reason,
        "gargalo": resol.gargalo,
        "resolubilidade": resol.valor,
        "turnos_cliente": len(cust),
        "trecho": trecho,
    }


if __name__ == "__main__":
    main()
