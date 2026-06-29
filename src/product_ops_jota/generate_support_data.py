"""
Atalaia · Support Lab — Gerador de conversas sintéticas (Mundo 1)
=================================================================
Gera N conversas REALISTAS e ROTULADAS no banco de suporte.

Tese de credibilidade do dado sintético:
  · o RÓTULO é determinístico (eu especifico tema/natureza/criticidade ao gerar) →
    sei a verdade de cada conversa, então posso avaliar se o classificador acerta.
  · o TEXTO é template variado (e, opcionalmente, LLM por cima) → realismo.
  · a distribuição é ancorada nos dados reais do ReclameAqui do Jota.

No canal de suporte só existem 2 naturezas (quem está aqui FALOU):
  SYSTEM_SIGNALED   = há evento de sistema correlacionado (kyc.failed, pix.returned…)
  BEHAVIOR_INFERRED = nada falhou no sistema; é confusão/atrito de jornada
(o ABSENCE_DETECTED — o silencioso — vive no Mundo 2, fora daqui.)
"""
from __future__ import annotations

import random
import sqlite3
from datetime import datetime, timedelta

# ─── Distribuição de temas (ancorada no ReclameAqui + produto) ──────────────
THEME_WEIGHTS = {
    "account_access": 0.18,   # "não consigo acessar" — o nº1 no ReclameAqui
    "pix": 0.22,              # loop de chave, limite parcelado
    "kyc": 0.15,              # "não liberam abertura de conta"
    "fala_tap": 0.15,         # "dinheiro bloqueado após venda"
    "boleto": 0.10,           # estorno, cobrança duplicada
    "account_data": 0.08,     # alteração/exclusão de dados
    "yield_open_finance": 0.07,
    "other": 0.05,
}

# P(SYSTEM_SIGNALED) por tema — o resto é BEHAVIOR_INFERRED
P_SYSTEM_SIGNALED = {
    "account_access": 0.20, "pix": 0.50, "kyc": 0.80, "fala_tap": 0.60,
    "boleto": 0.70, "account_data": 0.10, "yield_open_finance": 0.30, "other": 0.20,
}

# evento de sistema emitido quando SYSTEM_SIGNALED
EVENT_BY_THEME = {
    "account_access": "session.crashed", "pix": "pix.returned",
    "kyc": "kyc.failed", "fala_tap": "tap_to_pay.settlement_delayed",
    "boleto": "charge.duplicated", "account_data": "data_change.requested",
    "yield_open_finance": "open_finance.consent_expired", "other": "generic.error",
}

# Eventos de sistema SEM conversa (substrato que o Mundo 2 lê como atrito
# silencioso / absence_detected). NÃO é natureza de conversa de suporte: quem
# chega ao suporte levantou a mão. Pesos = onde o cliente mais sofre calado
# (KYC trava e ele desiste; Pix volta e ele não reclama).
ORPHAN_EVENT_WEIGHTS = {
    "kyc.failed": 0.28,
    "pix.returned": 0.22,
    "tap_to_pay.settlement_delayed": 0.18,
    "session.crashed": 0.12,
    "charge.duplicated": 0.10,
    "open_finance.consent_expired": 0.10,
}

# criticidade-base por tema (1–5) — eleva com contexto depois
BASE_CRITICALITY = {
    "account_access": 3, "pix": 3, "kyc": 3, "fala_tap": 4,
    "boleto": 3, "account_data": 2, "yield_open_finance": 2, "other": 2,
}

SEGMENTS = (["mei"] * 45 + ["pf"] * 40 + ["pj"] * 15)   # Jota é forte em empreendedor
AGE_BANDS = ["18-25", "26-40", "41-60", "60+"]
LITERACY = ["low", "medium", "high"]

# ─── Atendentes (nome + gênero p/ a saudação "Sou a/o ...") ──────────────────
AGENT_NAMES = ["Aline", "Gustavo", "Marina", "Rafael", "Beatriz", "Diego", "Camila"]
AGENT_GENDER = {"Aline": "f", "Gustavo": "m", "Marina": "f", "Rafael": "m",
                "Beatriz": "f", "Diego": "m", "Camila": "f"}

# Nomes de cliente (só vivem no TEXTO — não há coluna de nome no schema).
CUSTOMER_NAMES = [
    "Hugo", "Bruno", "Carla", "Diego", "Fernanda", "João", "Larissa", "Marcos",
    "Patrícia", "Rafael", "Tatiane", "Vinícius", "Camila", "Eduardo", "Juliana",
    "Renato", "Sabrina", "Thiago", "Aline", "Felipe", "Mariana", "Rodrigo",
    "Letícia", "Gustavo", "Priscila", "André", "Vanessa", "Lucas", "Débora", "Otávio",
]

# ─── Bancos de texto por tema (cliente) — variação p/ não repetir ───────────
CUSTOMER_OPENERS = ["olá", "oi", "bom dia", "boa tarde", "ola, tudo bem?",
                    "oi, preciso de ajuda", "boa noite", "e ai, tudo bem?"]
PROBLEM_TEXT = {
    "account_access": ["nao consigo acessar meu app", "o app fecha sozinho", "nao entro na conta de jeito nenhum",
                       "abre e fecha na hora", "to tentando entrar e nao vai"],
    "pix": ["nao consigo fazer um pix", "tentei mandar pix 3x e nao foi", "a chave nao funciona",
            "cade meu limite do pix parcelado?", "meu pix deu erro"],
    "kyc": ["nao consigo abrir minha conta", "minha conta nao foi liberada", "trava na hora da foto/selfie",
            "disseram que nao posso abrir conta", "a verificacao nao passa"],
    "fala_tap": ["fiz uma venda e o dinheiro nao caiu", "recebi pela maquininha mas nao to vendo o dinheiro",
                 "meu dinheiro ta bloqueado", "vendi e nao caiu na conta", "a venda aprovou mas cade o valor?"],
    "boleto": ["paguei a parcela duplicada", "preciso de estorno", "fui cobrado duas vezes",
               "paguei e continua em aberto", "quero meu dinheiro de volta"],
    "account_data": ["preciso alterar meus dados", "quero excluir minha conta", "mudar meu cadastro",
                     "atualizar meu telefone", "trocar email da conta"],
    "yield_open_finance": ["como funciona o rende+?", "meu open finance desconectou",
                           "nao to vendo meu rendimento", "conectei outro banco e sumiu"],
    "other": ["tenho uma duvida", "preciso de ajuda com uma coisa", "queria entender uma cobranca",
              "uma pergunta sobre a conta", "nao entendi uma coisa aqui"],
}
# Detalhe extra que o cliente manda em rajada logo após o problema (estilo WhatsApp).
PROBLEM_DETAIL = {
    "account_access": ["ja desinstalei e instalei de novo", "ta dando erro toda vez que abro",
                       "e meu salario ta na conta, preciso URGENTE", "ja troquei a senha e nada"],
    "pix": ["o dinheiro saiu da minha conta mas nao chegou", "a pessoa do outro lado ta esperando",
            "e um pagamento de fornecedor, nao posso atrasar", "ja conferi a chave 10x ta certa"],
    "kyc": ["mandei os documentos faz 3 dias", "a foto do RG nao quer aceitar de jeito nenhum",
            "preciso abrir pra receber um pagamento amanha", "ja tentei com RG e CNH"],
    "fala_tap": ["vendi 480 reais hoje de manha", "o cliente passou o cartao e aprovou na hora",
                 "preciso desse dinheiro pra pagar fornecedor", "ja se passaram horas e nada"],
    "boleto": ["debitou 2x no meu cartao", "tenho o comprovante dos dois pagamentos aqui",
               "isso ja aconteceu mes passado tambem", "preciso desse estorno pra fechar o mes"],
    "account_data": ["mudei de numero e nao consigo atualizar", "o sistema nao deixa salvar",
                     "preciso disso pra receber o codigo de acesso", "ja tentei pelo app e pelo site"],
    "yield_open_finance": ["conectei meu outro banco e o saldo sumiu", "nao aparece mais nada no rende+",
                           "ja desconectei e conectei de novo", "era pra estar rendendo e nao rende"],
    "other": ["é meio complicado de explicar", "nao sei se é aqui que pergunto isso",
              "ja procurei no app e nao achei", "queria so entender como funciona"],
}
# Atendente: pergunta de diagnóstico por tema.
AGENT_PROBE = {
    "account_access": "Quando você tenta acessar, aparece alguma mensagem de erro? Está no Android ou iPhone?",
    "pix": "Você consegue me confirmar o valor e se chegou a aparecer algum código de erro na tela?",
    "kyc": "Em qual etapa trava — no envio do documento ou na selfie? Aparece alguma mensagem?",
    "fala_tap": "Me confirma o valor da venda e o horário, por favor, que eu verifico a liquidação aqui.",
    "boleto": "Você tem os comprovantes dos dois débitos? Me passa as datas que eu localizo aqui.",
    "account_data": "Qual dado você precisa alterar? E está tentando pelo app ou pelo site?",
    "yield_open_finance": "Qual banco você conectou no Open Finance? E quando o saldo sumiu?",
    "other": "Entendi. Me conta com um pouco mais de detalhe pra eu te direcionar melhor?",
}
# Atendente: passo de troubleshooting / espera enquanto checa.
AGENT_STEP = {
    "account_access": "Vou pedir pra você limpar o cache do app e tentar de novo, pode ser?",
    "pix": "Deixa eu rastrear esse Pix pelo end-to-end aqui no sistema, um instante.",
    "kyc": "Vou reenviar sua análise de cadastro pro time de onboarding, só um momento.",
    "fala_tap": "Estou verificando o status da liquidação dessa venda no nosso sistema.",
    "boleto": "Localizei aqui, vou abrir a solicitação de estorno do valor duplicado.",
    "account_data": "Vou verificar por que o sistema não está salvando essa alteração.",
    "yield_open_finance": "Deixa eu checar o status da sua conexão de Open Finance.",
    "other": "Deixa eu verificar isso internamente pra te dar uma resposta certa.",
}
AGENT_HOLD = ["Só um momento que já verifico isso pra você.", "Deixa eu checar aqui no sistema, um instante.",
              "Estou olhando o seu caso agora, aguenta um pouquinho.", "Já te retorno com uma posição."]
CUSTOMER_DETAIL = ["é android", "to no iphone", "aparece sim uma mensagem de erro", "nao aparece nada, so trava",
                   "ja tentei isso e nao deu", "fiz exatamente isso e continua igual", "ok, fiz aqui", "ta feito"]

# Impaciência crescente (nível 1 → 3). Nível 3 só entra no arco furioso.
IMPATIENT = {
    1: ["e ai?", "tem alguem ai?", "oi???", "ainda ta ai?", "demora muito isso?", "?"],
    2: ["ja faz um tempão isso", "to esperando ate agora", "que demora hein", "ninguem responde?",
        "isso é serio?", "to esperando ha mais de uma hora"],
    3: ["alguem vai me responder ou nao???", "to esperando faz HORAS", "que palhaçada e essa???",
        "responde logo pelo amor de deus", "VOCES SUMIRAM?"],
}
# Palavrão / agressão — SÓ no arco furioso (intensidade "forte e real", confirmada).
PROFANITY = ["caralho que banco lixo", "porra nenhuma funciona nesse app", "que merda de atendimento e esse",
             "vão se foder", "to puto da vida com voces", "pqp que banco horrivel", "que bosta de suporte"]
THREATS = ["vou no procon", "vou postar tudo no reclame aqui", "vou processar voces",
           "vou cancelar tudo e fechar a conta", "ja to printando essa conversa", "nunca mais uso esse banco"]
# Pedido de humano, tom por nível.
HUMAN_DEMAND = {
    1: ["queria falar com um atendente de verdade", "tem como me passar pra um humano?"],
    2: ["me passa pra um humano por favor", "quero falar com alguem de verdade, nao com robo"],
    3: ["EU QUERO FALAR COM UM HUMANO AGORA", "para com isso e me passa pra uma PESSOA caralho",
        "QUERO UM ATENDENTE HUMANO JA"],
}
# Repetição quando context_lost (cliente repete info já dada, irritado).
REPEAT_PREFIX = ["ja falei isso", "como eu disse antes", "de novo a mesma pergunta?",
                 "eu JA expliquei isso pro outro atendente", "voce nao leu o que eu escrevi?"]


def _now_minus(days_back: float) -> datetime:
    return datetime(2026, 6, 27, 12, 0, 0) - timedelta(days=days_back)


def _pick(weights: dict) -> str:
    r, acc = random.random(), 0.0
    for k, w in weights.items():
        acc += w
        if r <= acc:
            return k
    return list(weights)[-1]


def generate(conn: sqlite3.Connection, n_conversations: int = 1000, seed: int = 42) -> None:
    random.seed(seed)
    cur = conn.cursor()

    # usuários: ~0.8 por conversa (alguns voltam mais de uma vez)
    n_users = int(n_conversations * 0.8)
    users = []
    for i in range(n_users):
        uid = f"u_{i:05d}"
        seg = random.choice(SEGMENTS)
        lit = random.choices(LITERACY, weights=[25, 50, 25])[0]
        age = random.choices(AGE_BANDS, weights=[20, 40, 30, 10])[0]
        signup = _now_minus(random.uniform(1, 540)).isoformat()
        users.append(uid)
        cur.execute("INSERT INTO users VALUES (?,?,?,?,?)", (uid, seg, signup, age, lit))

    for c in range(n_conversations):
        cid = f"c_{c:05d}"
        uid = random.choice(users)
        theme = _pick(THEME_WEIGHTS)
        nature = ("system_signaled" if random.random() < P_SYSTEM_SIGNALED[theme]
                  else "behavior_inferred")

        # criticidade: base × contexto (mid-sale no fala_tap, etapa frágil no kyc)
        crit = BASE_CRITICALITY[theme]
        if theme == "fala_tap" and random.random() < 0.5:
            crit = min(crit * 1.4, 5)             # atendendo cliente agora
        if theme == "kyc":
            crit = min(crit * 1.2, 5)             # onboarding frágil
        crit = round(min(crit + random.uniform(-0.5, 0.5), 5.0), 2)
        crit = max(crit, 1.0)

        # perfil/sinais de qualidade
        frustrated = random.random() < (0.25 + 0.12 * (crit - 3))
        asked_human = 1 if (frustrated and random.random() < 0.6) else 0
        handoff_done = 1 if (asked_human and random.random() < 0.7) else 0
        agent_switch = random.random() < 0.15
        context_lost = 1 if (agent_switch and random.random() < 0.5) else 0
        sent_start = round(random.uniform(-0.3, 0.1), 2)

        # desfecho condicionado à frustração/resolubilidade
        if frustrated:
            outcome = random.choices(["resolved", "escalated", "abandoned", "no_response"],
                                     weights=[35, 30, 25, 10])[0]
        else:
            outcome = random.choices(["resolved", "escalated", "abandoned", "no_response"],
                                     weights=[70, 8, 12, 10])[0]
        sent_end = round(random.uniform(0.2, 0.8) if outcome == "resolved"
                         else random.uniform(-0.9, -0.2), 2)

        started = _now_minus(random.uniform(0, 90))
        cur.execute("INSERT INTO conversations VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (cid, uid, "support", started.isoformat(), theme, nature, crit, outcome,
                     asked_human, handoff_done, context_lost, sent_start, sent_end))

        # evento correlacionado (só SYSTEM_SIGNALED)
        if nature == "system_signaled":
            evt_at = (started - timedelta(minutes=random.randint(1, 30))).isoformat()
            cur.execute("INSERT INTO events VALUES (?,?,?,?,?)",
                        (f"e_{c:05d}", uid, cid, EVENT_BY_THEME[theme], evt_at))

        # mensagens: monta a conversa
        _generate_messages(cur, cid, theme, outcome, asked_human, handoff_done,
                           agent_switch, context_lost, frustrated, crit,
                           started, mostly_audio=(random.random() < 0.2))

    # ── Eventos de sistema SEM conversa (conversation_id = NULL) ──────────────
    # O cliente teve a falha e NÃO reclamou. Isso NÃO é uma natureza do suporte
    # (no canal de suporte só há 2 naturezas); é o substrato que o Mundo 2 lê
    # depois como atrito silencioso. Gerado DEPOIS do loop de conversas de
    # propósito — assim não desloca a sequência aleatória e cada conversa acima
    # continua idêntica (determinismo). IDs neutros, continuando a sequência
    # `e_xxxxx` após o range das conversas para não colidir com os correlacionados.
    n_orphan = int(n_conversations * 0.4)
    for k in range(n_orphan):
        uid = random.choice(users)
        evt = _pick(ORPHAN_EVENT_WEIGHTS)
        evt_at = _now_minus(random.uniform(0, 90)).isoformat()
        cur.execute("INSERT INTO events VALUES (?,?,?,?,?)",
                    (f"e_{n_conversations + k:05d}", uid, None, evt, evt_at))

    conn.commit()


def _saudacao(agente: str, nome: str) -> str:
    """Saudação inicial no formato real do Jota (gênero-aware)."""
    artigo = "a" if AGENT_GENDER.get(agente) == "f" else "o"
    return (f"Oi, {nome}! Sou {artigo} {agente}, do suporte do Jota, "
            f"pode contar comigo por aqui. Me diz como posso te ajudar hoje?")


def _arco_frustracao(frustrated: bool, crit: float) -> int:
    """Nível 0-3 (calmo → impaciente → irritado → furioso). Governa tom e palavrão.

    Só o nível 3 (minoria das conversas frustradas) usa palavrão explícito.
    """
    if not frustrated:
        arco = 0 if random.random() < 0.7 else 1
    else:
        arco = random.choices([1, 2, 3], weights=[30, 45, 25])[0]
    if crit >= 4.5 and arco < 3 and random.random() < 0.4:
        arco += 1
    return arco


def _gap(curto: tuple, longo: tuple, p_longo: float) -> float:
    """Sorteia um intervalo em minutos: curto na maioria, cauda longa às vezes."""
    return random.uniform(*longo) if random.random() < p_longo else random.uniform(*curto)


def _generate_messages(cur, cid, theme, outcome, asked_human, handoff_done,
                       agent_switch, context_lost, frustrated, crit,
                       started, mostly_audio):
    t = started
    turn = 0
    agent_a = random.choice(AGENT_NAMES)
    agent_b = random.choice([a for a in AGENT_NAMES if a != agent_a])
    nome = random.choice(CUSTOMER_NAMES)
    arco = _arco_frustracao(frustrated, crit)
    modality = "audio" if mostly_audio else "text"
    # conversa "lenta" = esperas longas acumulam horas (SLA real do Jota ~33h).
    # Minoria: a maioria resolve em ~1-2h; a cauda longa puxa o pior caso.
    lenta = random.random() < 0.22

    def add(sender, text, agent=None, gap_min=0.0):
        nonlocal t, turn
        t = t + timedelta(minutes=gap_min)
        cur.execute("INSERT INTO messages VALUES (?,?,?,?,?,?,?,?)",
                    (f"m_{cid}_{turn:02d}", cid, turn, sender, agent, t.isoformat(),
                     modality if sender == "customer" else "text", text))
        turn += 1

    _detalhe_usado: list[str] = []

    def detalhe() -> str:
        """Resposta do cliente sem repetir as 2 últimas (evita soar robótico)."""
        opts = [d for d in CUSTOMER_DETAIL if d not in _detalhe_usado[-2:]]
        d = random.choice(opts or CUSTOMER_DETAIL)
        _detalhe_usado.append(d)
        return d

    def espera_cliente(nivel_min: int = 1):
        """Cliente cobra resposta enquanto espera (intensidade pelo arco)."""
        nivel = min(arco, 3) if arco >= nivel_min else 0
        if nivel >= 1:
            add("customer", random.choice(IMPATIENT[min(nivel, 3)]),
                gap_min=_gap((2, 18), (40, 150), 0.35 if lenta else 0.1))

    # ── 1. abertura ──────────────────────────────────────────────────────────
    add("customer", random.choice(CUSTOMER_OPENERS))
    # 1ª resposta humana: o gap que define o SLA (cauda longa = SLA ruim)
    add("human_agent", _saudacao(agent_a, nome), agent=agent_a,
        gap_min=_gap((0.5, 18), (60, 1600), 0.35 if lenta else 0.12))

    # ── 2. cliente expõe o problema (em rajadas, estilo WhatsApp) ─────────────
    add("customer", random.choice(PROBLEM_TEXT[theme]), gap_min=_gap((0.3, 4), (5, 20), 0.2))
    if random.random() < 0.7:
        add("customer", random.choice(PROBLEM_DETAIL[theme]), gap_min=random.uniform(0.2, 2))
    espera_cliente(nivel_min=2)

    # ── 3. diagnóstico: atendente pergunta, cliente responde ──────────────────
    add("human_agent", AGENT_PROBE[theme], agent=agent_a, gap_min=_gap((1, 15), (40, 300), 0.2 if lenta else 0.07))
    add("customer", detalhe(), gap_min=_gap((0.5, 5), (10, 50), 0.1))

    # ── 4. rodadas de troubleshooting (1-3) ───────────────────────────────────
    rodadas = random.choices([1, 2, 3], weights=[40, 40, 20])[0]
    falas_agente = [AGENT_STEP[theme]] + AGENT_HOLD
    _agente_usado: list[str] = []
    for i in range(rodadas):
        opts = [f for f in falas_agente if f not in _agente_usado[-1:]]
        fala = random.choice(opts or falas_agente)
        _agente_usado.append(fala)
        add("human_agent", fala,
            agent=agent_a, gap_min=_gap((1, 12), (30, 240), 0.22 if lenta else 0.08))
        espera_cliente(nivel_min=1)
        add("customer", detalhe(), gap_min=_gap((0.5, 6), (15, 70), 0.12))

    # ── 5. troca de atendente → é o que GERA perda de contexto ────────────────
    agente_final = agent_a
    if agent_switch:
        add("human_agent", "Vou transferir você para um especialista, só um momento.",
            agent=agent_a, gap_min=random.uniform(2, 40))
        add("human_agent", f"Oi {nome}, sou {('a' if AGENT_GENDER.get(agent_b)=='f' else 'o')} "
            f"{agent_b} e vou continuar seu atendimento. Pode me confirmar o que aconteceu?",
            agent=agent_b, gap_min=_gap((2, 20), (40, 240), 0.2 if lenta else 0.07))
        agente_final = agent_b
        if context_lost:
            # cliente repete, irritado, a informação que já tinha dado
            add("customer", f"{random.choice(REPEAT_PREFIX)}, {random.choice(PROBLEM_TEXT[theme])}",
                gap_min=random.uniform(0.5, 6))

    # ── 6. pedido de humano (tom pelo arco) e eventual handoff ────────────────
    if asked_human:
        add("customer", random.choice(HUMAN_DEMAND[max(1, min(arco, 3))]),
            gap_min=random.uniform(0.5, 8))
        if handoff_done:
            add("human_agent", "Claro, já estou te transferindo para um especialista humano, tá?",
                agent=agente_final, gap_min=random.uniform(1, 20))

    # ── 7. explosão do arco furioso (palavrão + ameaça), só nível 3 ───────────
    if arco >= 3:
        add("customer", random.choice(PROFANITY), gap_min=random.uniform(0.3, 5))
        if random.random() < 0.7:
            add("customer", random.choice(THREATS), gap_min=random.uniform(0.2, 3))

    # ── 8. fecho conforme desfecho ────────────────────────────────────────────
    if outcome == "resolved":
        add("human_agent", f"Pronto, {nome}! Resolvido por aqui. Posso te ajudar em mais alguma coisa?",
            agent=agente_final, gap_min=_gap((2, 25), (45, 200), 0.15))
        if arco >= 2:
            add("customer", random.choice(["ate que enfim", "demorou mas resolveu", "ok, obrigado",
                                           "beleza, mas que demora hein"]), gap_min=random.uniform(0.5, 8))
        else:
            add("customer", random.choice(["obrigado!", "valeu", "showw", "perfeito, muito obrigado",
                                           "ótimo, era isso"]), gap_min=random.uniform(0.5, 8))
    elif outcome == "escalated":
        protocolo = f"#2026-{random.randint(10000, 99999)}"
        add("human_agent", f"Vou abrir um protocolo e encaminhar pro time responsável: {protocolo}. "
            f"Você recebe retorno por aqui, {nome}.", agent=agente_final, gap_min=random.uniform(2, 60))
        if random.random() < 0.6:
            add("customer", random.choice(["e quanto tempo isso vai demorar?", "de novo isso...",
                                           "ta, mas eu preciso resolver hoje", "ok aguardo"]),
                gap_min=random.uniform(0.5, 10))
    elif outcome == "abandoned":
        # cliente desiste — surto e some, ou silêncio
        if arco >= 2 and random.random() < 0.7:
            add("customer", random.choice(["esquece", "deixa pra la", "vou cancelar tudo mesmo",
                                           "perdi meu tempo aqui", "..."]), gap_min=random.uniform(1, 30))
    elif outcome == "no_response":
        pass  # o atendimento nunca responde de volta — o silêncio que mais dói
