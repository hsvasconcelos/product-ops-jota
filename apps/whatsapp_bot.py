"""Demo · Jota — o Jota proativo, ao vivo no WhatsApp.
=============================================================================
Webhook que recebe mensagens do Evolution API (gateway não-oficial de WhatsApp)
e responde como o agente PROATIVO do Mundo 2: detecta o atrito (classifier),
ancora num procedimento (RAG), decide a ação (decision) e gera a resposta com
um LLM (OpenAI) — grounded no doc recuperado, com FALLBACK pro template se a API cair.

ENQUADRAMENTO (dizer na sala): o pipeline é REAL, rodando agora. A única coisa
"simulada" é o transporte — Evolution em vez da API oficial da Meta — porque é
um case, não produção com cliente. Mostra que dá pra colocar em prod.

Como funciona a demo:
  · O avaliador manda um slash command (ex.: /pix) → injeta o CONTEXTO de uma
    conversa-ruim real (mensagens + evento de sistema) e o Jota responde PROATIVO.
  · Depois ele digita à vontade → o agente CONTINUA a conversa, ancorado.
  · /ajuda lista os comandos.

Degradação graciosa (mesmo padrão do rag.py): sem OPENAI_API_KEY ou se a
chamada falhar, cai no template determinístico — a demo nunca trava por causa do LLM.

ponytail: estado de conversa por número é in-memory (dict). Suficiente p/ uma
demo de processo único; se virar prod, troca por Redis (o Evolution já sobe um).
"""
from __future__ import annotations

import json
import logging
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

import httpx
from fastapi import FastAPI, Request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "apps"))

from product_ops_jota.channels import proactive_line                 # noqa: E402
from product_ops_jota.classifier import classify_conversation        # noqa: E402
from product_ops_jota.decision import decide                         # noqa: E402
from product_ops_jota.friction_model import InterceptionAction       # noqa: E402
from product_ops_jota.rag import Retriever                           # noqa: E402
# reuso das heurísticas do copiloto (policy, não mecanismo) — DRY
from copiloto import ACK_BY_THEME, derivar_decisao, montar_sugestao  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(message)s")
log = logging.getLogger("jota.whatsapp")

# ─── POLICY (config no topo) ─────────────────────────────────────────────────
EVOLUTION_URL = os.environ.get("EVOLUTION_URL", "http://evolution:8080")
EVOLUTION_API_KEY = os.environ.get("EVOLUTION_API_KEY", "")
EVOLUTION_INSTANCE = os.environ.get("EVOLUTION_INSTANCE", "jota")
OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")   # gpt-4o p/ prosa mais rica
TYPING_MS = 1500               # quanto o "digitando…" aparece antes da resposta
TOP_K = 2

DATA = json.loads((ROOT / "data" / "conversas_ruins.json").read_text("utf-8"))
SCENARIOS = {c["id"]: c for c in DATA}

# slash command → id do cenário-real no conversas_ruins.json
COMMANDS = {
    "/pix": "ex_pix_loop",
    "/kyc": "ex_kyc_falhou",
    "/falatap": "ex_fala_tap_inseguranca",
    "/conta": "c_real_001",
    "/boleto": "ex_boleto_duplicado",
    "/openfinance": "ex_open_finance",
    "/pixerrado": "ex_pix_pessoa_errada",
    "/kyclimbo": "ex_kyc_limbo",
    "/excluir": "ex_excluir_conta",
}
SEG_NOME = {"pf": "pessoa física", "pj": "pessoa jurídica", "mei": "MEI"}

retriever = Retriever()
app = FastAPI()
# estado por número de WhatsApp (jid → sessão). ponytail: in-memory, single-process.
SESSIONS: dict[str, dict] = {}


# ─── LLM grounded (OpenAI, com fallback template) ────────────────────────────
def _llm_client():
    if not os.environ.get("OPENAI_API_KEY"):
        return None
    try:
        from openai import OpenAI
        return OpenAI()
    except Exception as e:  # lib ausente, etc.
        log.warning("OpenAI indisponível (%s) — usando template", type(e).__name__)
        return None


LLM = _llm_client()


def _system_prompt(theme, segment, doc) -> str:
    base = (
        "Você é o Jota, assistente financeiro no WhatsApp. Fala português do Brasil, "
        "tom caloroso e direto, mensagens curtas (é WhatsApp). Você é PROATIVO: já "
        f"percebeu um possível atrito do cliente ({SEG_NOME.get(segment, segment)}) e "
        "abre a conversa reconhecendo isso, confirmando com uma pergunta — se errar, o "
        "cliente corrige.\n\n"
        "REGRA DE OURO (confiança é o produto): você NUNCA inventa procedimento sobre "
        "dinheiro/conta. Responda ANCORADO somente no procedimento abaixo. Se ele não "
        "cobrir o que o cliente precisa, diga com honestidade que vai acionar um humano "
        "com o contexto — não chute."
    )
    if doc:
        passos = "\n".join(f"- {p}" for p in doc.steps)
        base += (
            f"\n\n## PROCEDIMENTO ANCORADO ({doc.id})\n{doc.content}\n"
            f"Passos:\n{passos}"
        )
    else:
        base += "\n\n(Nenhum procedimento relevante recuperado — acione um humano.)"
    return base


def gerar_resposta(history, theme, segment, doc) -> str:
    """Resposta do Jota: LLM grounded (OpenAI); fallback pro template do copiloto."""
    if LLM is not None and doc is not None:
        try:
            resp = LLM.chat.completions.create(
                model=OPENAI_MODEL,
                max_tokens=400,
                messages=[{"role": "system", "content": _system_prompt(theme, segment, doc)},
                          *history],
            )
            return (resp.choices[0].message.content or "").strip()
        except Exception as e:                       # rede/erro → degrada
            log.warning("OpenAI falhou (%s) — fallback template", type(e).__name__)
    # fallback determinístico (mesma ancoragem, sem LLM)
    sug = montar_sugestao(theme, [doc] if doc else [])
    if not sug:
        return "Vou te conectar com um atendente que resolve isso com você. 🙏"
    passos = "\n".join(f"{i}. {p}" for i, p in enumerate(sug["passos"], 1))
    return f"{sug['resposta']}\n\n{passos}"


# ─── Detecção + decisão a partir do histórico acumulado ──────────────────────
def _msgs_from_history(history, base_ts: datetime):
    """history (role/content) → tuplas (sender, text, sent_at) p/ o classifier."""
    out = []
    for i, h in enumerate(history):
        sender = "customer" if h["role"] == "user" else "bot"
        ts = (base_ts + timedelta(minutes=i)).isoformat()
        out.append((sender, h["content"], ts))
    return out


def analisar(session) -> tuple:
    """Roda o pipeline real sobre a conversa atual: detecção, RAG, decisão."""
    history, events = session["history"], session["events"]
    base_ts = datetime.fromisoformat(session["started_at"])
    msgs = _msgs_from_history(history, base_ts)
    detection = classify_conversation(msgs, events, session["started_at"])
    theme = detection.predicted_theme
    customer_text = " ".join(h["content"] for h in history if h["role"] == "user")
    query = f"{customer_text} {theme.value.replace('_', ' ')}"
    docs = retriever.retrieve(query, top_k=TOP_K)
    decisao = decide(derivar_decisao(theme, detection, docs, customer_text))
    return detection, docs, decisao


# ─── Orquestração de uma mensagem recebida ───────────────────────────────────
def _nova_sessao(jid) -> dict:
    return {"history": [], "events": [], "started_at": datetime.now().isoformat()}


def _ajuda() -> str:
    linhas = "\n".join(f"  {cmd}" for cmd in COMMANDS)
    return ("👋 Sou o *Jota* proativo (demo). Mande um comando pra eu pegar o "
            "contexto de uma conversa real e agir — ou escreva à vontade:\n\n"
            f"{linhas}\n\n  /reset — recomeçar a conversa")


def processar(jid: str, texto: str) -> str:
    texto = texto.strip()
    cmd = texto.lower().split()[0] if texto else ""

    if cmd in ("/ajuda", "/start", "/help"):
        return _ajuda()

    if cmd in ("/reset", "/limpar"):
        SESSIONS.pop(jid, None)
        return "🧹 Pronto, recomeçamos do zero. Manda /ajuda pra ver os comandos."

    if cmd in COMMANDS:
        # SEED: injeta o contexto da conversa-ruim real (só as falas do cliente
        # + eventos) e parte pra resposta proativa.
        cenario = SCENARIOS[COMMANDS[cmd]]
        sess = _nova_sessao(jid)
        sess["started_at"] = cenario["started_at"]
        sess["events"] = [(e["event_type"], e["occurred_at"])
                          for e in cenario.get("eventos", [])]
        sess["segment"] = cenario.get("segment", "pf")
        sess["history"] = [{"role": "user", "content": m["text"]}
                           for m in cenario["mensagens"] if m["sender"] == "customer"]
        SESSIONS[jid] = sess
    else:
        # follow-up livre (ou primeira mensagem espontânea)
        sess = SESSIONS.setdefault(jid, _nova_sessao(jid))
        sess.setdefault("segment", "pf")
        sess["history"].append({"role": "user", "content": texto})

    detection, docs, decisao = analisar(sess)
    doc = docs[0] if docs else None
    linha = proactive_line(sess["segment"])

    # log pro apresentador narrar (aparece no terminal/servidor, não no WhatsApp)
    log.info("[%s] tema=%s nat=%s | ação=%s prio=%.2f | linha=%s | doc=%s",
             jid.split("@")[0], detection.predicted_theme.value,
             detection.predicted_nature.value, decisao.action.value,
             decisao.priority, linha.value, doc.id if doc else "—")

    # P4: confiança em jogo que a IA não prova → humano quente (com contexto).
    if decisao.action == InterceptionAction.HUMAN_HANDOFF:
        ack = ACK_BY_THEME.get(detection.predicted_theme, "")
        reply = (f"Oi! {ack}\n\nIsso aqui eu prefiro resolver com um humano do nosso "
                 "time, que já vai te chamar com todo o contexto — sem te fazer repetir "
                 "nada. Já estou passando. 🤝")
        sess["history"].append({"role": "assistant", "content": reply})
        return reply

    reply = gerar_resposta(sess["history"], detection.predicted_theme,
                           sess["segment"], doc)
    sess["history"].append({"role": "assistant", "content": reply})
    return reply


# ─── Envio via Evolution (com "digitando…") ──────────────────────────────────
async def enviar(numero: str, texto: str) -> None:
    url = f"{EVOLUTION_URL}/message/sendText/{EVOLUTION_INSTANCE}"
    headers = {"apikey": EVOLUTION_API_KEY, "Content-Type": "application/json"}
    payload = {"number": numero, "text": texto, "delay": TYPING_MS}
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.post(url, json=payload, headers=headers)
        if r.status_code >= 300:
            log.error("envio falhou %s: %s", r.status_code, r.text[:200])


# ─── Webhook do Evolution ────────────────────────────────────────────────────
@app.post("/webhook")
@app.post("/webhook/messages-upsert")
async def webhook(req: Request):
    body = await req.json()
    data = body.get("data") or {}
    key = data.get("key") or {}
    jid = key.get("remoteJid", "")

    # ignora: eco das próprias mensagens, grupos, status, e não-texto
    if key.get("fromMe") or jid.endswith("@g.us") or "broadcast" in jid:
        return {"ok": True}
    msg = data.get("message") or {}
    texto = msg.get("conversation") or (msg.get("extendedTextMessage") or {}).get("text")
    if not jid or not texto:
        return {"ok": True}

    numero = jid.split("@")[0]
    try:
        reply = processar(jid, texto)
    except Exception as e:                            # nunca derruba a demo
        log.exception("erro processando")
        reply = "Ops, tive um problema aqui — já estou chamando um humano pra te ajudar. 🙏"
    await enviar(numero, reply)
    return {"ok": True}


@app.get("/")
def health():
    return {"status": "ok", "rag": retriever.mode,
            "llm": "on" if LLM else "template",
            "comandos": list(COMMANDS)}
