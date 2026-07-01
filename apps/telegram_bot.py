"""Demo · Jota no Telegram — o motor REAL, ao vivo e conversacional.
=============================================================================
Long polling (roda no notebook, SEM VPS/ngrok — o bot puxa as mensagens). Reusa
o CÉREBRO de `src/product_ops_jota/` (classifier → decision → RAG → handoff): toda
melhoria no modelo reflete aqui automaticamente. Geração com OpenAI grounded no
doc recuperado, com fallback determinístico (a demo nunca trava por causa do LLM).

ENQUADRAMENTO (dizer na sala): o pipeline é REAL, rodando agora. Transporte é
Telegram (oficial, estável) em vez do WhatsApp só porque é um case — prova que
coloco em produção. O fundador pega o celular e testa ao vivo.

Setup:
  1. No Telegram, fale com @BotFather → /newbot → copie o token
  2. No .env:  TELEGRAM_BOT_TOKEN=...   (e OPENAI_API_KEY=... pra prosa rica)
  3. .venv/bin/python apps/telegram_bot.py
  4. abra o bot no Telegram e mande /start

Comandos: /start · /debug (mostra o cérebro) · /pix /kyc /falatap /pixerrado /excluir (cenários proativos)
"""
from __future__ import annotations

import json
import logging
import os
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "apps"))

# .env sem dependência extra
_envf = ROOT / ".env"
if _envf.exists():
    for _l in _envf.read_text().splitlines():
        _l = _l.strip()
        if _l and not _l.startswith("#") and "=" in _l:
            _k, _v = _l.split("=", 1)
            os.environ.setdefault(_k, _v)

from product_ops_jota.classifier import classify_conversation              # noqa: E402
from product_ops_jota.decision import (                                    # noqa: E402
    decide, derive_decision_input, derive_resolubilidade, UserProfile,
)
from product_ops_jota.friction_model import InterceptionAction, SupportTheme as _T  # noqa: E402
from product_ops_jota.handoff import build_context_pack                    # noqa: E402
from product_ops_jota.rag import Retriever                                 # noqa: E402
from copiloto import montar_sugestao                                       # noqa: E402  (fallback)

logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(message)s")
log = logging.getLogger("jota.telegram")

TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
API = f"https://api.telegram.org/bot{TOKEN}"
OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-4o")
TOP_K = 2

DATA = json.loads((ROOT / "data" / "conversas_ruins.json").read_text("utf-8"))
SCENARIOS = {c["id"]: c for c in DATA}
COMMANDS = {
    "/pix": "ex_pix_loop", "/kyc": "ex_kyc_falhou", "/falatap": "ex_fala_tap_inseguranca",
    "/pixerrado": "ex_pix_pessoa_errada", "/excluir": "ex_excluir_conta",
    "/boleto": "ex_boleto_duplicado", "/conta": "c_real_001",
}
SEG_NOME = {"pf": "pessoa física", "pj": "pessoa jurídica", "mei": "MEI"}
ACTION_LABEL = {
    InterceptionAction.AI_RESOLVE: "IA resolve in-thread",
    InterceptionAction.AI_RESOLVE_SILENT: "IA resolve em background",
    InterceptionAction.AI_ASSIST: "IA assiste (humano no loop)",
    InterceptionAction.HUMAN_HANDOFF: "Humano (handoff quente)",
    InterceptionAction.NO_INTERCEPT: "Não interceptar (observar)",
}

retriever = Retriever()
SESSIONS: dict[int, dict] = {}    # chat_id → sessão (in-memory; ponytail: ok p/ demo)
DEBUG: set[int] = set()           # chat_ids com /debug ligado


# ─── LLM grounded (OpenAI) + fallback determinístico ─────────────────────────
def _llm():
    if not os.environ.get("OPENAI_API_KEY"):
        return None
    try:
        from openai import OpenAI
        return OpenAI()
    except Exception as e:
        log.warning("OpenAI indisponível (%s) — usando template", type(e).__name__)
        return None


LLM = _llm()

JOTA_VOICE = (
    "Você é a *Aline*, do time de suporte do Jota (assistente financeiro que vive no WhatsApp). "
    "PT-BR, tom caloroso, animado e DIRETO, mensagens curtas (é chat). Use *negrito* nos "
    "valores/termos-chave e, quando for um processo, passos numerados (1. 2. 3.). No máximo 1 emoji.\n"
    "REGRA DE OURO (confiança é o produto): NUNCA invente procedimento sobre dinheiro/conta. "
    "Responda ANCORADO só no procedimento abaixo. Sem culpar o cliente; sempre ofereça o próximo "
    "passo ou uma alternativa. Se faltar um dado do cliente, PERGUNTE. "
    "IMPORTANTE: você NÃO decide acionar humano e NUNCA cita 'atendente', 'humano', 'suporte', "
    "'time', nem diz que vai 'encaminhar', 'analisar', 'passar pra frente'. PROIBIDO também o hedge "
    "de limbo: 'precisa de análise mais detalhada', 'é uma questão específica', 'precisa de um olhar "
    "mais aprofundado', 'talvez seja preciso verificar algo mais específico' — isso é uma decisão de "
    "escalonamento, que é do SISTEMA, não sua, e sem quem/quando deixa o cliente no vácuo. "
    "Se você NÃO tem um passo concreto novo, NÃO invente 'análise': faça UMA pergunta objetiva pra "
    "entender melhor. NUNCA repita a mesma dica que já deu. Seu papel é ajudar com o que está ancorado.\n"
    "O JOTA É A CONTA/BANCO DO CLIENTE (conta de pagamento no WhatsApp). NUNCA mande o cliente "
    "'procurar o banco dele' como se o Jota fosse outra coisa — aqui, o banco dele é o Jota. Para um "
    "Pix enviado pela conta Jota, oriente pelo caminho do PRÓPRIO Jota, sem terceirizar pro 'seu banco'.\n"
    "FALE COMO PRA UM LEIGO (#6): zero jargão técnico — nada de 'logs', 'cache', 'opções de "
    "desenvolvedor'. Se precisar citar algo assim, explique em 1 frase simples.\n"
    "SE A MENSAGEM FOR AMBÍGUA (ex.: 'não consigo acessar o Jota' pode ser no app OU aqui no "
    "WhatsApp), faça UMA pergunta curta pra entender ANTES de despejar um passo a passo. O Jota "
    "vive no WhatsApp; não assuma que é o app."
)


def _system_prompt(theme, seg, doc, disappointed=False, opener=False) -> str:
    s = JOTA_VOICE + f"\n\nCliente: {SEG_NOME.get(seg, seg)}."
    if disappointed:
        s += ("\nO cliente está DESANIMADO/decepcionado (frustração fria). Acolha primeiro, "
              "de forma genuína e breve, antes de resolver — sem dramatizar.")
    if opener:
        s += ("\nVocê é PROATIVO: percebeu o atrito e ABRE a conversa reconhecendo, com os dados "
              "que já tem. Curto (1-2 frases + 1 oferta). Antecipe as dúvidas que sua mensagem gera.")
    if doc:
        passos = "\n".join(f"- {p}" for p in doc.steps)
        s += f"\n\n## PROCEDIMENTO ANCORADO ({doc.id})\n{doc.content}\nPassos:\n{passos}"
    else:
        s += "\n\n(Nenhum procedimento recuperado — seja honesto e acione um humano.)"
    return s


_ESC = ("atendente", "suporte", "humano", "manualmente", "técnico", "logs",
        "encaminh", "analise", "análise", "verificar com")


_THEME_KEYS = [t.value for t in _T]


def theme_judge(text):
    """LLM-as-judge: classifica o tema quando a regra/semântica ficou ambígua. Saída restrita
    às chaves de tema; None se indisponível/erro (degrada pro que a regra já achou)."""
    if LLM is None or not text.strip():
        return None
    try:
        r = LLM.chat.completions.create(
            model=OPENAI_MODEL, max_tokens=12, temperature=0,
            messages=[{"role": "system", "content":
                       "Você classifica a mensagem de um cliente do Jota em UM tema de suporte. "
                       "Responda SÓ a chave, exatamente uma de: " + ", ".join(_THEME_KEYS) + ". "
                       "Nada além da chave."},
                      {"role": "user", "content": text[:500]}])
        val = (r.choices[0].message.content or "").strip().lower()
        for t in _T:
            if t.value in val:
                return t
    except Exception as e:
        log.warning("theme_judge falhou (%s)", type(e).__name__)
    return None


def gerar(history, theme, seg, doc, disappointed=False, opener=False) -> str:
    # quando a IA resolve, tira passos de escalação/jargão do procedimento (a decisão
    # de chamar humano é do gate, não do texto — e jargão assusta o leigo).
    if doc is not None:
        doc = doc.model_copy(update={"steps": [s for s in doc.steps
                                               if not any(w in s.lower() for w in _ESC)]})
    if LLM is not None and doc is not None:
        try:
            msgs = [{"role": "system", "content": _system_prompt(theme, seg, doc, disappointed, opener)}]
            if opener:
                msgs.append({"role": "user", "content": "(abra a conversa proativamente agora)"})
            else:
                msgs += history
            r = LLM.chat.completions.create(model=OPENAI_MODEL, max_tokens=400, messages=msgs)
            return (r.choices[0].message.content or "").strip()
        except Exception as e:
            log.warning("OpenAI falhou (%s) — fallback", type(e).__name__)
    sug = montar_sugestao(theme, [doc] if doc else [])
    if not sug:
        return "Vou te conectar com uma pessoa do time que resolve isso com você, com todo o contexto. 🙏"
    passos = "\n".join(f"{i}. {p}" for i, p in enumerate(sug["passos"], 1))
    return f"{sug['resposta']}\n\n{passos}"


# ─── pipeline real (cérebro de src/) ─────────────────────────────────────────
def _msgs(history, base: datetime):
    out = []
    for i, h in enumerate(history):
        snd = "customer" if h["role"] == "user" else "bot"
        out.append((snd, h["content"], (base + timedelta(minutes=i)).isoformat()))
    return out


def analisar(sess):
    base = datetime.fromisoformat(sess["started_at"])
    ctx = [{"role": "user", "content": sess["context_text"]}] if sess.get("context_text") else []
    hist = ctx + sess["history"]
    det = classify_conversation(_msgs(hist, base), sess["events"], sess["started_at"],
                                retriever=retriever, judge=theme_judge)
    txt = " ".join(h["content"] for h in hist if h["role"] == "user")
    docs = retriever.retrieve(f"{txt} {det.predicted_theme.value.replace('_',' ')}", top_k=TOP_K)
    doc = docs[0] if docs else None
    # ESGOTAMENTO: o sinal PRIMÁRIO é o do CLIENTE (determinístico, independe do reply do LLM):
    # quantas vezes ele disse "não funcionou / continua / já tentei" (in_loop). 2 sinais de
    # falha → o procedimento não resolveu → humano. O fix-streak é backstop; tema novo zera.
    if sess.get("last_theme") != det.predicted_theme.value:
        sess["stuck_signals"] = 0                       # tema novo → zera a contagem de falhas
    sess["last_theme"] = det.predicted_theme.value
    if det.in_loop:
        sess["stuck_signals"] = sess.get("stuck_signals", 0) + 1
    sig = sess.get("stuck_signals", 0)
    # esgotamento = o CLIENTE sinalizou falha (não conversa longa!): 2 "não funcionou",
    # ou 1 + emoção. Conversa produtiva e longa NÃO escala — só falha repetida.
    stuck = sig >= 2 or (sig >= 1 and (det.frustrated or det.disappointed))
    inp = derive_decision_input(det, sess["profile"], txt, base, doc=doc, stuck=stuck)
    # streak é incrementado em handle_message SÓ quando entrega um fix real (pergunta de
    # esclarecimento não conta) — senão o esgotamento dispara cedo demais.
    return det, docs, decide(inp), inp


_ACTION_EMOJI = {
    InterceptionAction.AI_RESOLVE: "🟢", InterceptionAction.AI_RESOLVE_SILENT: "🟢",
    InterceptionAction.AI_ASSIST: "🟡", InterceptionAction.HUMAN_HANDOFF: "🔴",
    InterceptionAction.NO_INTERCEPT: "⚪",
}
_NAT_PT = {"system_signaled": "evento de sistema", "behavior_inferred": "comportamento",
           "absence_detected": "ausência (silenciosa)"}


def _brain_text(det, docs, dec, inp) -> str:
    doc = docs[0] if docs else None
    sinais = [n for n, v in (("pediu humano", det.asked_for_human), ("frustrado", det.frustrated),
                             ("desanimado", det.disappointed), ("em loop", det.in_loop),
                             ("confuso", det.confused), ("⚠️ vulnerável", det.safety_concern)) if v]
    emoji = _ACTION_EMOJI.get(dec.action, "")
    return (
        "🧠 *como o motor pensou*\n\n"
        f"🔎 *detectou:* {det.predicted_theme.value} · {_NAT_PT.get(det.predicted_nature.value, det.predicted_nature.value)} "
        f"_({det.theme_confidence:.0%} de certeza)_\n"
        f"📡 *sinais:* {', '.join(sinais) or 'nenhum'}\n"
        f"📊 *4 números:* criticidade {inp.criticality} · confiança-em-jogo {inp.trust_risk} · "
        f"resolubilidade {inp.resolvability} · certeza {inp.detection_confidence}\n\n"
        f"{emoji} *decisão: {ACTION_LABEL.get(dec.action, dec.action.value)}*  ·  prioridade {dec.priority}\n"
        f"_↳ {dec.reason}_\n"
        f"📚 *fonte:* {doc.id if doc else '—'}" + (f" — {doc.title}" if doc else " (nada ancorado)")
    )


def _handoff_card(det, dec, inp, sess) -> str:
    hora = datetime.fromisoformat(sess["started_at"]).hour
    pack = build_context_pack(det, dec, inp.criticality, sess["profile"].segment, hora)
    return (
        "📋 *Pacote pro atendente humano* (handoff quente)\n"
        f"cliente: {pack.segment.upper()} · atrito: `{pack.theme.value}` · {pack.nature.value} · crit {pack.criticality}\n"
        f"evidência: {pack.evidence}\n"
        f"sinais: {', '.join(pack.signals) or '—'}\n"
        f"→ fila *{pack.routing.specialty}* · prioridade {pack.routing.priority:.2f}\n"
        f"_{pack.routing.note}_"
    )


# ─── Telegram API (httpx) ────────────────────────────────────────────────────
def send(chat_id, text):
    """Manda em Markdown; se o Telegram rejeitar (char especial do LLM/cérebro),
    reenvia em TEXTO PURO — nunca cai calado (dead air na demo)."""
    try:
        r = httpx.post(f"{API}/sendMessage", json={"chat_id": chat_id, "text": text,
                                                   "parse_mode": "Markdown"}, timeout=20)
        if not r.json().get("ok"):
            httpx.post(f"{API}/sendMessage", json={"chat_id": chat_id, "text": text}, timeout=20)
    except Exception as e:
        log.warning("sendMessage falhou: %s", e)


def typing(chat_id):
    try:
        httpx.post(f"{API}/sendChatAction", json={"chat_id": chat_id, "action": "typing"}, timeout=10)
    except Exception:
        pass


def transcribe(file_id) -> str | None:
    """Áudio do cliente → texto (Whisper). On-brand: o público do Jota fala por áudio.
    Baixa o .ogg do Telegram e transcreve; None se indisponível (degrada gracioso)."""
    if LLM is None:
        return None
    try:
        gf = httpx.get(f"{API}/getFile", params={"file_id": file_id}, timeout=20).json()
        path = gf["result"]["file_path"]
        audio = httpx.get(f"https://api.telegram.org/file/bot{TOKEN}/{path}", timeout=45).content
        tr = LLM.audio.transcriptions.create(model="whisper-1", language="pt",
                                             file=("audio.ogg", audio))
        return (tr.text or "").strip()
    except Exception as e:
        log.warning("transcribe falhou (%s)", type(e).__name__)
        return None


def _new_session(seg="pf", events=None, context_text="", started=None, name=""):
    return {"segment": seg, "events": events or [], "context_text": context_text,
            "started_at": started or datetime.now().isoformat(), "history": [],
            "profile": UserProfile(segment=seg), "name": name, "greeted": False,
            "stuck_signals": 0}


def _saudacao(name):
    nome = f", {name}" if name else ""
    return (f"Oi{nome}! Sou a *Aline*, do suporte do Jota, pode contar comigo por aqui. 💚\n\n"
            "Me diz como posso te ajudar hoje?")


def _saudacao_curta(name):
    """1º contato quando o cliente JÁ trouxe o problema — sem 'me diz como ajudar' (redundante)."""
    nome = f", {name}" if name else ""
    return f"Oi{nome}! Sou a *Aline*, do suporte do Jota — já vou te ajudar com isso. 💚"


# caminho alternativo por tema, oferecido no handoff (não deixa o cliente na mão enquanto espera)
WORKAROUND = {
    _T.ACCOUNT_ACCESS: "enquanto isso, dá pra ver seu saldo e usar o Jota *aqui no WhatsApp* mesmo",
}


def handle_command(chat_id, cmd, name=""):
    cmd = cmd.split()[0].lower()
    if cmd in ("/start", "/ajuda", "/help"):
        SESSIONS[chat_id] = _new_session(name=name)
        SESSIONS[chat_id]["greeted"] = True
        send(chat_id, _saudacao(name))
        send(chat_id, "_(demo do case · /debug mostra o cérebro · /reset zera · "
                      "/pix /kyc /falatap /pixerrado /excluir = cenários proativos)_")
        return
    if cmd == "/reset":
        SESSIONS[chat_id] = _new_session(name=name)
        send(chat_id, "🧹 Conversa zerada. Pode mandar um novo caso do zero.")
        return
    if cmd == "/debug":
        if chat_id in DEBUG:
            DEBUG.discard(chat_id); send(chat_id, "🧠 debug *off*")
        else:
            DEBUG.add(chat_id); send(chat_id, "🧠 debug *on* — vou mostrar o cérebro a cada turno")
        return
    if cmd in COMMANDS:
        c = SCENARIOS[COMMANDS[cmd]]
        seg = c.get("segment", "pf")
        ctx = " ".join(m["text"] for m in c["mensagens"] if m["sender"] == "customer")
        sess = _new_session(seg, [(e["event_type"], e["occurred_at"]) for e in c.get("eventos", [])],
                            ctx, c["started_at"], name=name)
        SESSIONS[chat_id] = sess
        det, docs, dec, inp = analisar(sess)
        send(chat_id, "🔔 _atrito detectado — agindo antes do chamado existir_")
        typing(chat_id)
        opener = gerar([], det.predicted_theme, seg, docs[0] if docs else None,
                       det.disappointed, opener=True)
        sess["history"].append({"role": "assistant", "content": opener})
        send(chat_id, opener)
        if chat_id in DEBUG:
            send(chat_id, _brain_text(det, docs, dec, inp))
        return
    send(chat_id, "Não conheci esse comando. /ajuda pra ver os atalhos.")


def run_turn(sess, text):
    """O CÉREBRO de um turno, sem Telegram: detecta → decide → redige/handoff → atualiza
    o esgotamento. Reusado pelo bot (handle_message) E pelo simulador de conversas (eval)."""
    sess["history"].append({"role": "user", "content": text})
    det, docs, dec, inp = analisar(sess)
    if dec.action == InterceptionAction.HUMAN_HANDOFF:
        em_horario = 9 <= datetime.now().hour < 20
        sla = ("Como é horário comercial (9h–20h), te retornam em seguida."
               if em_horario else
               "O time atende das *9h às 20h* — te retornam logo na abertura, com prioridade.")
        reply = ("Pra resolver isso direito, vou te conectar com uma *pessoa do time*, que continua "
                 f"com você e te retorna *aqui mesmo, neste número*. {sla} Todo o contexto vai junto, "
                 "você não repete nada. 🙏")
        work = WORKAROUND.get(det.predicted_theme)
        if work:
            reply += f"\n\nE {work}, sem precisar esperar. 😉"
        kind = "handoff"
    else:
        reply = gerar(sess["history"], det.predicted_theme, seg=sess["segment"],
                      doc=docs[0] if docs else None, disappointed=det.disappointed)
        sess["history"].append({"role": "assistant", "content": reply})
        kind = "resolve"
    return {"det": det, "docs": docs, "dec": dec, "inp": inp, "reply": reply, "kind": kind}


def handle_message(chat_id, text, name=""):
    sess = SESSIONS.get(chat_id) or _new_session(name=name)
    SESSIONS[chat_id] = sess
    if name and not sess.get("name"):
        sess["name"] = name
    # 1º contato sem /start: a Aline se apresenta (curto — o cliente já trouxe o problema)
    if not sess.get("greeted"):
        sess["greeted"] = True
        send(chat_id, _saudacao_curta(sess.get("name", "")))
    typing(chat_id)
    r = run_turn(sess, text)
    send(chat_id, r["reply"])
    if r["kind"] == "handoff" and chat_id in DEBUG:
        send(chat_id, _handoff_card(r["det"], r["dec"], r["inp"], sess))
    if chat_id in DEBUG:
        send(chat_id, _brain_text(r["det"], r["docs"], r["dec"], r["inp"]))


def main():
    if not TOKEN:
        print("✗ falta TELEGRAM_BOT_TOKEN no .env (pegue com o @BotFather).")
        sys.exit(1)
    me = httpx.get(f"{API}/getMe", timeout=20).json()
    log.info("bot @%s no ar (long polling). Ctrl+C pra parar.", me.get("result", {}).get("username", "?"))
    offset = None
    while True:
        try:
            r = httpx.get(f"{API}/getUpdates",
                          params={"offset": offset, "timeout": 30}, timeout=40).json()
        except Exception as e:
            log.warning("getUpdates: %s", e); time.sleep(3); continue
        for upd in r.get("result", []):
            offset = upd["update_id"] + 1
            msg = upd.get("message") or upd.get("edited_message")
            if not msg:
                continue
            chat_id = msg["chat"]["id"]
            name = (msg.get("from") or {}).get("first_name", "")
            if "text" not in msg:
                voice = msg.get("voice") or msg.get("audio")
                if voice:                                  # ÁUDIO → transcreve (Whisper) → pipeline
                    typing(chat_id)
                    trans = transcribe(voice["file_id"])
                    if trans:
                        send(chat_id, f"🎤 _entendi seu áudio:_ “{trans}”")
                        log.info("← %s (%s) [áudio]: %s", chat_id, name, trans[:60])
                        try:
                            handle_message(chat_id, trans, name)
                        except Exception as e:
                            log.exception("erro no áudio: %s", e)
                            send(chat_id, "Ops, tive um problema — tenta de novo? 🙏")
                        continue
                send(chat_id, "Recebi, mas não consegui entender o áudio/arquivo — me manda por *texto*? 🙏")
                continue
            text = msg["text"].strip()
            log.info("← %s (%s): %s", chat_id, name, text[:60])
            try:
                (handle_command if text.startswith("/") else handle_message)(chat_id, text, name)
            except Exception as e:
                log.exception("erro tratando msg: %s", e)
                send(chat_id, "Ops, tive um problema aqui — tenta de novo? 🙏")


if __name__ == "__main__":
    main()
