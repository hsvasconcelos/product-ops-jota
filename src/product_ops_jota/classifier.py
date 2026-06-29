"""Classificador de atrito (Mundo 1 + Mundo 2) — v1 por REGRAS/heurística.
=============================================================================
Lê uma conversa BRUTA (texto das mensagens + eventos de sistema do mesmo
usuário) e DERIVA o rótulo — tema, natureza e sinais comportamentais. É o que
transforma dado em detecção: nada de ler o gabarito (as colunas gold_*).

Um motor, dois mundos:
  · Mundo 1 (análise): roda em lote sobre as 1000 conversas → o eval mede a
    qualidade contra o gabarito (evals/run_classifier_eval.py).
  · Mundo 2 (interceptação): roda ao vivo numa conversa nova → decide agir.

PRINCÍPIO DE HONESTIDADE (o que separa detecção de trapaça):
  A natureza é decidida correlacionando evento↔conversa por `user_id` +
  JANELA DE TEMPO, IGNORANDO o FK `events.conversation_id`. Esse FK é parte do
  gabarito gerado ("este evento causou esta conversa"); na vida real um
  kyc.failed chega do backend sem vir pré-ligado a um chamado — ligar os dois
  É o problema de detecção. Ler o FK seria colar a resposta.

DECISÃO CONSCIENTE DE ESCOPO (não é buraco):
  O v1 NÃO prevê criticidade. Criticidade = erro × momento × o que está em
  jogo (ver friction_model) — depende de contexto (estágio da jornada, PJ no
  meio de uma venda, histórico) que regras de texto não capturam sem chutar.
  Fica como gabarito (gold_criticality) e é a evolução natural pro LLM-as-judge.

EVOLUÇÃO DOCUMENTADA (não implementada aqui):
  v1 = regras/heurística (este módulo). v2 = LLM-as-judge aplicado SÓ no
  resíduo ambíguo (theme com baixa confiança, empates) — controle e custo sob
  medida, em vez de jogar um LLM em tudo.
"""
from __future__ import annotations

import unicodedata
from datetime import datetime, timedelta

from pydantic import BaseModel, Field

from .friction_model import FrictionNature, SupportTheme

# ─────────────────────────────────────────────────────────────────────────────
# POLICY (configuração tipada e nomeada) — os botões do classificador.
# O MECANISMO (as funções abaixo) é estável; isto aqui se recalibra sem tocar
# na lógica. (policy vs mechanism)
# ─────────────────────────────────────────────────────────────────────────────

# Janela de correlação evento↔conversa (por user_id + tempo, NUNCA pelo FK).
# O evento correlacionado nasce pouco antes do início; a janela o captura sem
# laçar eventos órfãos distantes no tempo (que viram ruído realista de detecção).
EVENT_PRE_WINDOW = timedelta(minutes=90)     # evento até 90 min ANTES do início
EVENT_POST_WINDOW = timedelta(minutes=30)    # ou até 30 min após a última msg

# Palavras/padrões por tema, no vocabulário do CLIENTE (texto normalizado,
# sem acento, minúsculo). Detecção por substring — simples e auditável.
THEME_KEYWORDS: dict[SupportTheme, list[str]] = {
    SupportTheme.PIX: ["pix", "chave", "transferi", "transferencia", "qr code", "pix parcelado"],
    SupportTheme.ACCOUNT_ACCESS: ["acessar", "acesso", "nao entro", "nao consigo entrar", "login",
                                  "senha", "fecha sozinho", "abre e fecha", "app fecha", "travou o app"],
    SupportTheme.KYC: ["abrir conta", "abrir minha conta", "abertura de conta", "verificacao",
                       "selfie", "documento", " rg", "cnh", "nao foi liberada", "nao posso abrir",
                       "onboarding", "analise de cadastro"],
    SupportTheme.FALA_TAP: ["fala tap", "maquininha", "vendi", "venda", "nao caiu", "nao cai",
                            "bloqueado", "liquidacao", "passou o cartao", "recebi pela maquina"],
    SupportTheme.BOLETO: ["boleto", "estorno", "estornar", "cobrado", "cobranca", "duplicada",
                          "duas vezes", "em aberto", "paguei e continua"],
    SupportTheme.ACCOUNT_DATA: ["alterar meus dados", "alterar dados", "atualizar", "mudar meu cadastro",
                                "mudar cadastro", "excluir minha conta", "trocar email", "trocar o email",
                                "atualizar meu telefone", "mudar telefone"],
    SupportTheme.YIELD_OPEN_FINANCE: ["rende", "rendimento", "rende+", "open finance", "consentimento",
                                      "conectei outro banco", "nao rende"],
}
# Desempate quando >1 tema empata em nº de matches (mais específico → mais genérico).
THEME_PRIORITY = [
    SupportTheme.FALA_TAP, SupportTheme.KYC, SupportTheme.PIX, SupportTheme.BOLETO,
    SupportTheme.YIELD_OPEN_FINANCE, SupportTheme.ACCOUNT_DATA, SupportTheme.ACCOUNT_ACCESS,
    SupportTheme.OTHER,
]

# Sinais comportamentais derivados do TEXTO do cliente.
HUMAN_REQUEST_PATTERNS = ["humano", "atendente", "pessoa de verdade", "falar com alguem",
                          "falar com uma pessoa", "nao robo", "nao quero robo", "me passa pra um"]
FRUSTRATION_PATTERNS = ["caralho", "porra", "merda", "foder", "puto", "pqp", "bosta", "lixo",
                        "palhacada", "absurdo", "procon", "reclame aqui", "processar", "horrivel",
                        "vergonha", "ridiculo"]
LOOP_PATTERNS = ["ja falei", "de novo", "como eu disse", "ja tentei", "ja fiz isso",
                 "voce nao leu", "ja expliquei", "mesma coisa"]

# Limiar de similaridade p/ considerar duas mensagens do cliente "a mesma"
# (sinal de loop sem precisar de marcador textual explícito).
LOOP_SIMILARITY_MIN = 0.85
NATURE_CONF_WITH_EVENT = 0.95    # achou evento correlacionado → bem confiante
NATURE_CONF_NO_EVENT = 0.75      # sem evento → behavior_inferred, menos certo
THEME_CONF_NO_MATCH = 0.30       # nenhum keyword bateu → cai em OTHER, baixa conf.


# ─────────────────────────────────────────────────────────────────────────────
# Saída tipada (fail-fast: confidências têm de viver em [0,1]).
# ─────────────────────────────────────────────────────────────────────────────
class ClassifierOutput(BaseModel):
    predicted_theme: SupportTheme
    theme_confidence: float = Field(ge=0.0, le=1.0)
    predicted_nature: FrictionNature
    nature_confidence: float = Field(ge=0.0, le=1.0)
    # sinais comportamentais derivados do texto
    asked_for_human: bool
    frustrated: bool
    in_loop: bool
    # explicabilidade: por que decidiu assim
    matched_keywords: list[str] = Field(default_factory=list)
    correlated_event: str | None = None


# ─────────────────────────────────────────────────────────────────────────────
# MECANISMO — funções puras. Recebem só DADO BRUTO, nunca as colunas gold_*.
# ─────────────────────────────────────────────────────────────────────────────
def _normalize(text: str) -> str:
    """minúsculo + sem acento — pra casar 'verificação' com 'verificacao'."""
    t = unicodedata.normalize("NFKD", text or "")
    t = "".join(ch for ch in t if not unicodedata.combining(ch))
    return t.lower()


def _similar(a: str, b: str) -> float:
    """Similaridade barata (Jaccard de tokens) — pra detectar repetição."""
    sa, sb = set(a.split()), set(b.split())
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


def _parse_ts(ts: str) -> datetime:
    return datetime.fromisoformat(ts)


def classify_theme(customer_text_norm: str) -> tuple[SupportTheme, float, list[str]]:
    """Pontua cada tema por nº de keywords batidas; argmax, desempate por
    prioridade; zero match → OTHER. Confiança ~ margem do vencedor."""
    hits: dict[SupportTheme, list[str]] = {}
    for theme, kws in THEME_KEYWORDS.items():
        matched = [kw.strip() for kw in kws if kw in customer_text_norm]
        if matched:
            hits[theme] = matched
    if not hits:
        return SupportTheme.OTHER, THEME_CONF_NO_MATCH, []

    max_score = max(len(m) for m in hits.values())
    empatados = [t for t, m in hits.items() if len(m) == max_score]
    vencedor = min(empatados, key=lambda t: THEME_PRIORITY.index(t))
    total = sum(len(m) for m in hits.values())
    # confiança: fração dos matches que apontam pro vencedor, com piso/teto.
    conf = round(min(0.99, 0.5 + 0.5 * (max_score / total)), 2)
    return vencedor, conf, hits[vencedor]


def find_correlated_event(events, started_at: datetime, last_msg_at: datetime) -> str | None:
    """Há evento de sistema do MESMO usuário na janela temporal da conversa?
    Correlaciona por tempo — IGNORA events.conversation_id de propósito."""
    ini = started_at - EVENT_PRE_WINDOW
    fim = last_msg_at + EVENT_POST_WINDOW
    for event_type, occurred_at in events:
        if ini <= _parse_ts(occurred_at) <= fim:
            return event_type
    return None


def classify_conversation(messages, events, started_at) -> ClassifierOutput:
    """Deriva o rótulo de UMA conversa a partir do bruto.

    messages: lista de (sender, text, sent_at) em ordem.
    events:   lista de (event_type, occurred_at) do MESMO user (sem o FK).
    started_at: início da conversa (fato observável).
    """
    started = started_at if isinstance(started_at, datetime) else _parse_ts(started_at)
    customer_msgs = [(txt or "") for snd, txt, _ in messages if snd == "customer"]
    customer_norm = _normalize(" \n ".join(customer_msgs))
    last_msg_at = _parse_ts(messages[-1][2]) if messages else started

    # tema
    theme, theme_conf, matched = classify_theme(customer_norm)

    # natureza (correlação honesta por tempo)
    evt = find_correlated_event(events, started, last_msg_at)
    if evt is not None:
        nature, nature_conf = FrictionNature.SYSTEM_SIGNALED, NATURE_CONF_WITH_EVENT
    else:
        nature, nature_conf = FrictionNature.BEHAVIOR_INFERRED, NATURE_CONF_NO_EVENT

    # sinais comportamentais (do texto)
    asked_human = any(p in customer_norm for p in HUMAN_REQUEST_PATTERNS)
    frustrated = any(p in customer_norm for p in FRUSTRATION_PATTERNS)
    in_loop = any(p in customer_norm for p in LOOP_PATTERNS) or _has_repetition(customer_msgs)

    return ClassifierOutput(
        predicted_theme=theme, theme_confidence=theme_conf,
        predicted_nature=nature, nature_confidence=nature_conf,
        asked_for_human=asked_human, frustrated=frustrated, in_loop=in_loop,
        matched_keywords=matched, correlated_event=evt,
    )


def _has_repetition(customer_msgs: list[str]) -> bool:
    """Cliente repetiu (quase) a mesma mensagem? Sinal de loop sem marcador."""
    norm = [_normalize(m) for m in customer_msgs if m.strip()]
    for i in range(len(norm)):
        for j in range(i + 1, len(norm)):
            if _similar(norm[i], norm[j]) >= LOOP_SIMILARITY_MIN:
                return True
    return False


# ─────────────────────────────────────────────────────────────────────────────
# Carga do BRUTO a partir do banco — sem JAMAIS tocar nas colunas gold_*.
# started_at e user_id são FATOS observáveis; os eventos vêm por user_id (não
# pelo FK conversation_id). É o que o classificador "veria" na vida real.
# ─────────────────────────────────────────────────────────────────────────────
def load_raw_conversation(conn, conversation_id: str):
    user_id, started_at = conn.execute(
        "SELECT user_id, started_at FROM conversations WHERE conversation_id=?",
        (conversation_id,),
    ).fetchone()
    messages = conn.execute(
        "SELECT sender, text, sent_at FROM messages WHERE conversation_id=? ORDER BY turn_index",
        (conversation_id,),
    ).fetchall()
    events = conn.execute(
        "SELECT event_type, occurred_at FROM events WHERE user_id=?",  # SEM filtrar por conversation_id
        (user_id,),
    ).fetchall()
    return messages, events, started_at
