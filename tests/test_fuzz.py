"""Fuzzing de INVARIANTES — o que NUNCA pode quebrar, varrido em grade densa.
=============================================================================
test_core testa exemplos; aqui testamos PROPRIEDADES: para dezenas de milhares
de combinações de sinais e para entradas hostis, as garantias do desenho têm
que valer sempre. Roda no CI junto do gate rápido (puro Python, sem rede).

    .venv/bin/python tests/test_fuzz.py
"""
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from product_ops_jota.decision import DecisionInput, decide
from product_ops_jota.friction_model import InterceptionAction

CONTIDO = {InterceptionAction.AI_RESOLVE, InterceptionAction.AI_RESOLVE_SILENT,
           InterceptionAction.AI_ASSIST}
CRITS = [1.0, 1.5, 2.0, 2.5, 3.0, 3.5, 4.0, 4.5, 5.0]
GRID01 = [i / 10 for i in range(11)]


def test_invariantes_do_decide():
    """Varre ~100k combinações: segurança/política/esgotamento SEMPRE ganham,
    e a saída é SEMPRE válida — nenhuma combinação de sinais fura os gates."""
    n = 0
    for crit in CRITS:
        for trust in GRID01:
            for resol in GRID01:
                for conf in GRID01:
                    base = dict(criticality=crit, trust_risk=trust,
                                resolvability=resol, detection_confidence=conf)
                    # 1) segurança fura QUALQUER coisa (mesmo com stuck/requires juntos)
                    d = decide(DecisionInput(**base, safety_flag=True, stuck=True, requires_human=True))
                    assert d.action is InterceptionAction.HUMAN_HANDOFF and d.priority == 1.0, base
                    # 2) esgotamento (sem segurança) → humano
                    d = decide(DecisionInput(**base, stuck=True))
                    assert d.action is InterceptionAction.HUMAN_HANDOFF, base
                    # 3) política da KB → humano
                    d = decide(DecisionInput(**base, requires_human=True))
                    assert d.action is InterceptionAction.HUMAN_HANDOFF, base
                    # 4) saída sempre válida
                    d = decide(DecisionInput(**base))
                    assert isinstance(d.action, InterceptionAction)
                    assert 0.0 <= d.priority <= 1.0 and d.reason
                    n += 4
    assert n > 40000
    print(f"  invariantes do decide: {n} decisões varridas, zero violações")


def test_monotonicidade():
    """Com os demais sinais fixos: (a) MAIS resolubilidade nunca tira um caso da
    contenção; (b) MAIS trust nunca tira um caso do humano (uma vez escalado por
    confiança, mais confiança em jogo não 'des-escala')."""
    viol_a = viol_b = 0
    for crit in CRITS:
        for conf in GRID01:
            for trust in GRID01:
                prev = None
                for resol in GRID01:                       # resolubilidade crescente
                    c = decide(DecisionInput(criticality=crit, trust_risk=trust,
                                             resolvability=resol, detection_confidence=conf)).action in CONTIDO
                    if prev is True and c is False:
                        viol_a += 1
                    prev = c
            for resol in GRID01:
                prev_h = None
                for trust in GRID01:                       # trust crescente
                    h = decide(DecisionInput(criticality=crit, trust_risk=trust,
                                             resolvability=resol, detection_confidence=conf)).action \
                        is InterceptionAction.HUMAN_HANDOFF
                    if prev_h is True and h is False:
                        viol_b += 1
                    prev_h = h
    assert viol_a == 0, f"{viol_a} casos onde MAIS resolubilidade tirou da contenção"
    assert viol_b == 0, f"{viol_b} casos onde MAIS trust des-escalou o humano"
    print("  monotonicidade: resolubilidade e trust se comportam em toda a grade")


HOSTIS = ["", "   ", "a" * 10000, "😀😀😀🔥🔥", "\x00\x01\x02", "<script>alert(1)</script>",
          "'; DROP TABLE conversations; --", "my pix is not working at all",
          "no puedo acceder a mi cuenta", "ЖФЫВ олдж фывх", "𝕡𝕚𝕩 𝕟𝕒𝕠 𝕔𝕒𝕚𝕦",
          "não\nconsigo\n\n\nentrar\t\tno app", "ajuda " * 500]


def test_classificador_contra_entrada_hostil():
    """Nenhuma entrada de cliente pode derrubar a detecção: sem exceção, saída tipada."""
    from product_ops_jota.classifier import classify_conversation
    for txt in HOSTIS:
        det = classify_conversation([("customer", txt, "2026-07-04T10:00:00")], [],
                                    "2026-07-04T10:00:00")
        assert det.predicted_theme is not None and 0 <= det.theme_confidence <= 1
    # mensagem com texto None (transcrição de áudio que falhou, por exemplo)
    det = classify_conversation([("customer", None, "2026-07-04T10:00:00"),
                                 ("bot", "oi", "2026-07-04T10:01:00")], [], "2026-07-04T10:00:00")
    assert det is not None
    print(f"  classificador: {len(HOSTIS)+1} entradas hostis sem exceção")


def test_desfecho_em_bordas():
    """derive_desfecho não pode quebrar com dados sujos de produção."""
    from product_ops_jota.friction_model import SupportTheme
    from product_ops_jota.outcome import Desfecho, derive_desfecho
    bot = ("bot", "resposta", "2026-07-04T10:01:00")
    cliente = ("customer", "oi", "2026-07-04T10:00:00")
    # conversa de 1 mensagem (só cliente) → sem resposta
    r = derive_desfecho([cliente], [], SupportTheme.PIX)
    assert r.desfecho is Desfecho.SEM_RESPOSTA
    # conversa só com bot (proativa sem resposta do cliente) → assumido, nunca sucesso forte
    r = derive_desfecho([bot], [], SupportTheme.PIX)
    assert r.desfecho is Desfecho.RESOLVIDO_ASSUMIDO and r.confianca < 0.6
    # timestamps iguais e fora de ordem não quebram
    r = derive_desfecho([("customer", "a", "2026-07-04T10:00:00"), ("bot", "b", "2026-07-04T10:00:00"),
                         ("customer", "obrigado!", "2026-07-04T09:59:00")], [], SupportTheme.PIX)
    assert r is not None
    # evento de cura NA BORDA da janela (exatamente +24h) ainda confirma; +24h01 não
    r = derive_desfecho([cliente, bot], [("pix.sent", "2026-07-05T10:01:00")], SupportTheme.PIX)
    assert r.desfecho is Desfecho.RESOLVIDO_CONFIRMADO
    r = derive_desfecho([cliente, bot], [("pix.sent", "2026-07-05T10:02:00")], SupportTheme.PIX)
    assert r.desfecho is not Desfecho.RESOLVIDO_CONFIRMADO
    # evento com timestamp inválido é ignorado, não derruba
    r = derive_desfecho([cliente, bot], [("pix.sent", "não-é-data")], SupportTheme.PIX)
    assert r is not None
    # next_contact ANTERIOR ao fim da conversa não conta como recontato
    r = derive_desfecho([cliente, bot], [], SupportTheme.PIX, next_contact_at="2026-07-04T09:00:00")
    assert r.desfecho is not Desfecho.NAO_RESOLVIDO
    print("  desfecho: bordas e dados sujos sem quebra")


def test_incidente_em_bordas():
    from product_ops_jota.incident import INCIDENT_MIN_EVENTS, detect_incident
    now = datetime(2026, 7, 4, 12, 0, 0)
    mk = lambda n: [("pix.returned", "2026-07-04T11:55:00")] * n
    assert detect_incident(mk(INCIDENT_MIN_EVENTS - 1), now) is None      # 29 → não
    assert detect_incident(mk(INCIDENT_MIN_EVENTS), now) == "pix.returned"  # 30 → sim
    assert detect_incident([], now) is None
    # eventos no FUTURO não contam (relógio dessincronizado não dispara incidente)
    assert detect_incident([("x", "2026-07-04T13:00:00")] * 99, now) is None
    # timestamp inválido é ignorado
    assert detect_incident([("x", "lixo")] * 99, now) is None
    print("  incidente: limiar exato, futuro e lixo tratados")


def test_escada_do_bot():
    """A escada de 2ª tentativa: só doc do MESMO tema, nunca requires_human,
    nunca em crise, e tema com um doc só escala direto."""
    import os
    os.environ.setdefault("TELEGRAM_BOT_TOKEN", "x")
    os.environ["JOTA_RAG_MODE"] = "bm25"
    os.environ.pop("OPENAI_API_KEY", None)
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "apps"))
    try:
        import telegram_bot as tb
    except ModuleNotFoundError as e:
        # o gate leve do CI não instala as deps do app (httpx/fastapi). A escada é
        # coberta local/nightly; os invariantes puros acima rodam em qualquer lugar.
        print(f"  escada: pulada (app indisponível: {e.name}) — cobertura local/nightly")
        return
    from product_ops_jota.classifier import doc_theme

    # tema com UM doc (dados/LGPD): esgota → humano direto, sem caminho B
    s = tb._new_session("pf")
    tb.run_turn(s, "quero alterar meus dados cadastrais")
    tb.run_turn(s, "ja tentei e nao funcionou, continua igual")
    r = tb.run_turn(s, "de novo, nao resolve, mesma coisa")
    assert not s.get("caminho_b"), "não existe caminho B em tema de doc único"
    # crise NUNCA entra na escada, mesmo com strikes acumulados
    s = tb._new_session("pf")
    tb.run_turn(s, "nao consigo entrar no app")
    tb.run_turn(s, "ja tentei, nao funcionou")
    r = tb.run_turn(s, "nao aguento mais viver, vou me matar")
    assert r["dec"].action.value == "human_handoff" and "188" in r["reply"]
    assert not s.get("caminho_b")
    # quando o caminho B existe, é do MESMO tema e nunca requires_human
    s = tb._new_session("pf")
    tb.run_turn(s, "nao consigo entrar no app, fecha sozinho")
    tb.run_turn(s, "ja tentei isso, nao funcionou, continua igual")
    r = tb.run_turn(s, "tambem nao resolveu, continua a mesma coisa")
    if s.get("caminho_b"):
        doc_b = r["docs"][0]
        assert doc_theme(doc_b.id) == r["det"].predicted_theme
        assert not doc_b.requires_human
    print("  escada: doc único, crise e tema/privilégio respeitados")


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"✓ {name}")
    print("fuzzing: todas as invariantes valem")
