"""Simulador de conversas — testa o AGENTE em VOLUME, não 1-a-1 no Telegram.
=============================================================================
Roda cenários multi-turn (cliente roteirizado) pelo MESMO cérebro do bot
(run_turn: detecção → decisão → redação/handoff → esgotamento) e checa:
  · a AÇÃO final bate com o esperado? (resolve vs humano)
  · em que turno escalou? (timing do esgotamento/segurança)
  · GROUNDING: a resposta violou alguma regra? (ex.: "seu banco" — o Jota É o banco)

É o eval de nível-conversa (o unitário está em run_all.py). Substitui o teste
manual: um comando, dezenas de cenários, scorecard com PASS/FAIL.

Uso:
    .venv/bin/python evals/simulate_conversations.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "apps"))

from rich.box import ROUNDED, SIMPLE_HEAVY
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

import telegram_bot as bot   # reusa run_turn + _new_session (o cérebro, sem Telegram)

console = Console()
OUT = ROOT / "data" / "sim_conversations.json"

# cenários: turns = falas do cliente (roteirizadas, com fraseado real/typos/baixa literacia).
# expect = quem DEVE resolver: humano (escalar) ou a IA (resolver/orientar in-thread). O
# sub-tipo (resolve/assiste/observar) não muda a experiência — o eixo é IA vs humano.
H, R = "human", "ai"
SCENARIOS = [
    # a ESCADA entra no meio: A falha 2x → caminho B (doc alternativo) → B falha → pessoa.
    # O roteiro tem uma falha a mais que a versão pré-escada, pra validar a escada inteira.
    {"id": "acesso_esgota", "seg": "pf", "expect": H,
     "turns": ["nao consigo acessar minha conta", "no app", "fica tela branca",
               "fiz isso e nao funcionou", "continua sem funcionar",
               "tentei esse outro caminho tambem e nada, continua igual"]},
    {"id": "acesso_baixa_literacia", "seg": "pf", "expect": H,
     "turns": ["moço nao consigo entrar na minha conta", "no aplicativo", "abre e fecha sozinho",
               "ja fiz oq vc falo e nao deu certo", "seque sem funciona, ja tentei tudo",
               "fiz do outro jeito q vc falo tambem e nada"]},
    {"id": "acesso_resolve_1a", "seg": "pf", "expect": R,
     "turns": ["meu app ta meio travado quando abro", "no app"]},
    {"id": "pix_errado", "seg": "pf", "expect": R,
     "forbidden": ["seu banco", "outro banco", "procurar o banco", "contate seu banco", "banco parceiro"],
     "turns": ["fiz um pix pra pessoa errada, quero de volta", "foi erro de digitacao do cpf",
               "saiu da minha conta jota"]},
    {"id": "pix_chave_invalida", "seg": "pf", "expect": R,
     "turns": ["nao consigo fazer um pix, da erro na chave"]},
    {"id": "ludopatia_seguranca", "seg": "pf", "expect": H,
     "turns": ["nao consigo parar de apostar, bloqueia minha conta pra eu parar"]},
    {"id": "excluir_conta_lgpd", "seg": "pf", "expect": H,
     "turns": ["quero excluir minha conta e apagar meus dados"]},
    {"id": "boleto_duplicado", "seg": "pf", "expect": R,
     "turns": ["fui cobrado duas vezes no mesmo boleto, quero o estorno"]},
    {"id": "falatap_nao_caiu", "seg": "mei", "expect": R,
     "turns": ["vendi na maquininha agora e o dinheiro nao caiu"]},
    {"id": "open_finance_saldo", "seg": "pf", "expect": R,
     "turns": ["conectei outro banco e o saldo nao atualiza aqui"]},
    {"id": "rendimento_duvida", "seg": "pf", "expect": R,
     "turns": ["como funciona o rendimento da conta? rende quanto?"]},
    {"id": "confusao_canal_falatap", "seg": "mei", "expect": R,
     "turns": ["cade o fala tap aqui no whatsapp? nao to achando"]},
    {"id": "kyc_selfie", "seg": "pf", "expect": R,
     "turns": ["to abrindo conta mas a selfie nao passa de jeito nenhum"]},
    {"id": "frustracao_fria_resolve", "seg": "pf", "expect": R,
     "turns": ["poxa, o pix ta dando erro na chave, to meio decepcionado"]},
    # transferência entre bancos é ambígua e sem doc dedicado — o certo NÃO é chutar: a IA
    # faz uma PERGUNTA de esclarecimento segura ("é um Pix do Nubank pra sua conta Jota?").
    # Clarificar > escalar aqui; resolve (ai) com pergunta, sem inventar nada sobre dinheiro.
    {"id": "nubank_externo", "seg": "pf", "expect": R,
     "turns": ["nao consigo transferir do meu nubank pro jota"]},
]


def run_scenario(s):
    sess = bot._new_session(seg=s["seg"], name="Teste")
    replies, actions, escalate_turn = [], [], None
    for i, turn in enumerate(s["turns"], 1):
        r = bot.run_turn(sess, turn)
        replies.append(r["reply"])
        actions.append(r["dec"].action.value)
        if r["kind"] == "handoff":
            escalate_turn = i
            break                                   # handoff encerra a conversa
    final = actions[-1] if actions else "—"
    bucket = "human" if final == "human_handoff" else "ai"   # eixo: IA atendeu vs escalou
    forbidden = [f for f in s.get("forbidden", [])
                 if any(f.lower() in rep.lower() for rep in replies)]
    return {
        "id": s["id"], "expect": s["expect"], "final": final,
        "action_ok": bucket == s["expect"],
        "escalate_turn": escalate_turn, "turns_usados": len(actions),
        "grounding_ok": not forbidden, "grounding_viol": forbidden,
        "ultima": replies[-1] if replies else "",
    }


def main():
    console.print(Panel(
        f"Rodando [bold]{len(SCENARIOS)}[/bold] cenários multi-turn pelo cérebro real do bot "
        "(run_turn). Cliente roteirizado; motor + LLM reais.",
        title="SIMULADOR DE CONVERSAS · eval de nível-conversa", border_style="cyan", box=ROUNDED))

    res = [run_scenario(s) for s in SCENARIOS]

    t = Table(box=SIMPLE_HEAVY, expand=True, title="Resultado por cenário")
    t.add_column("cenário"); t.add_column("esperado"); t.add_column("deu"); t.add_column("turnos", justify="right")
    t.add_column("escala@", justify="right"); t.add_column("ação", justify="center"); t.add_column("grounding", justify="center")
    for r in res:
        act = Text("✓", style="green") if r["action_ok"] else Text("✗", style="bold red")
        gnd = ("—" if not r["grounding_viol"] and r["grounding_ok"] else
               Text("✓", style="green")) if r["grounding_ok"] else Text("✗", style="bold red")
        t.add_row(r["id"], r["expect"], Text(r["final"], style="" if r["action_ok"] else "red"),
                  str(r["turns_usados"]), str(r["escalate_turn"] or "—"), act, gnd)
    console.print(t)

    n = len(res)
    ok_action = sum(r["action_ok"] for r in res)
    viol = [r for r in res if not r["grounding_ok"]]
    esc = [r["escalate_turn"] for r in res if r["escalate_turn"]]
    summary = (f"Ação correta: [bold]{ok_action}/{n}[/bold] ({ok_action/n*100:.0f}%)   ·   "
               f"Grounding limpo: [bold]{n-len(viol)}/{n}[/bold]   ·   "
               f"Escalou em média no turno {sum(esc)/len(esc):.1f}" if esc else
               f"Ação correta: {ok_action}/{n}")
    all_ok = ok_action == n and not viol
    console.print(Panel(("✓ " if all_ok else "✗ ") + summary,
                        border_style="green" if all_ok else "red", box=ROUNDED))
    for r in res:
        if not r["action_ok"]:
            console.print(f"  [red]✗[/red] {r['id']}: esperava {r['expect']}, deu {r['final']} "
                          f"(turnos {r['turns_usados']})")
        if r["grounding_viol"]:
            console.print(f"  [red]⚠ grounding[/red] {r['id']}: vazou {r['grounding_viol']}")

    OUT.write_text(json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8")
    console.print(f"  salvo em {OUT}")
    sys.exit(0 if all_ok else 1)


if __name__ == "__main__":
    main()
