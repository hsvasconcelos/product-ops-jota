"""Demo · Jota — front web estilo WhatsApp pra apresentar o case ao vivo.
=============================================================================
Sidebar de conversas (cada uma = um cenário real). Dois modos, a tese visual:

  · MUNDO 2 — PROATIVO (produto): o Jota detecta o atrito e fala PRIMEIRO,
    ancorado num procedimento. É o cliente-facing (topo da escada de adoção).
  · MUNDO 1 — REATIVO (atendimento): o cliente escreve no suporte e a IA
    SUGERE ao atendente (copiloto, Estágio 1 — IA sugere, humano decide).

Roda o PIPELINE REAL (classifier → RAG → decisão) + geração OpenAI grounded,
100% local — sem WhatsApp/Evolution, à prova de falha. O painel "cérebro"
mostra a detecção e o roteamento pra narrar ao vivo.

Rodar:
    .venv/bin/uvicorn apps.web_demo:app --port 8000   # abre http://localhost:8000
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from pydantic import BaseModel

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "apps"))

# carrega .env sem dependência extra (pra OPENAI_API_KEY ao rodar local)
_envf = ROOT / ".env"
if _envf.exists():
    for _l in _envf.read_text().splitlines():
        _l = _l.strip()
        if _l and not _l.startswith("#") and "=" in _l:
            _k, _v = _l.split("=", 1)
            os.environ.setdefault(_k, _v)

from product_ops_jota.channels import WhatsAppLine, proactive_line      # noqa: E402
from product_ops_jota.classifier import classify_conversation           # noqa: E402
from product_ops_jota.decision import decide                            # noqa: E402
from product_ops_jota.friction_model import (                            # noqa: E402
    InterceptionAction, SupportTheme, FrictionNature,
    LOOP_CHAVE_PIX, KYC_FALHOU, FALA_TAP_INSEGURANCA,
)
from product_ops_jota.rag import Retriever                              # noqa: E402
from product_ops_jota.decision import explain_gates, derive_resolubilidade, DEFAULT_THRESHOLDS  # noqa: E402
from product_ops_jota.friction_model import DEFAULT_CONFIDENCE                # noqa: E402
from product_ops_jota.handoff import build_context_pack                 # noqa: E402
import telegram_bot as brain     # noqa: E402  — o MESMO cérebro do bot em prod (import não dispara polling)
from copiloto import ACK_BY_THEME, derivar_decisao, montar_sugestao     # noqa: E402

OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-4o")
TOP_K = 2
# o gerar() só roda no proativo → suprimimos passos de escalação humana aqui.
_ESC = ("suporte", "atendente", "humano", "manualmente", "manual")

DATA = json.loads((ROOT / "data" / "conversas_ruins.json").read_text("utf-8"))
SCEN = {c["id"]: c for c in DATA}

# nome amigável, modo e emoji por cenário (config da demo — fácil de re-taggear).
# Proativos primeiro (produto/Mundo 2), depois reativos (atendimento/Mundo 1).
UI = {
    "ex_estorno_duplicado":    ("Parcela cobrada 2x",            "proativo", "🔁"),
    "ex_pix_loop":             ("Cliente PF · loop de Pix",      "proativo", "🔑"),
    "ex_kyc_falhou":           ("Onboarding · KYC falhou",       "proativo", "🪪"),
    "ex_fala_tap_inseguranca": ("Vendedora MEI · Fala Tap",      "proativo", "💳"),
    "ex_kyc_limbo":            ("Conta no limbo (sem evento)",   "proativo", "⏳"),
    "c_real_001":              ("Caso REAL · app fecha sozinho", "reativo",  "📱"),
    "ex_boleto_duplicado":     ("Boleto cobrado 2x",             "reativo",  "🧾"),
    "ex_open_finance":         ("Open Finance · saldo sumiu",    "reativo",  "🏦"),
    "ex_pix_pessoa_errada":    ("Pix pra pessoa ERRADA",         "reativo",  "⚠️"),
    "ex_excluir_conta":        ("Excluir conta / dados",         "reativo",  "🗑️"),
}
LINE_LABEL = {
    WhatsAppLine.SUPPORT: "Suporte", WhatsAppLine.PRODUCT_PF: "Produto · PF",
    WhatsAppLine.PRODUCT_PJ: "Produto · PJ/MEI",
}
ACTION_LABEL = {
    InterceptionAction.AI_RESOLVE: "IA resolve in-thread",
    InterceptionAction.AI_RESOLVE_SILENT: "IA resolve em background",
    InterceptionAction.AI_ASSIST: "IA assiste (humano no loop)",
    InterceptionAction.HUMAN_HANDOFF: "Humano (handoff quente)",
    InterceptionAction.NO_INTERCEPT: "Não interceptar (observar)",
}

# liga os cenários proativos aos casos-herói do friction_model → mostra a REGRA
# objetiva que dispara a detecção (não só palavras-chave).
CASE_OF = {
    "ex_pix_loop": LOOP_CHAVE_PIX, "ex_kyc_falhou": KYC_FALHOU,
    "ex_fala_tap_inseguranca": FALA_TAP_INSEGURANCA,
}


# O QUE O SISTEMA JÁ SABE por cenário (eventos + base de dados). É o que separa o
# proativo de alto nível: o Jota AFIRMA e RESOLVE com esses dados — não pergunta o
# que já tem. (perfil ajusta o tom: literacia/idade/contexto.)
FACTS = {
    "ex_estorno_duplicado": {
        "fatos": ("O QUÊ: a cobrança duplicada é a PARCELA do empréstimo do cliente (R$ 318,40), "
                  "que é agendada e cobrada todo mês — então é uma cobrança esperada, só que "
                  "processada duas vezes. POR QUÊ: às 08:14 uma instabilidade momentânea no "
                  "processamento registrou a mesma parcela 2x. IMPACTO: os R$ 318,40 a mais "
                  "saíram da conta, MAS o estorno automático já foi disparado e o valor volta em "
                  "até 1 dia útil — o saldo final do cliente não muda. RECORRÊNCIA: foi um caso "
                  "isolado, já sinalizado ao time; não vai se repetir. A cobrança original "
                  "(a parcela legítima) é mantida normalmente."),
        "perfil": "aja ANTES da reclamação — informe que já resolveu, antecipando as dúvidas",
    },
    "ex_pix_loop": {
        "fatos": ("O cliente tentou enviar o MESMO Pix 4x nos últimos ~10 min. A chave que ele "
                  "está usando (o CPF) NÃO está cadastrada como chave Pix do destinatário em "
                  "nenhum banco — por isso trava. O saldo do cliente é suficiente. Nenhuma falha "
                  "de sistema: o problema é a chave do destino, não o Jota."),
        "perfil": "primeira semana de conta, baixa familiaridade digital",
    },
    "ex_kyc_falhou": {
        "fatos": ("Onboarding em andamento. A validação da biometria (selfie) falhou 1 vez. As "
                  "causas mais comuns desse erro são iluminação ruim, foto tremida ou rosto "
                  "coberto (óculos/boné/máscara). A conta ainda não foi ativada."),
        "perfil": "onboarding, possivelmente baixa familiaridade digital",
    },
    "ex_fala_tap_inseguranca": {
        "fatos": ("Venda no Fala Tap APROVADA hoje de manhã, no valor de R$480,00. O plano de "
                  "recebimento do cliente é D1 (cai no próximo dia útil). A liquidação é "
                  "GARANTIDA pelo Jota — o dinheiro não corre risco. Vendedora MEI, 1ª semana."),
        "perfil": "MEI nova (primeira semana), insegura sobre o recebimento",
    },
    "ex_kyc_limbo": {
        "fatos": ("O cliente iniciou o onboarding mas NÃO concluiu. Não houve evento de conclusão "
                  "nem de falha há mais de 1h (limbo) — ele parou numa etapa silenciosamente, sem "
                  "erro. Falta pouco pra ativar a conta."),
        "perfil": "onboarding travado, reengajar com leveza",
    },
}


# TIMELINE coreografada de cada cena proativa: o atrito NASCENDO em tempo real
# (ações do cliente + eventos do sistema pipocando) até CRUZAR o limiar → interceptação.
# t = ms de espera antes de mostrar o beat. Cena roteirizada; a detecção/decisão que
# roda em cima dela é real.
TIMELINE = {
    "ex_estorno_duplicado": [
        {"t": 600, "tipo": "evento", "sinal": "payment.charged",
         "detalhe": "parcela do empréstimo · R$ 318,40 · 08:14:02"},
        {"t": 1600, "tipo": "evento", "sinal": "payment.charged", "conta": "DUPLICADA",
         "detalhe": "mesma parcela, mesmo valor, 08:14:05 → cobrança em duplicidade", "trip": True},
    ],
    "ex_pix_loop": [
        {"t": 500, "tipo": "user", "texto": "oi, preciso mandar um pix pra uma pessoa"},
        {"t": 900, "tipo": "jota", "texto": "Claro! Me passa a *chave Pix* (CPF, e-mail, telefone "
         "ou aleatória) e o *valor* que você quer enviar que eu já preparo aqui. 😉"},
        {"t": 1700, "tipo": "user", "texto": "a chave é o cpf dela: 092.748.994-513"},
        {"t": 800, "tipo": "evento", "sinal": "pix.key.invalid", "conta": "1ª",
         "detalhe": "chave informada tem 12 dígitos — CPF tem 11"},
        {"t": 700, "tipo": "jota", "texto": "Opa, essa chave tem *12 dígitos* — o CPF tem 11. "
         "Confere aí e me manda o certinho, junto com o valor. 🙏"},
        {"t": 1700, "tipo": "user", "texto": "ué, não foi não? deixa eu tentar de novo: 092.748.994-513"},
        {"t": 800, "tipo": "evento", "sinal": "pix.key.invalid", "conta": "2ª",
         "detalhe": "mesma chave inválida de novo, em < 2 min → loop", "trip": True},
    ],
    "ex_kyc_falhou": [
        {"t": 500, "tipo": "user", "texto": "quero abrir minha conta"},
        {"t": 1300, "tipo": "evento", "sinal": "kyc.biometrics.started",
         "detalhe": "validação de selfie iniciada"},
        {"t": 1800, "tipo": "evento", "sinal": "kyc.failed",
         "detalhe": "biometria não validou (momento mais frágil: antes do 1º valor)", "trip": True},
    ],
    "ex_fala_tap_inseguranca": [
        {"t": 500, "tipo": "evento", "sinal": "tap_to_pay.payment_approved",
         "detalhe": "venda R$480,00 aprovada · plano de recebimento D1"},
        {"t": 1600, "tipo": "user", "texto": "vendi agora pela maquininha mas não caiu nada"},
        {"t": 1500, "tipo": "user", "texto": "tá certo isso? fico insegura, é bastante dinheiro",
         "sinal": "receipt_anxiety_intent ↑ 0.82", "detalhe": "ansiedade de recebimento ≥ 0.7", "trip": True},
    ],
    "ex_kyc_limbo": [
        {"t": 500, "tipo": "evento", "sinal": "kyc.started", "detalhe": "onboarding iniciado"},
        {"t": 1400, "tipo": "evento", "sinal": "kyc.identity_verified", "detalhe": "identidade ok"},
        {"t": 2000, "tipo": "ausencia", "sinal": "onboarding.completed",
         "detalhe": "evento ESPERADO que não ocorre — ⏱ 60+ min de silêncio, sem erro", "trip": True},
    ],
}


def _regra_for(sid):
    """Descrição legível da regra de detecção objetiva que dispara o atrito."""
    case = CASE_OF.get(sid)
    if case:
        r = case.detection
        if r.nature is FrictionNature.SYSTEM_SIGNALED:
            txt = f"evento de sistema: {r.event_type}"
        elif r.nature is FrictionNature.BEHAVIOR_INFERRED:
            txt = f"comportamento: {r.behavior_feature} ≥ {r.threshold} (janela {r.window_minutes} min)"
        else:
            txt = f"ausência: {r.expected_event} não ocorre em {r.baseline_window_minutes} min"
        return {"nature": r.nature.value, "texto": txt, "confianca": r.base_confidence}
    if sid == "ex_kyc_limbo":
        return {"nature": "absence_detected", "confianca": 0.75,
                "texto": "ausência: onboarding.completed não ocorre na janela (sem evento de falha)"}
    if sid == "ex_estorno_duplicado":
        return {"nature": "system_signaled", "confianca": 1.0,
                "texto": "evento: 2 cobranças idênticas (mesmo valor + destino) em < 10s → duplicidade"}
    return None


retriever = Retriever()
SESSIONS: dict[str, dict] = {}
app = FastAPI()


# ─── geração: OpenAI grounded, fallback template ─────────────────────────────
def _llm():
    if not os.environ.get("OPENAI_API_KEY"):
        return None
    try:
        from openai import OpenAI
        return OpenAI()
    except Exception:
        return None


LLM = _llm()
SEG_NOME = {"pf": "pessoa física", "pj": "pessoa jurídica", "mei": "MEI"}


# Guia de voz extraído de conversas REAIS do Jota (WhatsApp). É o que faz o tom bater.
JOTA_VOICE = """Você é o Jota — assistente financeiro que vive no WhatsApp. Sua voz:
· Calorosa, animada e otimista, mas DIRETA. PT-BR informal com "você".
· Curto, mas COMPLETO. É WhatsApp: vá direto, sem sermão nem lista numerada (passos vão no
  painel). MAS nunca deixe o cliente com uma dúvida que a SUA mensagem criou — isso gera mais
  ansiedade que silêncio. Quando você traz uma novidade (ex.: "foi cobrado 2x", "sua venda
  travou"), antecipe e responda na MESMA mensagem, em tom natural: o QUÊ (sobre o que é, de
  forma reconhecível pro cliente), o PORQUÊ (motivo simples), se AFETA o cliente e como você já
  resolve, e se vai SE REPETIR.
· Numa transação simples (pedir chave e valor), aí sim seja minimalista — uma coisa por vez.
· Valide de forma CONCRETA, como o Jota real ("essa chave tem 12 dígitos, o CPF tem 11; confere?").
· Gírias leves do dia a dia, sem exagero: "Show", "Boa!", "Opa", "Poxa", "Bora",
  "beleza?", "valeu", "numa boa", "tô", "pra", "5 minutinhos".
· Emojis com MODERAÇÃO — em geral 1 por mensagem (😉 😊 🌞 💰 🚀 ✅ 👇 💙 🎉). Nunca encha de emoji.
· Formatação do WhatsApp: *negrito* nos termos e valores-chave; passos numerados (1. 2. 3.)
  quando for um processo. Nada de markdown de título (#) nem **asteriscos duplos**.
· Diante de um problema: explique de forma objetiva e gentil, SEM culpar o cliente
  ("a chave não foi reconhecida", "não está registrada"), e SEMPRE ofereça o próximo
  passo ou uma alternativa.
· Honesto sobre limites — se não dá pra fazer algo, diz na lata e oferece o que dá.
· Termine oferecendo ajuda ou confirmando o próximo passo ("Quer que eu...?",
  "Precisa de mais alguma coisa?").

Seu jeito de falar (exemplos REAIS):
— "A chave Pix que você enviou (09274899459) não foi reconhecida como válida. Pode conferir e me mandar de novo?"
— "Opa, não foi possível porque essa chave está registrada na sua própria conta. Quer tentar com outra?"
— "Oi, Hugo! Vim te contar sobre o *Radar de Boletos* 📃 ... Quer receber lembretes sobre seus boletos?"
— "Boa! Vou programar isso pra você. Mas antes preciso de mais alguns dados: ..."

REGRA DE OURO (confiança é o produto): nunca invente procedimento sobre dinheiro/conta.
Responda só ANCORADO no procedimento abaixo; se ele não cobrir, diga com honestidade que
vai acionar um humano com todo o contexto — sem fazer o cliente repetir nada."""


def _sys(seg, doc, opener, fatos="", perfil=""):
    s = JOTA_VOICE + f"\n\nCliente desta conversa: {SEG_NOME.get(seg, seg)}."
    if perfil:
        s += f" Perfil do cliente: {perfil}."
    s += ("\nADAPTE o tom ao cliente: espelhe a formalidade e o tamanho das mensagens dele. "
          "Se houver baixa familiaridade digital (ou cliente idoso), simplifique, vá mais "
          "devagar e evite jargão; se ele escreve curto e direto, responda curto e direto.")
    if fatos:
        s += (f"\n\n## O QUE VOCÊ JÁ SABE (eventos do sistema + base de dados)\n{fatos}\n"
              "Use estes fatos com CONFIANÇA. NUNCA pergunte ao cliente algo que você já sabe "
              "aqui — status da venda, plano de recebimento, valor, nº de tentativas, motivo da "
              "falha. Perguntar o que já temos passa insegurança. AFIRME com os dados e RESOLVA.")
    if opener:
        s += ("\n\nVocê é PROATIVO: o cliente está NO MEIO de uma tentativa (ex.: mandar um Pix) "
              "e o sistema detectou o atrito. Você JÁ acompanhou a conversa. Mude de marcha: numa "
              "frase, reconheça o que tá travando (com os dados reais) e ofereça O caminho que "
              "resolve — ex.: gerar um QR Code de cobrança. CURTO (1-2 frases + 1 oferta). NÃO "
              "repita o que o cliente já tentou em detalhe, NÃO faça lista de passos (vão no "
              "painel), NÃO confirme o que já está nos fatos, NÃO mencione atendente/suporte "
              "humano — você resolve. ANTECIPE as perguntas que sua mensagem vai gerar na cabeça "
              "do cliente (sobre o quê é isso? por que aconteceu? eu fui afetado? vai repetir?) e "
              "responda TODAS de forma reconhecível e tranquilizadora — uma proativa que deixa "
              "dúvida gera mais ansiedade que silêncio.")
    if doc:
        passos = "\n".join(f"- {p}" for p in doc.steps)
        s += f"\n\n## PROCEDIMENTO ANCORADO ({doc.id})\n{doc.content}\nPassos:\n{passos}"
    return s


def gerar(history, theme, seg, doc, opener=False, fatos="", perfil="") -> str:
    if LLM is not None and doc is not None:
        try:
            # proativo resolve sozinho → tira passos de escalação humana do procedimento
            doc = doc.model_copy(update={"steps": [s for s in doc.steps
                                                   if not any(w in s.lower() for w in _ESC)]})
            msgs = [{"role": "system", "content": _sys(seg, doc, opener, fatos, perfil)}]
            if opener:
                msgs.append({"role": "user", "content":
                             "(abra a conversa proativamente agora, resolvendo com o que você já sabe)"})
            else:
                msgs += history
            r = LLM.chat.completions.create(model=OPENAI_MODEL, max_tokens=400, messages=msgs)
            return (r.choices[0].message.content or "").strip()
        except Exception:
            pass
    # fallback determinístico (ancorado, sem LLM)
    ack = ACK_BY_THEME.get(theme, ACK_BY_THEME[SupportTheme.OTHER])
    if doc:
        passos = "\n".join(f"{i}. {p}" for i, p in enumerate(doc.steps, 1))
        return f"Oi! {ack}\n\n{doc.content}\n\n{passos}"
    return "Vou te conectar com um atendente que resolve isso com você, com todo o contexto. 🙏"


# ─── pipeline real sobre a conversa atual ────────────────────────────────────
def _msgs(history, base: datetime):
    out = []
    for i, h in enumerate(history):
        snd = "customer" if h["role"] == "user" else "bot"
        out.append((snd, h["content"], (base + timedelta(minutes=i)).isoformat()))
    return out


def analisar(sess):
    base = datetime.fromisoformat(sess["started_at"])
    # contexto do atrito (sinal) entra como turnos de cliente p/ a detecção
    ctx_turns = [{"role": "user", "content": sess["context_text"]}] if sess["context_text"] else []
    hist = ctx_turns + sess["history"]
    det = classify_conversation(_msgs(hist, base), sess["events"], sess["started_at"])
    txt = " ".join(h["content"] for h in hist if h["role"] == "user")
    docs = retriever.retrieve(f"{txt} {det.predicted_theme.value.replace('_', ' ')}", top_k=TOP_K)
    dec = decide(derivar_decisao(det.predicted_theme, det, docs, txt))
    return det, docs, dec


def _brain(det, docs, dec, seg, regra=None):
    """Os 4 estágios do entregável, prontos pra renderizar visíveis em cada conversa:
    (1) detecção objetiva  (2) evidência do RAG  (3) decisão/roteamento  (4) passo a passo."""
    doc = docs[0] if docs else None
    trecho = None
    if doc:
        trecho = doc.content if len(doc.content) <= 200 else doc.content[:200].rstrip() + "…"
    return {
        # (1) detecção objetiva — a REGRA que disparou + a leitura do texto, auditável
        "regra": regra,
        "tema": det.predicted_theme.value, "tema_conf": round(det.theme_confidence, 2),
        "natureza": det.predicted_nature.value, "nat_conf": round(det.nature_confidence, 2),
        "keywords": det.matched_keywords, "evento": det.correlated_event,
        "sinais": [n for n, v in [("pediu humano", det.asked_for_human),
                                   ("frustrado", det.frustrated), ("em loop", det.in_loop)] if v],
        # (2) evidência recuperada (RAG)
        "doc": doc.id if doc else None, "doc_titulo": doc.title if doc else None,
        "doc_score": round(doc.score, 3) if doc else None, "doc_trecho": trecho,
        "rag_mode": retriever.mode,
        # (3) decisão / roteamento
        "acao": dec.action.value, "acao_label": ACTION_LABEL.get(dec.action, dec.action.value),
        "prioridade": dec.priority, "motivo": dec.reason, "linha": LINE_LABEL[proactive_line(seg)],
        # (4) passo a passo de resolução (do doc ancorado)
        "passos": doc.steps if doc else [],
    }


# ─── API ─────────────────────────────────────────────────────────────────────
class StartIn(BaseModel):
    id: str


class MsgIn(BaseModel):
    id: str
    text: str


class InterceptIn(BaseModel):
    text: str
    segment: str = "pf"


@app.post("/api/intercept")
def intercept(inp: InterceptIn):
    """O HERÓI: cola texto → o motor REAL (mesmo run_turn do bot em prod) devolve o
    raio-x completo — detecção auditável, cascata de gates, resolubilidade, RAG e handoff."""
    sess = brain._new_session(seg=inp.segment, name="Cliente")
    r = brain.run_turn(sess, inp.text)
    det, docs, dec, di = r["det"], r["docs"], r["dec"], r["inp"]
    doc = docs[0] if docs else None
    resol = derive_resolubilidade(det, doc)
    gx = explain_gates(di)
    payload = {
        "deteccao": {
            "natureza": det.predicted_nature.value, "natureza_conf": round(det.nature_confidence, 2),
            "tema": det.predicted_theme.value, "tema_conf": round(det.theme_confidence, 2),
            "tema_fonte": det.theme_source, "evento": det.correlated_event,
            "sinais": [s.model_dump() for s in det.signals if s.disparou],
            "flags": {"frustrado": det.frustrated, "em_loop": det.in_loop, "confuso": det.confused,
                      "decepcionado": det.disappointed, "pediu_humano": det.asked_for_human,
                      "seguranca": det.safety_concern},
        },
        "gates": gx["gates"],
        "resolubilidade": {**resol.fatores.model_dump(), "valor": resol.valor, "gargalo": resol.gargalo},
        "rag": [{"id": d.id, "titulo": d.title, "score": d.score, "requires_human": d.requires_human} for d in docs],
        "decisao": {"acao": dec.action.value, "motivo": dec.reason, "prioridade": dec.priority,
                    "capacidade": gx["capacidade"], "pressao_humano": gx["pressao_humano"],
                    "resolubilidade": di.resolvability, "criticidade": di.criticality},
        "resposta": r["reply"], "kind": r["kind"], "guardrail": r["guardrail"],
        "kb_gap": r.get("kb_gap", False),
    }
    if dec.action == InterceptionAction.HUMAN_HANDOFF:
        pack = build_context_pack(det, dec, di.criticality, inp.segment, datetime.now().hour)
        payload["handoff"] = {"especialidade": pack.routing.specialty, "in_hours": pack.routing.in_hours,
                              "nota": pack.routing.note, "prioridade": round(pack.routing.priority, 2),
                              "evidencia": pack.evidence, "sinais": pack.signals, "motivo": pack.reason}
    return payload


@app.get("/api/policy")
def policy():
    """Os 'números mágicos' à mostra — mas como POLICY nomeada, separada do mecanismo:
    priors iniciais recalibráveis, lidos do próprio código (fonte única de verdade)."""
    t = DEFAULT_THRESHOLDS
    return {
        "thresholds": [
            {"nome": "conf_floor", "grandeza": "certeza da detecção", "op": "<", "valor": t.conf_floor,
             "efeito": "não intercepta — palpite fraco"},
            {"nome": "roi_crit_floor", "grandeza": "criticidade (1–5)", "op": "<", "valor": t.roi_crit_floor,
             "efeito": "trivial (junto de trust baixo) → não incomoda"},
            {"nome": "roi_trust_floor", "grandeza": "trust em jogo", "op": "<", "valor": t.roi_trust_floor,
             "efeito": "trivial (junto de crit baixa) → não incomoda"},
            {"nome": "handoff_ceiling", "grandeza": "pressão por humano", "op": "≥", "valor": t.handoff_ceiling,
             "efeito": "handoff quente"},
            {"nome": "resolve_floor", "grandeza": "capacidade da IA", "op": "≥", "valor": t.resolve_floor,
             "efeito": "a IA resolve sozinha"},
            {"nome": "assist_floor", "grandeza": "capacidade da IA", "op": "≥", "valor": t.assist_floor,
             "efeito": "a IA assiste — humano no loop"},
        ],
        "confianca_base": [
            {"natureza": n.value, "valor": v,
             "o_que": ("fato: um evento de sistema" if v >= 1.0 else "probabilístico — calibrável contra o real")}
            for n, v in DEFAULT_CONFIDENCE.items()
        ],
        "formulas": [
            {"nome": "certeza da detecção", "expr": "mín(certeza do tema, certeza da natureza)", "o_que": "quão seguro o modelo está do tema E da natureza — o elo mais fraco (0–1)"},
            {"nome": "resolubilidade", "expr": "kb_existe × executável × reversível", "o_que": "os 3 fatos que dizem se a IA resolve sozinha (0–1)"},
            {"nome": "capacidade da IA", "expr": "resolubilidade × certeza da detecção", "o_que": "só age bem se sabe resolver E tem certeza do atrito (0–1)"},
            {"nome": "pressão por humano", "expr": "trust em jogo × (1 − resolubilidade)", "o_que": "confiança em risco que a IA não prova → empurra pro humano (0–1)"},
            {"nome": "prioridade na fila", "expr": "0.6 · criticidade_norm + 0.4 · pressão", "o_que": "ordena a fila: criticidade domina, trust não-resolvido empurra (0–1)"},
        ],
        "acoes": [
            {"nome": "não intercepta", "o_que": "a IA não age — fica quieta e observa, esperando mais sinal (certeza fraca ou atrito trivial)"},
            {"nome": "resolve", "o_que": "a IA resolve sozinha, ancorada no procedimento da KB"},
            {"nome": "assiste", "o_que": "a IA sugere a resposta, mas um humano decide (humano no loop)"},
            {"nome": "handoff", "o_que": "passa pro humano com o pacote de contexto (fila da especialidade)"},
        ],
    }


@app.get("/play")
def play():
    return FileResponse(str(ROOT / "apps" / "demo_play.html"))


@app.get("/")
def index():
    return FileResponse(str(ROOT / "apps" / "demo_ui.html"))


@app.get("/api/scenarios")
def scenarios():
    out = []
    for sid, (nome, modo, emoji) in UI.items():
        c = SCEN[sid]
        first = next((m["text"] for m in c["mensagens"] if m["sender"] == "customer"), "")
        out.append({"id": sid, "nome": nome, "modo": modo, "emoji": emoji,
                    "segment": c.get("segment", "pf"), "contexto": c.get("descricao", ""),
                    "preview": first[:48]})
    return out


def _customer_text(c) -> str:
    return " ".join(m["text"] for m in c["mensagens"] if m["sender"] == "customer")


@app.post("/api/start")
def start(inp: StartIn):
    c = SCEN[inp.id]
    nome, modo, emoji = UI[inp.id]
    seg = c.get("segment", "pf")
    sess = {
        "modo": modo, "segment": seg, "events": [(e["event_type"], e["occurred_at"])
                                                  for e in c.get("eventos", [])],
        "started_at": c["started_at"], "history": [], "context_text": _customer_text(c),
    }
    SESSIONS[inp.id] = sess
    det, docs, dec = analisar(sess)
    doc = docs[0] if docs else None
    msgs = []

    if modo == "proativo":
        f = FACTS.get(inp.id, {})
        sess["fatos"], sess["perfil"] = f.get("fatos", ""), f.get("perfil", "")
        msgs.append({"side": "center", "kind": "system",
                     "text": "🔔 Atrito detectado pelo sistema — agindo com os dados, antes do chamado existir."})
        opener = gerar([], det.predicted_theme, seg, doc, opener=True,
                       fatos=sess["fatos"], perfil=sess["perfil"])
        sess["history"].append({"role": "assistant", "content": opener})
        msgs.append({"side": "in", "kind": "jota", "author": "Jota", "text": opener})
    else:  # reativo — cliente escreveu no atendimento; IA sugere ao atendente
        msgs.append({"side": "center", "kind": "system",
                     "text": "📥 Conversa recebida no atendimento — a IA sugere ao atendente (você decide)."})
        for m in c["mensagens"]:
            if m["sender"] == "customer":
                msgs.append({"side": "in", "kind": "customer", "author": "Cliente", "text": m["text"]})
        msgs.append(_suggestion_msg(det.predicted_theme, docs, dec))

    return {"modo": modo, "brain": _brain(det, docs, dec, seg, _regra_for(inp.id)), "messages": msgs}


@app.post("/api/replay")
def replay(inp: StartIn):
    """Proativo em modo REPLAY: devolve a timeline (atrito nascendo) + a detecção/
    decisão reais no ponto do trip + a interceptação gerada. O front anima os beats."""
    c = SCEN[inp.id]
    seg = c.get("segment", "pf")
    f = FACTS.get(inp.id, {})
    sess = {
        "modo": "proativo", "segment": seg,
        "events": [(e["event_type"], e["occurred_at"]) for e in c.get("eventos", [])],
        "started_at": c["started_at"], "history": [], "context_text": _customer_text(c),
        "fatos": f.get("fatos", ""), "perfil": f.get("perfil", ""),
    }
    SESSIONS[inp.id] = sess
    det, docs, dec = analisar(sess)
    doc = docs[0] if docs else None
    interception = gerar([], det.predicted_theme, seg, doc, opener=True,
                         fatos=sess["fatos"], perfil=sess["perfil"])
    sess["history"].append({"role": "assistant", "content": interception})
    return {"beats": TIMELINE.get(inp.id, []),
            "brain": _brain(det, docs, dec, seg, _regra_for(inp.id)),
            "interception": interception}


def _suggestion_msg(theme, docs, dec):
    if dec.action == InterceptionAction.HUMAN_HANDOFF:
        return {"side": "center", "kind": "handoff",
                "text": "Confiança em jogo que a IA não resolve com prova → **handoff quente** "
                        "pra um humano, com o pacote de contexto. Nunca um bounce frio."}
    sug = montar_sugestao(theme, docs)
    if not sug:
        return {"side": "center", "kind": "handoff",
                "text": "Sem procedimento ancorado na base → encaminhar a um humano."}
    return {"side": "center", "kind": "suggestion", "author": "💡 Sugestão pro atendente",
            "text": sug["resposta"], "steps": sug["passos"], "fontes": sug["fontes"]}


@app.post("/api/message")
def message(inp: MsgIn):
    sess = SESSIONS.get(inp.id)
    if sess is None:
        return {"error": "inicie a conversa primeiro"}
    sess["history"].append({"role": "user", "content": inp.text})
    det, docs, dec = analisar(sess)
    doc = docs[0] if docs else None
    seg = sess["segment"]
    msgs = [{"side": "out", "kind": "customer", "author": "Cliente", "text": inp.text}]

    if sess["modo"] == "proativo":
        reply = gerar(sess["history"], det.predicted_theme, seg, doc,
                      fatos=sess.get("fatos", ""), perfil=sess.get("perfil", ""))
        sess["history"].append({"role": "assistant", "content": reply})
        msgs.append({"side": "in", "kind": "jota", "author": "Jota", "text": reply})
    else:
        msgs.append(_suggestion_msg(det.predicted_theme, docs, dec))

    return {"modo": sess["modo"], "brain": _brain(det, docs, dec, seg, _regra_for(inp.id)), "messages": msgs}


# ═══════════════════════════════════════════════════════════════════════════
# LABORATÓRIO — explorador de dados (estilo MLflow): a base de 10k + as QUERIES
# à mostra (transparência do método) + a tese "um só modelo: atendimento→produto".
# ═══════════════════════════════════════════════════════════════════════════
DB = ROOT / "data" / "jota_support.db"


def _db_ro() -> sqlite3.Connection:
    return sqlite3.connect(f"file:{DB}?mode=ro", uri=True)


# Queries prontas (reais) que contam a história — read-only, exibidas no editor.
LAB_PRESETS = [
    {"nome": "Padrões de erro — volumetria por tema",
     "sql": "SELECT gold_theme AS tema, COUNT(*) AS n\nFROM conversations\nGROUP BY gold_theme\nORDER BY n DESC;"},
    {"nome": "Os dois mundos (suporte × produto)",
     "sql": "SELECT channel AS mundo, COUNT(*) AS conversas\nFROM conversations\nGROUP BY channel;"},
    {"nome": "Resolução por mundo",
     "sql": "SELECT channel AS mundo,\n  ROUND(100.0*SUM(CASE WHEN outcome='resolved' THEN 1 ELSE 0 END)/COUNT(*),1) AS pct_resolvido\nFROM conversations\nGROUP BY channel;"},
    {"nome": "Erros INVISÍVEIS (ausência) — só o produto vê",
     "sql": "SELECT gold_theme AS tema, COUNT(*) AS ausencias\nFROM conversations\nWHERE gold_nature='absence_detected'\nGROUP BY gold_theme\nORDER BY ausencias DESC;"},
    {"nome": "Natureza do atrito por mundo",
     "sql": "SELECT channel AS mundo, gold_nature AS natureza, COUNT(*) AS n\nFROM conversations\nGROUP BY channel, gold_nature\nORDER BY mundo, n DESC;"},
    {"nome": "SLA de 1ª resposta no suporte (window LAG)",
     "sql": "WITH g AS (\n  SELECT m.conversation_id, m.sender, m.sent_at,\n    LAG(m.sent_at) OVER (PARTITION BY m.conversation_id ORDER BY m.turn_index) prev,\n    LAG(m.sender)  OVER (PARTITION BY m.conversation_id ORDER BY m.turn_index) ps\n  FROM messages m JOIN conversations c ON c.conversation_id=m.conversation_id\n  WHERE c.channel='support')\nSELECT ROUND(AVG((strftime('%s',sent_at)-strftime('%s',prev))/3600.0),1) AS sla_medio_h,\n       ROUND(MAX((strftime('%s',sent_at)-strftime('%s',prev))/3600.0),1) AS sla_pior_h\nFROM g WHERE ps='customer' AND sender='human_agent';"},
    {"nome": "Pedidos de humano IGNORADOS (atrito de confiança)",
     "sql": "SELECT SUM(asked_for_human) AS pediram_humano,\n  SUM(human_handoff_done) AS atendidos,\n  SUM(asked_for_human)-SUM(human_handoff_done) AS ignorados\nFROM conversations WHERE channel='support';"},
    {"nome": "Criticidade média por tema",
     "sql": "SELECT gold_theme AS tema, ROUND(AVG(gold_criticality),2) AS crit_media, COUNT(*) AS n\nFROM conversations\nGROUP BY gold_theme\nORDER BY crit_media DESC;"},
]


@app.get("/lab")
def lab():
    return FileResponse(str(ROOT / "apps" / "demo_lab.html"))


@app.get("/api/lab/overview")
def lab_overview():
    con = _db_ro()
    g = lambda sql: con.execute(sql).fetchall()
    total = g("SELECT COUNT(*) FROM conversations")[0][0]
    por_mundo = dict(g("SELECT channel, COUNT(*) FROM conversations GROUP BY channel"))
    users = g("SELECT COUNT(*) FROM users")[0][0]
    msgs = g("SELECT COUNT(*) FROM messages")[0][0]
    eventos = g("SELECT COUNT(*) FROM events")[0][0]
    temas = g("SELECT gold_theme, COUNT(*) n FROM conversations GROUP BY gold_theme ORDER BY n DESC")
    resol = dict(g("SELECT channel, ROUND(100.0*SUM(CASE WHEN outcome='resolved' THEN 1 ELSE 0 END)/COUNT(*),1) FROM conversations GROUP BY channel"))
    ausencias = g("SELECT COUNT(*) FROM conversations WHERE gold_nature='absence_detected'")[0][0]
    con.close()
    return {"total": total, "suporte": por_mundo.get("support", 0), "produto": por_mundo.get("jota", 0),
            "users": users, "mensagens": msgs, "eventos": eventos, "temas": temas,
            "resol_suporte": resol.get("support"), "resol_produto": resol.get("jota"),
            "ausencias": ausencias}


@app.get("/api/lab/presets")
def lab_presets():
    return LAB_PRESETS


# Funil do atendimento: o motor real rodado nos 5k (precompute scripts/lab_atendimento.py).
_ATEND = ROOT / "data" / "lab_atendimento.json"


@app.get("/api/lab/atendimento")
def lab_atendimento():
    if not _ATEND.exists():
        return {"error": "rode scripts/lab_atendimento.py primeiro"}
    return json.loads(_ATEND.read_text("utf-8"))


@app.get("/api/evals")
def evals():
    """Scorecard real (data/eval_scorecard.json, rodado no CI) + soak + as 4 camadas.
    A honestidade do dataset é o herói: números contra um seed set curado, não produção."""
    import re
    sc = json.loads((ROOT / "data" / "eval_scorecard.json").read_text("utf-8")) if (ROOT / "data" / "eval_scorecard.json").exists() else {"metrics": {}}
    m = sc.get("metrics", {})
    rows = [
        {"metrica": "detecção · natureza", "valor": m.get("deteccao_natureza"), "limiar": 0.95, "fmt": "pct", "o_que": f"acurácia vs gabarito · amostra {sc.get('amostra_deteccao','')}"},
        {"metrica": "detecção · tema", "valor": m.get("deteccao_tema"), "limiar": 0.80, "fmt": "pct", "o_que": "semântico"},
        {"metrica": "retrieval · Hit@3", "valor": m.get("rag_hit_at_k"), "limiar": 0.80, "fmt": "pct", "o_que": f"{m.get('rag_modo','')} · {m.get('rag_queries','')} queries"},
        {"metrica": "retrieval · MRR", "valor": m.get("rag_mrr"), "limiar": 0.70, "fmt": "pct", "o_que": "ranking do doc certo"},
        {"metrica": "decisão · acurácia", "valor": m.get("decisao_acuracia"), "limiar": 0.95, "fmt": "pct", "o_que": f"{m.get('decisao_cenarios','')} cenários"},
        {"metrica": "decisão · divergências", "valor": m.get("decisao_divergencias"), "limiar": 0, "fmt": "int", "invert": True, "o_que": "vs julgamento de produto"},
    ]
    for row in rows:
        v = row["valor"]
        row["ok"] = (v is not None) and (v <= row["limiar"] if row.get("invert") else v >= row["limiar"])
    soak = None
    sp = ROOT / "data" / "soak_report.md"
    if sp.exists():
        txt = sp.read_text("utf-8")
        grab = lambda pat: (re.search(pat, txt).group(1) if re.search(pat, txt) else None)
        soak = {"conversas": grab(r"conversas:\s*\*\*([\d.]+)\*\*"), "desfecho": grab(r"desfecho certo:\s*\*\*([\d.]+)%"),
                "grounding": grab(r"grounding limpo:\s*\*\*([\d.]+)%"), "tom": grab(r"tom médio:\s*\*\*([\d.,]+)")}
    camadas = [
        {"nome": "unit (run_all)", "o_que": "contratos do motor com limiares de regressão — vermelho se cair"},
        {"nome": "scripted · 15 cenários", "o_que": "conversas multi-turno determinísticas ponta a ponta"},
        {"nome": "cliente-LLM + juiz", "o_que": "um LLM finge de cliente; outro julga grounding / adequação / tom"},
        {"nome": "proativo · /demo-*", "o_que": "os 7 cenários proativos pelo fluxo real"},
    ]
    return {"scorecard": rows, "soak": soak, "camadas": camadas}


@app.get("/api/lab/gaps")
def lab_gaps():
    """O backlog de lacunas de KB (data/kb_gaps.jsonl) — perguntas sem procedimento relevante
    que o motor escalou e registrou. É o Mundo 1 alimentando o Mundo 2, com evidência."""
    p = ROOT / "data" / "kb_gaps.jsonl"
    rows = []
    if p.exists():
        for line in p.read_text("utf-8").splitlines()[-60:]:
            line = line.strip()
            if line:
                try:
                    rows.append(json.loads(line))
                except Exception:
                    pass
    rows.reverse()   # mais recente primeiro
    return {"gaps": rows}


class SQLIn(BaseModel):
    sql: str


@app.post("/api/lab/query")
def lab_query(inp: SQLIn):
    q = (inp.sql or "").strip().rstrip(";").strip()
    low = q.lower()
    if not (low.startswith("select") or low.startswith("with")):
        return {"error": "Apenas consultas SELECT/WITH são permitidas (read-only)."}
    if ";" in q:
        return {"error": "Rode uma instrução por vez."}
    try:
        con = _db_ro()
        cur = con.execute(q)
        cols = [d[0] for d in cur.description] if cur.description else []
        rows = cur.fetchmany(1000)
        con.close()
        return {"columns": cols, "rows": rows, "n": len(rows)}
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}
