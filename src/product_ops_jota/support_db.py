"""
Jota · Support Lab — Database (Mundo 1)
==========================================
O banco do "atendimento como laboratório": as conversas do número de SUPORTE,
modeladas pra que o painel as analise, meça e priorize o que antecipar no produto.

Modelo relacional normalizado (4 tabelas):

    users (1) ──< (N) conversations (1) ──< (N) messages
      └──────────────< (N) events >── (0..1) conversations

Princípios de modelagem aplicados:
  · normalização — o usuário existe UMA vez; conversas e mensagens referenciam.
  · store facts, derive views — guardamos *_at e rótulos; SLA/duração/agent_switches
    são DERIVADOS por SQL sobre `messages` (não persistidos).
  · as 3 naturezas EMERGEM da correlação entre tabelas:
        SYSTEM_SIGNALED  = conversa COM evento de falha correlacionado
        BEHAVIOR_INFERRED= conversa SEM evento de falha
        ABSENCE_DETECTED = evento esperado SEM conversa nem evento de conclusão
  · CHECK constraints = os enums do domínio, impostos pelo banco (fail-fast no insert).
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA_SQL = """
PRAGMA foreign_keys = ON;

-- ─── Quem é o cliente (existe uma vez) ──────────────────────────────────────
CREATE TABLE users (
    user_id            TEXT PRIMARY KEY,
    segment            TEXT NOT NULL CHECK (segment IN ('pf','pj','mei')),
    signup_at          TEXT NOT NULL,                      -- ISO8601 (fato)
    age_band           TEXT CHECK (age_band IN ('18-25','26-40','41-60','60+')),
    digital_literacy   TEXT CHECK (digital_literacy IN ('low','medium','high'))
);

-- ─── A conversa de suporte (a unidade de análise do painel) ─────────────────
CREATE TABLE conversations (
    conversation_id    TEXT PRIMARY KEY,
    user_id            TEXT NOT NULL REFERENCES users(user_id),
    channel            TEXT NOT NULL CHECK (channel IN ('support','jota')),
    started_at         TEXT NOT NULL,                      -- fato; duração é derivada
    -- GABARITO (gold): verdade gerada de cima pra baixo. O prefixo gold_ grita
    -- "isto é gabarito, não leia pra classificar". O classificador (classifier.py)
    -- deriva os rótulos só do que existe no mundo real (texto + eventos); o eval
    -- compara o previsto contra estas colunas. ─────────────────────────────────
    gold_theme         TEXT NOT NULL CHECK (gold_theme IN
                         ('account_access','pix','kyc','fala_tap','boleto',
                          'account_data','yield_open_finance','other')),
    gold_nature        TEXT NOT NULL CHECK (gold_nature IN
                         ('system_signaled','behavior_inferred','absence_detected')),
    gold_criticality   REAL NOT NULL CHECK (gold_criticality BETWEEN 1 AND 5),
    outcome            TEXT NOT NULL CHECK (outcome IN
                         ('resolved','abandoned','escalated','no_response')),
    -- sinais de qualidade do atendimento (extraídos de conversa real) ─────────
    asked_for_human    INTEGER NOT NULL DEFAULT 0,         -- bool 0/1
    human_handoff_done INTEGER NOT NULL DEFAULT 0,
    context_lost       INTEGER NOT NULL DEFAULT 0,         -- repetiu info já dada
    sentiment_start    REAL CHECK (sentiment_start BETWEEN -1 AND 1),
    sentiment_end      REAL CHECK (sentiment_end BETWEEN -1 AND 1)
);

-- ─── Cada turno da conversa (o grão fino) ───────────────────────────────────
CREATE TABLE messages (
    message_id         TEXT PRIMARY KEY,
    conversation_id    TEXT NOT NULL REFERENCES conversations(conversation_id),
    turn_index         INTEGER NOT NULL,                   -- ordem na conversa
    sender             TEXT NOT NULL CHECK (sender IN ('customer','bot','human_agent')),
    agent_name         TEXT,                               -- p/ derivar agent_switches
    sent_at            TEXT NOT NULL,                       -- p/ derivar SLA
    modality           TEXT NOT NULL DEFAULT 'text' CHECK (modality IN ('text','audio')),
    text               TEXT NOT NULL
);

-- ─── Eventos de sistema (materializam as 3 naturezas por correlação) ────────
CREATE TABLE events (
    event_id           TEXT PRIMARY KEY,
    user_id            TEXT NOT NULL REFERENCES users(user_id),
    conversation_id    TEXT REFERENCES conversations(conversation_id),  -- NULL = silencioso
    event_type         TEXT NOT NULL,                       -- noun.past_verb
    occurred_at        TEXT NOT NULL
);

-- índices p/ as queries do painel (correlação por usuário e tempo)
CREATE INDEX idx_conv_user   ON conversations(user_id);
CREATE INDEX idx_conv_theme  ON conversations(gold_theme, started_at);
CREATE INDEX idx_msg_conv    ON messages(conversation_id, turn_index);
CREATE INDEX idx_evt_user    ON events(user_id, occurred_at);
CREATE INDEX idx_evt_type    ON events(event_type, occurred_at);
"""


def init_db(path: str | Path) -> sqlite3.Connection:
    """Cria o banco do zero com o schema. Retorna a conexão."""
    path = Path(path)
    if path.exists():
        path.unlink()
    conn = sqlite3.connect(path)
    conn.executescript(SCHEMA_SQL)
    conn.commit()
    return conn


def seed_real_conversation(conn: sqlite3.Connection) -> None:
    """Semeia a conversa REAL (Hugo × Aline/Gustavo) como caso de validação.
    app fecha sozinho, sem evento de sistema → BEHAVIOR_INFERRED;
    pediu humano, houve troca de atendente e perda de contexto."""
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO users VALUES (?,?,?,?,?)",
        ("u_hugo", "pf", "2026-06-20T10:00:00", "26-40", "high"),
    )
    cur.execute(
        """INSERT INTO conversations VALUES
        (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        ("c_real_001", "u_hugo", "support", "2026-06-24T09:43:46",
         "account_access", "behavior_inferred", 3.0, "escalated",
         1, 1, 1, 0.0, -0.8),   # pediu humano, handoff feito, contexto perdido, esfriou
    )
    msgs = [
        ("customer", None,      "2026-06-24T09:43:46", "olá"),
        ("human_agent", "Aline","2026-06-24T09:44:04", "Oi, Hugo! Sou a Aline, do suporte do Jota..."),
        ("customer", None,      "2026-06-24T09:49:21", "aline, nao consigo acessar meu app"),
        ("human_agent", "Aline","2026-06-24T09:49:41", "Quando você tenta acessar, o que acontece?"),
        ("customer", None,      "2026-06-24T09:53:30", "fecha sozinho"),
        ("customer", None,      "2026-06-24T09:53:43", "preciso falar URGENTE c/ ser humano! agora!!"),
        ("human_agent", "Aline","2026-06-24T09:54:10", "Já estou vendo isso e te retorno logo."),
        ("human_agent", "Gustavo","2026-06-24T09:57:31", "Oi, Gustavo, bom dia... sou o Gustavo"),
        ("customer", None,      "2026-06-24T09:59:45", "não sou o gustavo"),
        ("human_agent", "Gustavo","2026-06-24T10:09:29", "Hugo, desculpe a mensagem anterior."),
    ]
    for i, (sender, agent, ts, txt) in enumerate(msgs):
        cur.execute(
            "INSERT INTO messages VALUES (?,?,?,?,?,?,?,?)",
            (f"m_real_{i:03d}", "c_real_001", i, sender, agent, ts, "text", txt),
        )
    conn.commit()
