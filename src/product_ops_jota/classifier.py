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
                                "mudar cadastro", "excluir minha conta", "excluir a conta", "excluir conta",
                                "excluir a minha conta", "apagar meus dados", "apagar dados", "apagar meus dado",
                                "deletar", "encerrar conta", "encerrar minha conta", "cancelar minha conta",
                                "cancelar a conta", "lgpd", "trocar email", "trocar o email",
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

# Doc da KB → tema, p/ classificação SEMÂNTICA (o tema do doc mais relevante à
# conversa). Âncora = procedimentos da KB (não as frases do banco) → eval honesto.
DOC_THEME_PREFIX = {
    "KB-PIX": SupportTheme.PIX, "KB-KYC": SupportTheme.KYC,
    "KB-FALATAP": SupportTheme.FALA_TAP, "KB-BOLETO": SupportTheme.BOLETO,
    "KB-ESTORNO": SupportTheme.BOLETO, "KB-CONECTA": SupportTheme.YIELD_OPEN_FINANCE,
    "KB-RENDE": SupportTheme.YIELD_OPEN_FINANCE, "KB-ACESSO": SupportTheme.ACCOUNT_ACCESS,
    "KB-CONTA": SupportTheme.ACCOUNT_ACCESS, "KB-DADOS": SupportTheme.ACCOUNT_DATA,
}


def doc_theme(doc_id: str) -> SupportTheme | None:
    """Tema de um doc pela convenção de id (KB-ACESSO → ACCOUNT_ACCESS)."""
    for prefix, th in DOC_THEME_PREFIX.items():
        if doc_id.startswith(prefix):
            return th
    return None


def prefer_theme(docs: list, theme: SupportTheme, confidence: float = 1.0,
                 floor: float = 0.60) -> list:
    """Reordena docs (estável) pondo à frente os cujo tema BATE com o tema detectado.
    O classificador já é a melhor aposta do tema; o RAG deve respeitá-la em vez de
    rankear só por termo — o BM25 leve confunde "acessar meu wpp" (acesso) com
    "reconectar banco" (open finance). Age só com tema confiável e não-OTHER; sem
    doc do tema entre os candidatos, devolve a ordem original (não inventa)."""
    if theme == SupportTheme.OTHER or confidence < floor:
        return docs
    match = [d for d in docs if doc_theme(d.id) == theme]
    return match + [d for d in docs if doc_theme(d.id) != theme] if match else docs

# Evento de sistema → tema (determinístico). Quando há evento correlacionado, ele é
# evidência FORTE do tema — funde com a semântica do texto ("determinístico onde dá").
EVENT_THEME = {
    "kyc.failed": SupportTheme.KYC,
    "pix.returned": SupportTheme.PIX, "pix.key_not_found": SupportTheme.PIX,
    "pix.key_invalid": SupportTheme.PIX,
    "tap_to_pay.settlement_delayed": SupportTheme.FALA_TAP, "settlement.held": SupportTheme.FALA_TAP,
    "tap_to_pay.payment_approved": SupportTheme.FALA_TAP,
    "session.crashed": SupportTheme.ACCOUNT_ACCESS,
    "charge.duplicated": SupportTheme.BOLETO, "payment.duplicate": SupportTheme.BOLETO,
    "payment.charged": SupportTheme.BOLETO,   # cobrança (duplicidade → estorno) vive no tema boleto
    "open_finance.consent_expired": SupportTheme.YIELD_OPEN_FINANCE,
    "bank.sync_stale": SupportTheme.YIELD_OPEN_FINANCE,
    "data_change.requested": SupportTheme.ACCOUNT_DATA,
}

# ── Sinais comportamentais (texto do cliente) ──────────────────────────────
# Determinístico onde dá (contadores/repetição); calibrado-com-limiar onde é
# fuzzy (frustração/confusão). NUNCA "vibe" — todo sinal expõe valor + limiar.
HUMAN_REQUEST_PATTERNS = ["humano", "atendente", "pessoa de verdade", "falar com alguem",
                          "falar com uma pessoa", "nao robo", "nao quero robo", "me passa pra um"]
# léxico de frustração (peso 1) e gatilhos de ESCALADA (peso 2 — sinal forte de chamado iminente)
FRUSTRATION_PATTERNS = ["caralho", "porra", "merda", "foder", "puto", "pqp", "bosta", "lixo",
                        "palhacada", "absurdo", "horrivel", "vergonha", "ridiculo", "nao aguento"]
ESCALATION_PATTERNS = ["procon", "reclame aqui", "processar", "vou cancelar", "nunca mais", "advogado"]
LOOP_PATTERNS = ["ja falei", "de novo", "como eu disse", "ja tentei", "ja fiz isso",
                 "voce nao leu", "ja expliquei", "mesma coisa",
                 # feedback de que a tentativa FALHOU — o procedimento não resolveu (esgotamento)
                 "nao resolveu", "nao resolve", "nao funcionou", "nao funciona", "nao adiantou",
                 "nao deu certo", "continua igual", "continua sem", "sigo sem", "nada aconteceu",
                 "nao fluiu", "sem sucesso", "nao mudou nada", "continua a mesma"]
# confusão de usabilidade / canal (o "não entender a usabilidade" que o case cita)
CONFUSION_PATTERNS = ["nao entendi", "como faço", "como faco", "como eu faco", "cade", "onde fica",
                      "onde que", "nao acho", "nao sei como", "como assim", "que isso", "onde encontro",
                      "nao consigo achar", "no app nao", "aqui no whatsapp nao", "nao tem aqui"]
# frustração FRIA — decepção/tristeza/desânimo. NÃO é raiva (não cruza FRUSTRATION):
# alimenta o TOM da resposta (mais acolhimento), nunca o gate de decisão. O cliente
# que desiste em silêncio é o que o princípio #3 manda cuidar.
COLD_FRUSTRATION_PATTERNS = ["triste", "tristeza", "que pena", "decepcionad", "decepcao",
                             "chatead", "desanimad", "desanima", "sem esperanca", "perdi a esperanca",
                             "desisti", "cansei", "nao adianta", "perdi a vontade", "frustrad",
                             "magoad", "sem animo", "nao consegue me ajudar", "ninguem me ajuda",
                             "ninguem resolve", "que saco"]
# SEGURANÇA / vulnerabilidade — ludopatia, autoexclusão. Dispara o gate de segurança.
# stems propositalmente LARGOS: em segurança, falso-positivo (escalar à toa) é o lado
# seguro; falso-negativo (não ver um vulnerável) é o custo alto. Validado pelo eval-LLM,
# que pegou "viciado em apostas, não consigo parar de gastar" passando batido no keyword exato.
VULNERABILITY_PATTERNS = ["viciad", "vicio", "ludopat", "aposta", "apostei", "apostando",
                          "jogo do tigrinho", "nao consigo parar de gastar", "nao consigo parar de jogar",
                          "nao consigo parar de apostar", "parar de gastar", "parar de jogar",
                          "parar de apostar", "perdi tudo apostando", "perdi tudo no jogo",
                          "me bloqueia pra eu nao gastar", "bloqueia minha conta pra", "bloquear minha conta pra"]

# Limiares CALIBRADOS (auditáveis) — policy, recalibrável sem tocar na lógica.
LOOP_SIMILARITY_MIN = 0.85       # similaridade p/ considerar 2 msgs "a mesma" (repetição)
RETRY_MIN = 1                    # ≥1 repetição quase-idêntica → retentativa/loop
FRUSTRATION_FLOOR = 1.0          # score (léxico=1, escalada=2) ≥ piso → frustrado
CONFUSION_FLOOR = 1              # ≥1 marcador de confusão → confuso
DISAPPOINTMENT_FLOOR = 1        # ≥1 marcador de desânimo/decepção → calibra o TOM
NATURE_CONF_WITH_EVENT = 0.95    # achou evento correlacionado → bem confiante
NATURE_CONF_NO_EVENT = 0.75      # sem evento → behavior_inferred, menos certo
NATURE_CONF_ABSENCE = 0.85       # ausência detectada (timer) → determinístico, confiante
THEME_CONF_NO_MATCH = 0.30       # nenhum keyword bateu → cai em OTHER, baixa conf.
JUDGE_CONF_FLOOR = 0.60          # abaixo disto o tema é AMBÍGUO → aciona LLM-as-judge (se houver)


# ─────────────────────────────────────────────────────────────────────────────
# Saída tipada (fail-fast: confidências têm de viver em [0,1]).
# ─────────────────────────────────────────────────────────────────────────────
class Signal(BaseModel):
    """Um sinal de detecção, AUDITÁVEL: expõe valor observado, limiar e se é
    determinístico. É o que torna a detecção legível (alimenta o radar)."""
    tipo: str                              # evento|retentativa|repeticao|frustracao|confusao|pedido_humano|ausencia|tema
    rotulo: str                            # legível pro radar
    deterministico: bool                   # True = evento/contador/timer; False = fuzzy calibrado
    disparou: bool                         # cruzou o limiar / ocorreu
    valor: str | float | None = None       # observado (contagem, score, event_type)
    limiar: float | None = None            # limiar (auditabilidade)


class ClassifierOutput(BaseModel):
    predicted_theme: SupportTheme
    theme_confidence: float = Field(ge=0.0, le=1.0)
    predicted_nature: FrictionNature
    nature_confidence: float = Field(ge=0.0, le=1.0)
    # sinais comportamentais derivados do texto (booleans p/ compatibilidade)
    asked_for_human: bool
    frustrated: bool
    in_loop: bool
    confused: bool = False
    disappointed: bool = False   # frustração FRIA (decepção/desânimo) — alimenta o TOM, não o gate
    safety_concern: bool = False # vulnerabilidade (ludopatia/autoexclusão) — dispara o gate de segurança
    # contadores/escalas determinísticos e auditáveis
    retry_count: int = 0
    frustration_score: float = 0.0
    # explicabilidade
    theme_source: str = "keyword"                          # "keyword" | "semantico"
    theme_doc: str | None = None                           # doc âncora (modo semântico)
    matched_keywords: list[str] = Field(default_factory=list)
    correlated_event: str | None = None
    signals: list[Signal] = Field(default_factory=list)   # todos avaliados, auditáveis


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


# Âncoras de tema (lado-PROBLEMA, vocabulário do cliente) — independentes do banco
# e do dado → eval honesto. A classificação semântica é por cosseno contra elas.
THEME_ANCHORS: dict[SupportTheme, list[str]] = {
    SupportTheme.PIX: ["não consigo fazer um pix", "a chave pix não funciona ou não é encontrada",
                       "erro ao enviar transferência pix", "mandei um pix para a pessoa errada",
                       "cadê meu limite de pix parcelado"],
    SupportTheme.KYC: ["não consigo abrir minha conta", "a selfie/biometria não passou na verificação",
                       "minha conta não foi liberada na abertura", "travou no cadastro de abertura de conta"],
    SupportTheme.FALA_TAP: ["vendi pela maquininha e o dinheiro não caiu", "recebimento da venda no fala tap travado",
                            "a venda aprovou mas o valor não veio", "meu dinheiro da maquininha está bloqueado"],
    SupportTheme.BOLETO: ["fui cobrado duas vezes, quero estorno", "cobrança em duplicidade no meu cartão",
                          "preciso estornar um pagamento", "boleto agendado não foi pago", "paguei e continua em aberto"],
    SupportTheme.ACCOUNT_ACCESS: ["não consigo acessar o aplicativo", "o app fecha sozinho ao abrir",
                                  "não consigo acessar a minha conta", "não consigo acessar minha conta",
                                  "não entro na minha conta de jeito nenhum", "esqueci a senha e não acesso",
                                  "não consigo entrar na conta que já tenho pra ver meu saldo",
                                  "preciso acessar minha conta pra resgatar meu dinheiro e não consigo"],
    SupportTheme.ACCOUNT_DATA: ["quero alterar meus dados cadastrais", "trocar meu telefone ou email",
                               "quero excluir a minha conta", "pedi pra excluir a conta e ela voltou",
                               "quero apagar meus dados e encerrar a conta", "deletar minha conta pela lgpd"],
    SupportTheme.YIELD_OPEN_FINANCE: ["dúvida sobre o rendimento de 100% do cdi", "conectei outro banco e o saldo sumiu",
                                      "open finance não atualiza o saldo", "quero trazer dinheiro de outro banco"],
    SupportTheme.OTHER: ["tenho uma dúvida geral", "não encontro essa função, onde fica",
                         "não entendi como usar isso", "queria entender como funciona"],
}
_ANCHOR_CACHE: dict = {}   # id(retriever) → (lista_de_temas, matriz_embeddings)


def classify_theme_semantic(customer_text: str, retriever) -> tuple[SupportTheme, float, str | None]:
    """Tema por SIGNIFICADO: cosseno entre o texto do cliente e ÂNCORAS de tema.
    Determinístico (dado o modelo), auditável (o cosseno é o score), robusto a
    paráfrase/typo — onde o keyword quebra. Cai no keyword se densos indisponíveis."""
    import numpy as np
    key = id(retriever)
    if key not in _ANCHOR_CACHE:
        temas, frases = [], []
        for th, ps in THEME_ANCHORS.items():
            for p in ps:
                temas.append(th); frases.append(p)
        emb = retriever.embed(frases)
        if emb is None:                                   # modo BM25 → sem semântica
            t, c, _ = classify_theme(_normalize(customer_text))
            return t, c, None
        _ANCHOR_CACHE[key] = (temas, np.asarray(emb))
    temas, M = _ANCHOR_CACHE[key]
    q = retriever.embed([customer_text])
    if q is None:
        t, c, _ = classify_theme(_normalize(customer_text))
        return t, c, None
    sims = M @ np.asarray(q)[0]
    i = int(sims.argmax())
    return temas[i], round(float(sims[i]), 2), THEME_ANCHORS[temas[i]][0]


def find_correlated_event(events, started_at: datetime, last_msg_at: datetime) -> str | None:
    """Há evento de sistema do MESMO usuário na janela temporal da conversa?
    Correlaciona por tempo — IGNORA events.conversation_id de propósito."""
    ini = started_at - EVENT_PRE_WINDOW
    fim = last_msg_at + EVENT_POST_WINDOW
    for event_type, occurred_at in events:
        if ini <= _parse_ts(occurred_at) <= fim:
            return event_type
    return None


def _retry_count(customer_msgs: list[str]) -> int:
    """Determinístico: quantas mensagens do cliente são quase-iguais a uma anterior
    (retentativa/loop sem precisar de marcador textual). 0 = sem repetição."""
    norm = [_normalize(m) for m in customer_msgs if m.strip()]
    return sum(1 for i in range(len(norm))
               if any(_similar(norm[i], norm[j]) >= LOOP_SIMILARITY_MIN for j in range(i)))


def _frustration_score(customer_norm: str) -> float:
    """Calibrado e auditável: léxico de frustração pesa 1; gatilho de ESCALADA pesa 2."""
    return float(sum(1 for p in FRUSTRATION_PATTERNS if p in customer_norm)) \
        + 2.0 * sum(1 for p in ESCALATION_PATTERNS if p in customer_norm)


def _confusion_count(customer_norm: str) -> int:
    return sum(1 for p in CONFUSION_PATTERNS if p in customer_norm)


def _disappointment_count(customer_norm: str) -> int:
    """Frustração FRIA: decepção/tristeza/desânimo. Separado da raiva — calibra o tom."""
    return sum(1 for p in COLD_FRUSTRATION_PATTERNS if p in customer_norm)


def detect_absence(expected_event: str, events, since: datetime, window_minutes: int) -> Signal:
    """Determinístico (timer): o evento esperado ocorreu na janela após 'since'?
    disparou=True quando NÃO ocorreu — a falha silenciosa que ninguém reclama."""
    fim = since + timedelta(minutes=window_minutes)
    ocorreu = any(et == expected_event and since <= _parse_ts(oa) <= fim for et, oa in events)
    return Signal(tipo="ausencia", rotulo=f"esperado e ausente: {expected_event}",
                  deterministico=True, disparou=not ocorreu,
                  valor=expected_event, limiar=float(window_minutes))


def classify_conversation(messages, events, started_at,
                          expected_events: list[tuple[str, int]] | None = None,
                          retriever=None, judge=None) -> ClassifierOutput:
    """Detecta o atrito de UMA conversa, como o sistema veria — devolvendo sinais AUDITÁVEIS.

    messages: (sender, text, sent_at) em ordem · events: (event_type, occurred_at) do mesmo
    user (sem FK) · started_at: início (fato) · expected_events: [(evento, janela_min)] p/
    detecção de AUSÊNCIA · retriever: se passado, tema é SEMÂNTICO (embeddings); senão keyword.
    """
    started = started_at if isinstance(started_at, datetime) else _parse_ts(started_at)
    customer_msgs = [(txt or "") for snd, txt, _ in messages if snd == "customer"]
    customer_norm = _normalize(" \n ".join(customer_msgs))
    last_msg_at = _parse_ts(messages[-1][2]) if messages else started

    evt = find_correlated_event(events, started, last_msg_at)   # sinal determinístico forte

    # ── tema: FUSÃO evento (determinístico) + semântica (texto) ─────────────────
    # 1º o evento correlacionado, se mapeia um tema (evidência forte e auditável);
    # 2º semântica por âncoras (se retriever); 3º keyword (fallback).
    theme_doc = None
    if evt is not None and evt in EVENT_THEME:
        theme, theme_conf, theme_source = EVENT_THEME[evt], 0.97, "evento"
        matched = [evt]
    elif retriever is not None:
        theme, theme_conf, theme_doc = classify_theme_semantic(" ".join(customer_msgs), retriever)
        theme_source, matched = "semantico", []
    else:
        theme, theme_conf, matched = classify_theme(customer_norm)
        theme_source = "keyword"

    # LLM-as-judge no RESÍDUO ambíguo: só quando a regra/semântica tem baixa confiança
    # (evento nunca é ambíguo). Determinístico onde confia, LLM só no difícil → custo sob
    # medida (~o resíduo, não tudo). `judge` é injetado (o motor não acopla OpenAI).
    if judge is not None and theme_source != "evento" and theme_conf < JUDGE_CONF_FLOOR:
        jt = judge(" ".join(customer_msgs))
        if jt is not None:
            theme, theme_conf, theme_source = jt, 0.90, "llm_judge"

    signals: list[Signal] = []
    # ── determinísticos: evento, retentativa, ausência ──────────────────────────
    signals.append(Signal(tipo="evento", rotulo="evento de sistema correlacionado",
                          deterministico=True, disparou=evt is not None, valor=evt))
    retry = _retry_count(customer_msgs)
    signals.append(Signal(tipo="retentativa", rotulo="tentativas quase-idênticas",
                          deterministico=True, disparou=retry >= RETRY_MIN,
                          valor=float(retry), limiar=float(RETRY_MIN)))
    loop_textual = any(p in customer_norm for p in LOOP_PATTERNS)
    signals.append(Signal(tipo="repeticao", rotulo="marcador de repetição ('já tentei', 'de novo')",
                          deterministico=True, disparou=loop_textual))
    absent = None
    for ev, win in (expected_events or []):
        s = detect_absence(ev, events, started, win)
        signals.append(s)
        if s.disparou:
            absent = s

    # ── fuzzy CALIBRADOS (limiar explícito, nunca "vibe") ───────────────────────
    frust = _frustration_score(customer_norm)
    signals.append(Signal(tipo="frustracao", rotulo="frustração (léxico=1, escalada=2)",
                          deterministico=False, disparou=frust >= FRUSTRATION_FLOOR,
                          valor=frust, limiar=FRUSTRATION_FLOOR))
    conf_n = _confusion_count(customer_norm)
    signals.append(Signal(tipo="confusao", rotulo="confusão de usabilidade/canal",
                          deterministico=False, disparou=conf_n >= CONFUSION_FLOOR,
                          valor=float(conf_n), limiar=float(CONFUSION_FLOOR)))
    disap = _disappointment_count(customer_norm)
    signals.append(Signal(tipo="desanimo", rotulo="frustração fria (decepção/desânimo) — calibra o TOM, não o gate",
                          deterministico=False, disparou=disap >= DISAPPOINTMENT_FLOOR,
                          valor=float(disap), limiar=float(DISAPPOINTMENT_FLOOR)))
    safety = any(p in customer_norm for p in VULNERABILITY_PATTERNS)
    signals.append(Signal(tipo="seguranca", rotulo="vulnerabilidade (ludopatia/autoexclusão) — IA se recusa, humano de propósito",
                          deterministico=True, disparou=safety))
    signals.append(Signal(tipo="llm_judge", rotulo="tema ambíguo → resolvido por LLM-as-judge (resíduo)",
                          deterministico=False, disparou=theme_source == "llm_judge"))
    asked_human = any(p in customer_norm for p in HUMAN_REQUEST_PATTERNS)
    signals.append(Signal(tipo="pedido_humano", rotulo="pediu atendente humano",
                          deterministico=True, disparou=asked_human))

    # ── natureza: ausência VIGIADA (timer) > evento (sistema) > comportamento ───
    # Ausência tem precedência QUANDO foi explicitamente vigiada (expected_events) e
    # disparou — é o caso limbo, onde eventos de progresso (kyc.started) são benignos
    # e o atrito real é o que NÃO aconteceu. Sem vigília, cai na lógica evento>comportamento.
    if absent is not None:
        nature, nature_conf = FrictionNature.ABSENCE_DETECTED, NATURE_CONF_ABSENCE
    elif evt is not None:
        nature, nature_conf = FrictionNature.SYSTEM_SIGNALED, NATURE_CONF_WITH_EVENT
    else:
        nature, nature_conf = FrictionNature.BEHAVIOR_INFERRED, NATURE_CONF_NO_EVENT

    in_loop = retry >= RETRY_MIN or loop_textual
    return ClassifierOutput(
        predicted_theme=theme, theme_confidence=theme_conf,
        predicted_nature=nature, nature_confidence=nature_conf,
        asked_for_human=asked_human, frustrated=frust >= FRUSTRATION_FLOOR,
        in_loop=in_loop, confused=conf_n >= CONFUSION_FLOOR,
        disappointed=disap >= DISAPPOINTMENT_FLOOR, safety_concern=safety,
        retry_count=retry, frustration_score=frust,
        theme_source=theme_source, theme_doc=theme_doc,
        matched_keywords=matched, correlated_event=evt, signals=signals,
    )


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
