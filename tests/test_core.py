"""Testes do núcleo: schema do mapa de problemas, score de decisão e banco."""
import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from product_ops_jota.friction_model import (
    HERO_CASES, DetectionRule, FrictionNature, criticality_score, trust_risk_score,
)
from product_ops_jota.decision import decide, decide_for_case, DecisionInput, InterceptionAction
from product_ops_jota.support_db import init_db, seed_real_conversation


def test_hero_cases_instantiate():
    assert len(HERO_CASES) == 3
    for c in HERO_CASES:
        assert 1 <= c.criticality_value <= 5
        assert 0 <= c.trust_risk_value <= 1


def test_detection_rule_failfast():
    try:
        DetectionRule(nature=FrictionNature.SYSTEM_SIGNALED)  # falta event_type
        assert False, "deveria rejeitar"
    except ValueError:
        pass


def test_score_decides_hero_cases():
    resolv = {"pix_key_loop": 0.85, "kyc_failed_onboarding": 0.45, "fala_tap_receipt_anxiety": 0.90}
    expected = {
        "pix_key_loop": InterceptionAction.AI_RESOLVE,
        "kyc_failed_onboarding": InterceptionAction.AI_ASSIST,
        "fala_tap_receipt_anxiety": InterceptionAction.AI_RESOLVE,
    }
    for c in HERO_CASES:
        d = decide_for_case(c, resolv[c.case_id])
        assert d.action == expected[c.case_id], (c.case_id, d.action)


def test_score_gates():
    # trust alto + irreversível + IA não resolve → humano
    d = decide(DecisionInput(criticality=5, trust_risk=0.9, resolvability=0.2, detection_confidence=1.0))
    assert d.action == InterceptionAction.HUMAN_HANDOFF
    # detecção fraca → não age
    d = decide(DecisionInput(criticality=3, trust_risk=0.5, resolvability=0.7, detection_confidence=0.3))
    assert d.action == InterceptionAction.NO_INTERCEPT


def test_decision_golden_suite():
    """O roteador bate com o gabarito de produto curado — guarda de regressão da policy.
    Se um limiar for recalibrado e quebrar uma expectativa de produto, este teste pega."""
    golden = json.loads(
        (Path(__file__).resolve().parents[1] / "evals" / "decision_golden.json").read_text(encoding="utf-8")
    )
    assert len(golden) >= 10, "gabarito de decisão muito pequeno"
    for s in golden:
        d = decide(DecisionInput(
            criticality=s["criticality"], trust_risk=s["trust_risk"],
            resolvability=s["resolvability"], detection_confidence=s["detection_confidence"]))
        assert d.action.value == s["expected_action"], (s["id"], d.action.value, "esperado", s["expected_action"])


def test_handoff_context_and_queue():
    """Handoff quente: roteia por especialidade, respeita horário e não insiste (anti-spam)."""
    from datetime import datetime, timedelta
    from product_ops_jota.friction_model import SupportTheme
    from product_ops_jota.handoff import route_to_queue, should_reintercept, REINTERCEPT_WINDOW

    em_horario = route_to_queue(SupportTheme.PIX, priority=0.8, hour=14)
    assert em_horario.specialty == "Pagamentos/Pix" and em_horario.in_hours
    fora = route_to_queue(SupportTheme.KYC, priority=0.5, hour=23)
    assert not fora.in_hours and "retorno prioritário" in fora.note

    agora = datetime(2026, 6, 28, 12, 0, 0)
    # já interceptado há pouco, sem escalar → não insiste
    ok, _ = should_reintercept(SupportTheme.PIX, agora - timedelta(hours=1), agora)
    assert ok is False
    # mesmo recente, mas escalou → intervém de novo
    ok, _ = should_reintercept(SupportTheme.PIX, agora - timedelta(hours=1), agora, escalating=True)
    assert ok is True
    # janela passou → pode de novo
    ok, _ = should_reintercept(SupportTheme.PIX, agora - REINTERCEPT_WINDOW - timedelta(hours=1), agora)
    assert ok is True


def test_db_builds_and_constraints():
    conn = init_db(":memory:")
    seed_real_conversation(conn)
    n = conn.execute("SELECT COUNT(*) FROM conversations").fetchone()[0]
    assert n == 1
    # CHECK constraint barra theme inválido
    try:
        conn.execute("INSERT INTO conversations VALUES "
                     "('x','u_hugo','support','2026-01-01','INVALID','behavior_inferred',3,'resolved',0,0,0,0,0)")
        assert False, "deveria barrar"
    except sqlite3.IntegrityError:
        pass


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"✓ {name}")
    print("todos os testes passaram")
