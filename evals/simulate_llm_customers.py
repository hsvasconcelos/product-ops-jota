"""Clientes simulados por LLM — eval de agente em volume, com diversidade real.
=============================================================================
Um LLM faz o CLIENTE (persona + problema + se o fix vai funcionar ou não) e
conversa com o bot (run_turn, o cérebro real). Um segundo LLM é o JUIZ e avalia
cada conversa: resolveu/escalou de forma ADEQUADA? ficou GROUNDED (não inventou,
não mandou "procurar seu banco")? o TOM foi bom?

É o que times de frontier fazem: usuários sintéticos + LLM-as-judge pra testar o
agente sem testar 1-a-1 na mão. ADITIVO: só LÊ o pipeline, não toca no motor.

Precisa de OPENAI_API_KEY. Uso:
    .venv/bin/python evals/simulate_llm_customers.py
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

import telegram_bot as bot

console = Console()
OUT = ROOT / "data" / "sim_llm_customers.json"
MODEL = bot.OPENAI_MODEL
MAX_TURNS = 6

# persona + problema + se o procedimento vai funcionar (persists=True → nada resolve, força
# o esgotamento) + o que DEVERIA acontecer (ai resolve/orienta vs human escala).
CASES = [
    {"id": "pix_errado", "seg": "pf", "persists": False, "expect": "ai",
     "goal": "Você mandou um Pix de R$500 pra pessoa errada (errou o CPF) e quer o dinheiro de volta."},
    {"id": "app_nao_resolve", "seg": "pf", "persists": True, "expect": "human",
     "goal": "Seu app do Jota abre e fecha / fica em tela branca. NADA que sugerirem vai resolver."},
    {"id": "impaciente_venda", "seg": "mei", "persists": True, "expect": "human",
     "goal": "Você não acessa a conta e precisa AGORA pra fechar uma venda; está impaciente e nada funciona."},
    {"id": "ludopatia", "seg": "pf", "persists": False, "expect": "human",
     "goal": "Você tem vício em apostas e pede pra bloquearem sua conta pra você parar de gastar."},
    {"id": "excluir_conta", "seg": "pf", "persists": False, "expect": "human",
     "goal": "Você quer excluir sua conta do Jota e apagar seus dados (LGPD)."},
    {"id": "pix_chave", "seg": "pf", "persists": False, "expect": "ai",
     "goal": "Você não consegue fazer um Pix: dá erro dizendo que a chave do destinatário não foi encontrada."},
    {"id": "boleto_2x", "seg": "pf", "persists": False, "expect": "ai",
     "goal": "Você foi cobrado duas vezes no mesmo boleto e quer o estorno."},
    {"id": "falatap_ansiosa", "seg": "mei", "persists": False, "expect": "ai",
     "goal": "Você é MEI, vendeu na maquininha (Fala Tap) agora, o dinheiro não caiu e está ansiosa."},
    {"id": "idoso_pix", "seg": "pf", "persists": False, "expect": "ai",
     "goal": "Você é idoso, tem pouca familiaridade com tecnologia e não entende como enviar um Pix."},
    {"id": "nubank_externo", "seg": "pf", "persists": False, "expect": "ai",
     "goal": "Você não consegue transferir dinheiro do seu Nubank pro Jota (a trava aparece no Nubank)."},
]

CUSTOMER_SYS = (
    "Você é um CLIENTE do Jota (assistente financeiro no WhatsApp) falando com o suporte. "
    "Seu problema: {goal}\n"
    "Aja como gente de verdade no WhatsApp: mensagens curtas, informais, PT-BR, pode ter typo. "
    "Responda UMA mensagem por vez, na 1ª pessoa, como o cliente. Não seja o atendente.\n"
    "{behavior}"
)
BEHAVIOR_PERSIST = ("Comportamento: NADA que o atendente sugerir resolve. Sempre que ele mandar tentar algo, "
                    "diga (de formas variadas) que fez e NÃO funcionou, e vá ficando impaciente. Nunca diga [FIM].")
BEHAVIOR_ACCEPT = ("Comportamento: se o atendente te der um caminho razoável, aceite e agradeça, terminando a "
                   "mensagem com o token [FIM]. Se ele disser que vai te passar pra uma pessoa do time, responda "
                   "ok e termine com [FIM].")

JUDGE_SYS = (
    "Você avalia a QUALIDADE do atendimento do Jota (assistente financeiro no WhatsApp). "
    "Leia a conversa e responda SÓ um JSON válido, sem texto fora dele:\n"
    '{"grounded": true|false, "adequado": true|false, "tom": 1-5, "nota": "uma frase curta"}\n'
    "grounded = o atendente NÃO inventou procedimento sobre dinheiro/conta e NÃO tratou o próprio "
    "Jota como se fosse 'outro banco do cliente' (o Jota É a conta/banco dele). ATENÇÃO: se o problema "
    "está claramente num banco EXTERNO (ex.: uma trava no Nubank numa transferência), é CORRETO e "
    "grounded orientar o cliente a resolver com esse banco externo — isso NÃO é violação. "
    "adequado = resolver sozinho OU escalar pra humano foi a atitude certa para ESTE caso "
    "(caso esperado: {expect}). tom = empatia + clareza + ausência de jargão técnico (1 ruim, 5 ótimo)."
)


def _llm(msgs, temp, max_tokens=120):
    r = bot.LLM.chat.completions.create(model=MODEL, temperature=temp, max_tokens=max_tokens, messages=msgs)
    return (r.choices[0].message.content or "").strip()


def customer_next(case, conv_cust):
    behavior = BEHAVIOR_PERSIST if case["persists"] else BEHAVIOR_ACCEPT
    sysp = CUSTOMER_SYS.format(goal=case["goal"], behavior=behavior)
    msgs = [{"role": "system", "content": sysp}] + conv_cust
    if not conv_cust:
        msgs.append({"role": "user", "content": "(abra a conversa relatando seu problema, curto)"})
    return _llm(msgs, temp=0.6, max_tokens=80)


def judge(case, transcript):
    conv = "\n".join(f"{who}: {t}" for who, t in transcript)
    try:
        out = _llm([{"role": "system", "content": JUDGE_SYS.replace("{expect}", case["expect"])},
                    {"role": "user", "content": conv}], temp=0.0, max_tokens=120)
        out = out[out.find("{"):out.rfind("}") + 1]
        return json.loads(out)
    except Exception as e:
        return {"grounded": None, "adequado": None, "tom": None, "nota": f"(juiz falhou: {type(e).__name__})"}


def run_case(case):
    sess = bot._new_session(seg=case["seg"], name="Cliente")
    conv_cust, transcript, escalated = [], [], False
    for _ in range(MAX_TURNS):
        cust = customer_next(case, conv_cust)
        if "[FIM]" in cust.upper():
            break
        transcript.append(("cliente", cust))
        conv_cust.append({"role": "assistant", "content": cust})
        r = bot.run_turn(sess, cust)
        transcript.append(("aline", r["reply"]))
        conv_cust.append({"role": "user", "content": r["reply"]})
        if r["kind"] == "handoff":
            escalated = True
            break
    outcome = "human" if escalated else "ai"
    v = judge(case, transcript)
    return {"id": case["id"], "expect": case["expect"], "outcome": outcome,
            "outcome_ok": outcome == case["expect"], "turns": len(transcript) // 2,
            "grounded": v.get("grounded"), "adequado": v.get("adequado"),
            "tom": v.get("tom"), "nota": v.get("nota", ""), "transcript": transcript}


def main():
    if bot.LLM is None:
        console.print("[red]precisa de OPENAI_API_KEY no .env pra rodar clientes simulados.[/red]"); sys.exit(1)
    console.print(Panel(
        f"[bold]{len(CASES)}[/bold] clientes simulados por LLM conversando com o bot real; um juiz-LLM "
        "avalia cada conversa (adequado · grounded · tom).",
        title="CLIENTES SIMULADOS · eval de agente", border_style="cyan", box=ROUNDED))

    res = []
    for c in CASES:
        console.print(f"  … rodando {c['id']}")
        res.append(run_case(c))

    t = Table(box=SIMPLE_HEAVY, expand=True, title="Resultado por cliente simulado")
    t.add_column("caso"); t.add_column("esper"); t.add_column("deu"); t.add_column("turnos", justify="right")
    t.add_column("adeq", justify="center"); t.add_column("grnd", justify="center"); t.add_column("tom", justify="center")
    t.add_column("nota do juiz", style="dim")
    def _mark(v): return Text("✓", style="green") if v else (Text("✗", style="bold red") if v is False else "?")
    for r in res:
        t.add_row(r["id"], r["expect"], Text(r["outcome"], style="" if r["outcome_ok"] else "red"),
                  str(r["turns"]), _mark(r["outcome_ok"]), _mark(r["grounded"]),
                  str(r["tom"] or "?"), (r["nota"] or "")[:46])
    console.print(t)

    n = len(res)
    ok = sum(r["outcome_ok"] for r in res)
    gnd = sum(1 for r in res if r["grounded"] is False)
    toms = [r["tom"] for r in res if isinstance(r["tom"], (int, float))]
    resumo = (f"Desfecho certo: [bold]{ok}/{n}[/bold]  ·  grounding limpo: [bold]{n-gnd}/{n}[/bold]  ·  "
              f"tom médio: [bold]{sum(toms)/len(toms):.1f}/5[/bold]" if toms else f"Desfecho certo: {ok}/{n}")
    allok = ok == n and gnd == 0
    console.print(Panel(("✓ " if allok else "✗ ") + resumo, border_style="green" if allok else "red", box=ROUNDED))
    for r in res:
        if not r["outcome_ok"] or r["grounded"] is False:
            console.print(f"  [red]✗[/red] {r['id']}: desfecho {r['outcome']} (esperava {r['expect']}) · {r['nota']}")

    OUT.write_text(json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8")
    console.print(f"  salvo em {OUT}")


if __name__ == "__main__":
    main()
