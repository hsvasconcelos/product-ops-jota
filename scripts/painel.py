"""Painel de terminal (TUI) do Mundo 1 — atendimento como laboratório.

Navegável por teclado, 5 abas, tudo derivado de SQL real sobre
`data/jota_support.db`. Nada de números hardcoded: toda análise é uma query.

Uso:
    python scripts/painel.py            # interativo (precisa de TTY)
    python scripts/painel.py --no-tty   # imprime as 5 abas em sequência e sai

Teclas (modo interativo):
    1-5        vai direto à aba
    ←  →       aba anterior / próxima
    t          (só na aba 5) escolhe o tema → abre o LEITOR de conversas
    q / Ctrl-C sai

    No modo LEITOR (aba 5, uma conversa por vez):
    ←  →       conversa anterior / próxima
    v          volta para a lista de temas
    q          sai

Princípio aplicado (policy vs mechanism): a fórmula do score de priorização
é o MECANISMO; os pesos abaixo (PESOS_SCORE) são CONFIGURAÇÃO — nomeados e
visíveis para serem explicados e ajustados sem tocar no cálculo.
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

from rich.box import ROUNDED, SIMPLE_HEAVY
from rich.console import Console, Group
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

DB = Path(__file__).resolve().parents[1] / "data" / "jota_support.db"
HEADER = "Product Ops · Jota — Atendimento como Laboratório"

# ── Mundo ativo (tecla 'm' alterna) — toda query filtra por canal ────────────
# Mundo 1 = suporte (reativo); Mundo 2 = produto (proativo). O painel é
# consciente de mundo: as métricas de cada um vivem no seu canal, sem misturar.
MUNDO = "support"
MUNDO_LABEL = {"support": "MUNDO 1 · Suporte (reativo)", "jota": "MUNDO 2 · Produto (proativo)"}


def _ch() -> str:
    """Fragmento SQL do filtro de canal do mundo ativo (channel é valor controlado)."""
    return f"channel = '{MUNDO}'"

# ── Policy: pesos e limiares do score de antecipação (aba 3) ─────────────────
# Mecanismo (a fórmula) vive em prioritizacao_dados(); estes são os botões.
SCORE_SCALE = 100.0          # só escala o número pra leitura (não muda ordem)
PESO_VOLUME = 1.0            # expoente do volume normalizado (0–1)
PESO_CRITICIDADE = 1.0       # expoente da criticidade normalizada (0–1)
PESO_TENDENCIA = 1.0         # expoente do fator de tendência
CRIT_MAX = 5.0              # criticality é 1–5 no schema → normaliza p/ 0–1

# Proteção contra ruído: tema com pouco volume total NÃO recebe efeito de
# tendência (uma semana fraca não pode disparar/derrubar a prioridade).
MIN_VOLUME_TENDENCIA = 15    # abaixo disso, trend_factor é neutro (1.0)
TREND_PISO = 0.5            # clamp inferior do fator de tendência
TREND_TETO = 2.0            # clamp superior do fator de tendência

# Temas mapeados (rótulo legível por chave do schema)
TEMA_LABEL = {
    "pix": "Pix",
    "account_access": "Acesso à conta",
    "fala_tap": "Fala Tap",
    "kyc": "KYC / onboarding",
    "boleto": "Boleto",
    "account_data": "Dados cadastrais",
    "yield_open_finance": "Rendimento / Open Finance",
    "other": "Outros",
}
NATUREZA_LABEL = {
    "behavior_inferred": "behavior_inferred (jornada)",
    "system_signaled": "system_signaled (bug/produto)",
    "absence_detected": "absence_detected (silencioso)",
}

console = Console()


# ─────────────────────────────────────────────────────────────────────────────
# Camada de dados — uma função por análise, todas em SQL real.
# Helpers defensivos garantem que nada quebra por divisão por zero.
# ─────────────────────────────────────────────────────────────────────────────
def safe_div(a: float, b: float, default: float = 0.0) -> float:
    """Divisão que nunca explode: denominador zero → default."""
    return a / b if b else default


def bar(value: float, maximo: float, width: int = 26, char: str = "█") -> str:
    """Barra de texto proporcional ao maior valor da série (à prova de zero)."""
    if maximo <= 0:
        return ""
    return char * max(0, round(width * value / maximo))


def connect() -> sqlite3.Connection:
    return sqlite3.connect(f"file:{DB}?mode=ro", uri=True)


def overview_dados(conn: sqlite3.Connection) -> dict:
    total = conn.execute(f"SELECT COUNT(*) FROM conversations WHERE {_ch()}").fetchone()[0]
    outcomes = dict(
        conn.execute(f"SELECT outcome, COUNT(*) FROM conversations WHERE {_ch()} GROUP BY outcome").fetchall()
    )
    resolvidos = outcomes.get("resolved", 0)
    volumetria = conn.execute(
        f"SELECT gold_theme, COUNT(*) n FROM conversations WHERE {_ch()} GROUP BY gold_theme ORDER BY n DESC"
    ).fetchall()
    naturezas = conn.execute(
        f"SELECT gold_nature, COUNT(*) n FROM conversations WHERE {_ch()} GROUP BY gold_nature ORDER BY n DESC"
    ).fetchall()
    return {
        "total": total,
        "resolvidos": resolvidos,
        "taxa_resolucao": safe_div(resolvidos, total) * 100,
        "outcomes": outcomes,
        "volumetria": volumetria,
        "naturezas": naturezas,
    }


def _semanas_por_tema(conn: sqlite3.Connection):
    """Devolve (semanas_ordenadas, {tema: {semana: n}}, {tema: total})."""
    rows = conn.execute(
        f"""SELECT strftime('%Y-W%W', started_at) wk, gold_theme, COUNT(*) n
           FROM conversations WHERE {_ch()} GROUP BY wk, gold_theme"""
    ).fetchall()
    semanas = sorted({wk for wk, _, _ in rows})
    por_tema: dict[str, dict[str, int]] = {}
    totais: dict[str, int] = {}
    for wk, theme, n in rows:
        por_tema.setdefault(theme, {})[wk] = n
        totais[theme] = totais.get(theme, 0) + n
    return semanas, por_tema, totais


def _tendencia(ultima: float, anteriores: list[int]) -> tuple[float, float]:
    """(ultima_semana, media_anteriores) — média à prova de lista vazia."""
    media_prev = safe_div(sum(anteriores), len(anteriores)) if anteriores else 0.0
    return ultima, media_prev


def trend_factor(total_tema: int, ultima: float, media_prev: float) -> float:
    """Fator de tendência protegido contra ruído e divisão por zero.

    - tema com volume total < MIN_VOLUME_TENDENCIA → neutro (1.0)
    - sem semanas anteriores ou média zero → neutro (1.0)
    - caso normal → ultima/media_prev, clampado em [TREND_PISO, TREND_TETO]
    """
    if total_tema < MIN_VOLUME_TENDENCIA:
        return 1.0
    if media_prev <= 0:
        return 1.0
    return max(TREND_PISO, min(TREND_TETO, ultima / media_prev))


def direcao(ultima: float, media_prev: float, total_tema: int) -> Text:
    """Seta colorida de direção (neutra se volume insuficiente)."""
    if total_tema < MIN_VOLUME_TENDENCIA or media_prev <= 0:
        return Text("=", style="dim")
    if ultima > media_prev:
        return Text("↑", style="bold red")
    if ultima < media_prev:
        return Text("↓", style="bold green")
    return Text("=", style="dim")


def trend_dados(conn: sqlite3.Connection) -> dict:
    semanas, por_tema, totais = _semanas_por_tema(conn)
    ultima_semana = semanas[-1] if semanas else None
    temas = sorted(totais, key=lambda t: totais[t], reverse=True)
    resumo = {}
    for t in temas:
        counts = por_tema.get(t, {})
        ult = counts.get(ultima_semana, 0) if ultima_semana else 0
        anteriores = [counts.get(wk, 0) for wk in semanas[:-1]]
        ult_val, media_prev = _tendencia(ult, anteriores)
        resumo[t] = {
            "ultima": ult_val,
            "media_prev": media_prev,
            "direcao": direcao(ult_val, media_prev, totais[t]),
            "total": totais[t],
        }
    return {
        "semanas": semanas,
        "ultima_semana": ultima_semana,
        "por_tema": por_tema,
        "temas": temas,
        "resumo": resumo,
    }


def natureza_dominante(conn: sqlite3.Connection) -> dict[str, tuple[str, float]]:
    """Por tema: (natureza predominante, % que ela representa). À prova de zero."""
    rows = conn.execute(
        f"SELECT gold_theme, gold_nature, COUNT(*) n FROM conversations WHERE {_ch()} GROUP BY gold_theme, gold_nature"
    ).fetchall()
    agg: dict[str, dict[str, int]] = {}
    for theme, nat, n in rows:
        agg.setdefault(theme, {})[nat] = n
    out = {}
    for theme, dist in agg.items():
        total = sum(dist.values())
        nat, n = max(dist.items(), key=lambda kv: kv[1])
        out[theme] = (nat, safe_div(n, total) * 100)
    return out


def prioritizacao_dados(conn: sqlite3.Connection) -> list[dict]:
    """Score de antecipação por tema — o coração da aba 3.

    MECANISMO (fórmula), com os PESOS vindos das constantes do topo:

        volume_norm  = n_tema / n_do_tema_líder              (0–1)
        crit_norm    = AVG(gold_criticality) / CRIT_MAX      (0–1)
        trend        = trend_factor(...) protegido            (~0.5–2.0)
        score = SCORE_SCALE
                * volume_norm ** PESO_VOLUME
                * crit_norm   ** PESO_CRITICIDADE
                * trend       ** PESO_TENDENCIA
    """
    vols = dict(conn.execute(f"SELECT gold_theme, COUNT(*) FROM conversations WHERE {_ch()} GROUP BY gold_theme").fetchall())
    crits = dict(
        conn.execute(f"SELECT gold_theme, AVG(gold_criticality) FROM conversations WHERE {_ch()} GROUP BY gold_theme").fetchall()
    )
    nat_dom = natureza_dominante(conn)
    trend = trend_dados(conn)
    n_lider = max(vols.values()) if vols else 0

    linhas = []
    for theme, n in vols.items():
        crit_media = crits.get(theme, 0.0) or 0.0
        r = trend["resumo"].get(theme, {})
        tf = trend_factor(n, r.get("ultima", 0), r.get("media_prev", 0))
        volume_norm = safe_div(n, n_lider)
        crit_norm = safe_div(crit_media, CRIT_MAX)
        score = (
            SCORE_SCALE
            * (volume_norm ** PESO_VOLUME)
            * (crit_norm ** PESO_CRITICIDADE)
            * (tf ** PESO_TENDENCIA)
        )
        nat, nat_pct = nat_dom.get(theme, ("—", 0.0))
        linhas.append(
            {
                "theme": theme,
                "volume": n,
                "crit_media": crit_media,
                "trend_factor": tf,
                "direcao": r.get("direcao", Text("=", style="dim")),
                "natureza": nat,
                "natureza_pct": nat_pct,
                "score": score,
            }
        )
    linhas.sort(key=lambda d: d["score"], reverse=True)
    return linhas


def sla_dados(conn: sqlite3.Connection) -> dict:
    # 1ª resposta humana após mensagem do cliente — window function LAG.
    # (mesma query do scripts/painel_queries.py, fonte de verdade do SLA)
    inscope = f"conversation_id IN (SELECT conversation_id FROM conversations WHERE {_ch()})"
    row = conn.execute(
        f"""WITH g AS (
               SELECT conversation_id, sender, sent_at,
                      LAG(sent_at) OVER (PARTITION BY conversation_id ORDER BY turn_index) prev,
                      LAG(sender)  OVER (PARTITION BY conversation_id ORDER BY turn_index) ps
               FROM messages WHERE {inscope})
           SELECT ROUND(AVG((strftime('%s',sent_at)-strftime('%s',prev))/3600.0),1),
                  ROUND(MAX((strftime('%s',sent_at)-strftime('%s',prev))/3600.0),1)
           FROM g WHERE ps='customer' AND sender='human_agent'"""
    ).fetchone()
    sla_medio, sla_pior = (row[0], row[1]) if row else (None, None)

    pediu, atendido = conn.execute(
        f"SELECT COALESCE(SUM(asked_for_human),0), COALESCE(SUM(human_handoff_done),0) FROM conversations WHERE {_ch()}"
    ).fetchone()
    ignorados = (pediu or 0) - (atendido or 0)

    contexto_perdido = conn.execute(
        f"SELECT COALESCE(SUM(context_lost),0) FROM conversations WHERE {_ch()}"
    ).fetchone()[0]

    trocas = conn.execute(
        f"""SELECT COUNT(*) FROM (
               SELECT conversation_id FROM messages
               WHERE agent_name IS NOT NULL AND {inscope}
               GROUP BY conversation_id HAVING COUNT(DISTINCT agent_name) > 1)"""
    ).fetchone()[0]

    return {
        "sla_medio": sla_medio,
        "sla_pior": sla_pior,
        "pediu_humano": pediu or 0,
        "handoff_feito": atendido or 0,
        "ignorados": ignorados,
        "contexto_perdido": contexto_perdido,
        "trocas_atendente": trocas,
    }


def temas_ordenados(conn: sqlite3.Connection) -> list[tuple[str, int]]:
    return conn.execute(
        f"SELECT gold_theme, COUNT(*) n FROM conversations WHERE {_ch()} GROUP BY gold_theme ORDER BY n DESC"
    ).fetchall()


# Conversa REAL âncora (caso do Hugo) — sempre encabeça o tema dela.
ANCHOR_ID = "c_real_001"
ANCHOR_THEME = "account_access"

# Colunas do cabeçalho da conversa lidas em conversas_do_tema (ordem importa).
_CONV_COLS = (
    "conversation_id, gold_theme, gold_nature, gold_criticality, outcome, "
    "asked_for_human, human_handoff_done, context_lost, "
    "sentiment_start, sentiment_end"
)


def _carrega_conversa(conn: sqlite3.Connection, cid: str) -> dict | None:
    """Cabeçalho + todas as mensagens (em ordem de turn_index) de UMA conversa."""
    row = conn.execute(
        f"SELECT {_CONV_COLS} FROM conversations WHERE conversation_id=?", (cid,)
    ).fetchone()
    if row is None:
        return None
    msgs = conn.execute(
        """SELECT turn_index, sender, agent_name, sent_at, text
           FROM messages WHERE conversation_id=? ORDER BY turn_index""",
        (cid,),
    ).fetchall()
    keys = (
        "id", "theme", "friction_nature", "criticality", "outcome",
        "asked_for_human", "human_handoff_done", "context_lost",
        "sentiment_start", "sentiment_end",
    )
    conv = dict(zip(keys, row))
    conv["mensagens"] = msgs
    conv["caso_real"] = cid == ANCHOR_ID
    return conv


def conversas_do_tema(conn: sqlite3.Connection, theme: str, limite: int = 10) -> list[dict]:
    """Até `limite` conversas do tema (cabeçalho + mensagens), maior criticidade
    primeiro. Quando o tema é o da conversa âncora (caso real do Hugo), ela é
    forçada para a 1ª posição, marcada com selo [CASO REAL]."""
    ids = [
        r[0]
        for r in conn.execute(
            f"""SELECT conversation_id FROM conversations
               WHERE gold_theme=? AND {_ch()} ORDER BY gold_criticality DESC, conversation_id LIMIT ?""",
            (theme, limite),
        ).fetchall()
    ]
    # Garante a âncora como 1ª (só no Mundo 1, onde ela vive).
    if theme == ANCHOR_THEME and MUNDO == "support":
        ids = [ANCHOR_ID] + [i for i in ids if i != ANCHOR_ID]
        ids = ids[:limite]

    return [c for cid in ids if (c := _carrega_conversa(conn, cid))]


# ─────────────────────────────────────────────────────────────────────────────
# Camada de render — cada função devolve um renderable do rich.
# ─────────────────────────────────────────────────────────────────────────────
def render_overview(conn) -> Panel:
    d = overview_dados(conn)

    topo = Text()
    topo.append(f"{d['total']} ", style="bold cyan")
    topo.append("conversas no laboratório   ·   ")
    topo.append(f"{d['taxa_resolucao']:.1f}% ", style="bold green")
    topo.append(f"resolvidas ({d['resolvidos']}/{d['total']})")

    out_line = "   ".join(f"{k}: {v}" for k, v in d["outcomes"].items())

    vol = Table(box=SIMPLE_HEAVY, expand=True, title="Volumetria por tema (o que priorizar)")
    vol.add_column("Tema", style="white")
    vol.add_column("N", justify="right", style="bold")
    vol.add_column("", ratio=1)
    maxn = d["volumetria"][0][1] if d["volumetria"] else 0
    for theme, n in d["volumetria"]:
        vol.add_row(TEMA_LABEL.get(theme, theme), str(n), Text(bar(n, maxn), style="cyan"))

    nat_titulo = ("Natureza do atrito (quem está aqui FALOU)" if MUNDO == "support"
                  else "Natureza do atrito (inclui o silencioso — ausência)")
    nat = Table(box=SIMPLE_HEAVY, expand=True, title=nat_titulo)
    nat.add_column("Natureza")
    nat.add_column("N", justify="right", style="bold")
    nat.add_column("%", justify="right")
    nat.add_column("", ratio=1)
    tot_nat = sum(n for _, n in d["naturezas"])
    maxnat = d["naturezas"][0][1] if d["naturezas"] else 0
    for nature, n in d["naturezas"]:
        nat.add_row(
            NATUREZA_LABEL.get(nature, nature),
            str(n),
            f"{safe_div(n, tot_nat) * 100:.0f}%",
            Text(bar(n, maxnat), style="magenta"),
        )

    return Panel(Group(topo, Text(f"  outcomes → {out_line}", style="dim"), "", vol, "", nat),
                 title="1 · VISÃO GERAL", border_style="cyan", box=ROUNDED)


def render_trend(conn) -> Panel:
    d = trend_dados(conn)
    if not d["semanas"]:
        return Panel("Sem dados de semana.", title="2 · TENDÊNCIA", border_style="cyan")

    # Mostra as últimas semanas (linhas) x temas (colunas), p/ caber na tela.
    semanas_view = d["semanas"][-8:]
    temas = d["temas"]

    t = Table(box=SIMPLE_HEAVY, expand=True,
              title=f"Conversas por semana × tema (últimas {len(semanas_view)} semanas)")
    t.add_column("Semana", style="bold")
    for theme in temas:
        t.add_column(TEMA_LABEL.get(theme, theme)[:10], justify="right")
    for wk in semanas_view:
        row = [wk]
        for theme in temas:
            row.append(str(d["por_tema"].get(theme, {}).get(wk, 0)))
        t.add_row(*row)

    # Linha-resumo de direção (última semana vs média das anteriores).
    resumo = Table(box=SIMPLE_HEAVY, expand=True,
                   title="Direção (última semana vs média das anteriores)")
    resumo.add_column("Tema")
    resumo.add_column("Última", justify="right")
    resumo.add_column("Média ant.", justify="right")
    resumo.add_column("Dir", justify="center")
    for theme in temas:
        r = d["resumo"][theme]
        resumo.add_row(
            TEMA_LABEL.get(theme, theme),
            f"{r['ultima']:.0f}",
            f"{r['media_prev']:.1f}",
            r["direcao"],
        )

    legenda = Text("  ↑ em alta (vermelho)   ↓ em queda (verde)   = estável/volume baixo", style="dim")
    return Panel(Group(t, "", resumo, legenda),
                 title="2 · TENDÊNCIA", border_style="cyan", box=ROUNDED)


def render_prioritizacao(conn) -> Panel:
    linhas = prioritizacao_dados(conn)

    # Frase de recomendação (topo) — o "o que antecipar primeiro".
    if linhas:
        top = linhas[0]
        seta = top["direcao"].plain
        alta = "em alta ↑" if seta == "↑" else ("em queda ↓" if seta == "↓" else "estável")
        rec = Text()
        rec.append("Priorize: ", style="bold yellow")
        rec.append(TEMA_LABEL.get(top["theme"], top["theme"]), style="bold white")
        rec.append(f" — {top['volume']} conversas, criticidade {top['crit_media']:.1f}, {alta}.  ")
        acao = ("correção de JORNADA/comunicação"
                if top["natureza"] == "behavior_inferred"
                else "correção de BUG/produto")
        rec.append(f"Natureza dominante: {top['natureza']} → {acao}.", style="italic")
    else:
        rec = Text("Sem dados.")

    pesos = Text(
        f"  fórmula: score = {SCORE_SCALE:.0f} · volume_norm^{PESO_VOLUME} · "
        f"crit_norm^{PESO_CRITICIDADE} · trend^{PESO_TENDENCIA}   "
        f"(trend neutro se volume < {MIN_VOLUME_TENDENCIA})",
        style="dim",
    )

    t = Table(box=SIMPLE_HEAVY, expand=True, title="Score de antecipação por tema")
    t.add_column("#", justify="right", style="dim")
    t.add_column("Tema", style="white")
    t.add_column("Score", justify="right", style="bold yellow")
    t.add_column("Volume", justify="right")
    t.add_column("Crit. média", justify="right")
    t.add_column("Tend.", justify="center")
    t.add_column("Natureza dominante → ação")
    maxscore = linhas[0]["score"] if linhas else 0
    for i, r in enumerate(linhas, 1):
        acao = "jornada" if r["natureza"] == "behavior_inferred" else "bug/produto"
        nat_cell = Text()
        nat_cell.append(f"{r['natureza']} ", style="bold")
        nat_cell.append(f"{r['natureza_pct']:.0f}% → {acao}", style="dim")
        score_cell = Text(f"{r['score']:.1f} ")
        score_cell.append(bar(r["score"], maxscore, width=10), style="yellow")
        t.add_row(
            str(i),
            TEMA_LABEL.get(r["theme"], r["theme"]),
            score_cell,
            str(r["volume"]),
            f"{r['crit_media']:.1f}",
            r["direcao"],
            nat_cell,
        )

    return Panel(Group(Panel(rec, border_style="yellow", box=ROUNDED), pesos, "", t),
                 title="3 · PRIORIZAÇÃO", border_style="cyan", box=ROUNDED)


def render_sla(conn) -> Panel:
    d = sla_dados(conn)
    medio = f"{d['sla_medio']}h" if d["sla_medio"] is not None else "—"
    pior = f"{d['sla_pior']}h" if d["sla_pior"] is not None else "—"

    t = Table(box=SIMPLE_HEAVY, expand=True, title="SLA de 1ª resposta & qualidade do atendimento")
    t.add_column("Métrica", style="white")
    t.add_column("Valor", justify="right", style="bold")
    t.add_column("Leitura", style="dim")
    t.add_row("SLA 1ª resposta — médio", medio, "derivado por window function (LAG), não armazenado")
    t.add_row("SLA 1ª resposta — pior", pior, "baseline real do Jota ~33h (ReclameAqui)")
    t.add_row("Pediu humano", str(d["pediu_humano"]), "")
    t.add_row("Handoff feito", str(d["handoff_feito"]), "")
    t.add_row(
        "Pedidos de humano IGNORADOS",
        str(d["ignorados"]),
        "pediu humano e não houve handoff",
    )
    t.add_row("Conversas com contexto perdido", str(d["contexto_perdido"]), "cliente repetiu info já dada")
    t.add_row("Conversas com troca de atendente", str(d["trocas_atendente"]), ">1 agente na mesma conversa")

    destaque = Text()
    if d["ignorados"] > 0:
        destaque.append(f"⚠ {d['ignorados']} pedidos de humano ignorados", style="bold red")
        destaque.append(" — atrito de confiança direto.", style="dim")
    return Panel(Group(t, "", destaque), title="4 · SLA & QUALIDADE",
                 border_style="cyan", box=ROUNDED)


SENDER_STYLE = {"customer": "bold white", "bot": "cyan", "human_agent": "green"}


def _hora(sent_at: str) -> str:
    """'2026-06-24T09:44:04' → '09:44' (defensivo se vier vazio/curto)."""
    return sent_at[11:16] if sent_at and len(sent_at) >= 16 else (sent_at or "")


def _sim_nao(v) -> Text:
    return Text("sim", style="bold red") if v else Text("não", style="green")


def _sinais_conversa(c: dict) -> Text:
    """Linha de sinais de qualidade do cabeçalho da conversa."""
    t = Text()
    t.append("asked_for_human: "); t.append_text(_sim_nao(c["asked_for_human"])); t.append("   ")
    t.append("handoff_done: "); t.append_text(_sim_nao(c["human_handoff_done"])); t.append("   ")
    t.append("context_lost: "); t.append_text(_sim_nao(c["context_lost"])); t.append("\n")
    s0, s1 = c["sentiment_start"], c["sentiment_end"]
    rumo = "↑" if (s1 is not None and s0 is not None and s1 > s0) else (
        "↓" if (s1 is not None and s0 is not None and s1 < s0) else "=")
    estilo = "green" if rumo == "↑" else ("red" if rumo == "↓" else "dim")
    t.append("sentiment: ")
    t.append(f"{s0:+.2f} → {s1:+.2f} {rumo}", style=estilo)
    return t


def render_conversa(c: dict, posicao: int | None = None, total: int | None = None) -> Panel:
    """UMA conversa ocupando a tela: cabeçalho + sinais + mensagens em ordem."""
    blocos = []
    if posicao is not None and total is not None:
        blocos.append(Text(f"  conversa {posicao} de {total}", style="bold cyan"))

    cab = Text()
    cab.append(c["id"], style="bold white")
    if c.get("caso_real"):
        cab.append("  [CASO REAL]", style="bold black on yellow")
    cab.append("\n")
    cab.append(f"tema: {TEMA_LABEL.get(c['theme'], c['theme'])}   ", style="dim")
    cab.append(f"natureza: {c['friction_nature']}   ", style="dim")
    cab.append(f"criticidade: {c['criticality']:.1f}   ", style="dim")
    cab.append(f"outcome: {c['outcome']}", style="dim")
    blocos.append(cab)
    blocos.append(_sinais_conversa(c))

    corpo = Text()
    for turn, sender, agente, sent_at, txt in c["mensagens"]:
        rotulo = f"{sender} ({agente})" if sender == "human_agent" and agente else sender
        corpo.append(f"  {_hora(sent_at)}  ", style="dim")
        corpo.append(f"{rotulo}: ", style=SENDER_STYLE.get(sender, "white"))
        corpo.append((txt or "") + "\n")

    titulo = f"{c['id']}  ·  criticidade {c['criticality']:.1f}  ·  {c['outcome']}"
    return Panel(Group(*blocos, Text(""), corpo),
                 title=titulo, border_style="yellow" if c.get("caso_real") else "dim",
                 box=ROUNDED)


def render_reader(theme_sel: str, convs: list[dict], idx: int) -> Panel:
    """Modo leitor: a conversa atual ocupando a tela + rodapé de navegação."""
    atual = render_conversa(convs[idx], posicao=idx + 1, total=len(convs))
    rodape = Text("  ← anterior   ·   → próxima   ·   v volta para a lista de temas   ·   q sai",
                  style="dim", justify="center")
    titulo = f"5 · DRILL-DOWN · LEITOR — {TEMA_LABEL.get(theme_sel, theme_sel)}"
    return Panel(Group(atual, rodape), title=titulo, border_style="cyan", box=ROUNDED)


def render_drilldown(conn, theme_sel: str | None) -> Panel:
    """Lista de temas (estado base da aba 5). 't' + número abre o modo leitor."""
    temas = temas_ordenados(conn)

    menu = Table(box=SIMPLE_HEAVY, expand=True, title="Temas (pressione 't' e digite o número)")
    menu.add_column("#", justify="right", style="dim")
    menu.add_column("Tema")
    menu.add_column("N", justify="right", style="bold")
    for i, (theme, n) in enumerate(temas, 1):
        marca = " ◀" if theme == theme_sel else ""
        menu.add_row(str(i), TEMA_LABEL.get(theme, theme) + marca, str(n))

    dica = Text(
        "\n  pressione 't' e o número do tema para abrir o LEITOR de conversas "
        "(uma por vez, ← → navega, v volta).",
        style="dim",
    )
    return Panel(Group(menu, dica), title="5 · DRILL-DOWN", border_style="cyan", box=ROUNDED)


# Registro das abas: (rótulo curto, função de render)
ABAS = [
    ("VISÃO GERAL", render_overview),
    ("TENDÊNCIA", render_trend),
    ("PRIORIZAÇÃO", render_prioritizacao),
    ("SLA & QUALIDADE", render_sla),
    ("DRILL-DOWN", render_drilldown),
]


def render_header(ativa: int) -> Panel:
    barra = Text()
    for i, (label, _) in enumerate(ABAS):
        estilo = "bold black on cyan" if i == ativa else "cyan"
        barra.append(f" {i + 1} {label} ", style=estilo)
        barra.append("  ")
    titulo = Text(justify="center")
    titulo.append(HEADER + "   ", style="bold white")
    titulo.append(f"[ {MUNDO_LABEL[MUNDO]} ]", style="bold black on green")
    return Panel(titulo, subtitle=barra, border_style="bright_blue", box=ROUNDED)


def render_footer() -> Text:
    return Text("  1-5 abas   ·   ←/→ navega   ·   m alterna Mundo 1/2   ·   t escolhe tema (aba 5)   ·   q sai",
                style="dim", justify="center")


# ─────────────────────────────────────────────────────────────────────────────
# Teclado: leitura de uma tecla em cbreak via stdlib (sem dependência extra).
# ─────────────────────────────────────────────────────────────────────────────
def ler_tecla() -> str:
    """Lê uma tecla sem Enter. Traduz setas (escape sequences) em 'LEFT'/'RIGHT'."""
    import termios
    import tty

    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setcbreak(fd)
        ch = sys.stdin.read(1)
        if ch == "\x1b":  # possível seta: \x1b[C (dir) / \x1b[D (esq)
            seq = sys.stdin.read(2)
            return {"[C": "RIGHT", "[D": "LEFT", "[A": "UP", "[B": "DOWN"}.get(seq, "ESC")
        return ch
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)


def pedir_tema(conn) -> str | None:
    """Modo canônico temporário: lê o número do tema do drill-down."""
    temas = temas_ordenados(conn)
    try:
        console.print("\n  número do tema (Enter cancela): ", end="")
        raw = input().strip()
    except (EOFError, KeyboardInterrupt):
        return None
    if not raw.isdigit():
        return None
    idx = int(raw) - 1
    return temas[idx][0] if 0 <= idx < len(temas) else None


def loop_interativo(conn) -> None:
    global MUNDO
    ativa = 0
    theme_sel: str | None = None
    # Estado do modo leitor (modal, só vive dentro da aba 5).
    reader = False
    convs: list[dict] = []
    conv_idx = 0
    while True:
        console.clear()
        console.print(render_header(ativa))
        render_fn = ABAS[ativa][1]
        if render_fn is render_drilldown and reader and convs:
            console.print(render_reader(theme_sel, convs, conv_idx))
        elif render_fn is render_drilldown:
            console.print(render_fn(conn, theme_sel))
            console.print(render_footer())
        else:
            console.print(render_fn(conn))
            console.print(render_footer())

        tecla = ler_tecla()

        # ── Modo leitor: modal. Só ← → v q agem; o resto é ignorado. ──────────
        if reader:
            if tecla in ("q", "Q", "\x03"):
                break
            elif tecla == "RIGHT":
                conv_idx = (conv_idx + 1) % len(convs)
            elif tecla == "LEFT":
                conv_idx = (conv_idx - 1) % len(convs)
            elif tecla in ("v", "V"):
                reader = False  # volta para a lista de temas (theme_sel fica marcado)
            continue

        # ── Lista / demais abas: navegação normal. ───────────────────────────
        if tecla in ("q", "Q", "\x03"):  # q ou Ctrl-C
            break
        elif tecla in ("RIGHT", "DOWN"):
            ativa = (ativa + 1) % len(ABAS)
        elif tecla in ("LEFT", "UP"):
            ativa = (ativa - 1) % len(ABAS)
        elif tecla in "12345":
            ativa = int(tecla) - 1
        elif tecla in ("m", "M"):                       # alterna Mundo 1 ↔ Mundo 2
            MUNDO = "jota" if MUNDO == "support" else "support"
            theme_sel, reader, convs = None, False, []
        elif tecla in ("t", "T") and ABAS[ativa][1] is render_drilldown:
            sel = pedir_tema(conn)
            if sel:
                theme_sel = sel
                convs = conversas_do_tema(conn, sel)  # até 10, âncora primeiro
                if convs:
                    conv_idx = 0
                    reader = True  # entra no modo leitor
    console.clear()
    console.print("[dim]até a próxima — Mundo 1 segue alimentando o Mundo 2.[/dim]")


def modo_nao_interativo(conn) -> None:
    """Fallback p/ stdin sem TTY (pipe/CI): visão geral dos DOIS mundos + abas do Mundo 1."""
    global MUNDO
    for mundo in ("support", "jota"):                    # visão geral de cada mundo
        MUNDO = mundo
        console.print(render_header(0))
        console.print(render_overview(conn))
    MUNDO = "support"                                    # o restante (lab) no Mundo 1
    console.print(render_trend(conn))
    console.print(render_prioritizacao(conn))
    console.print(render_sla(conn))
    # Aba 5 sem teclado: mostra a lista de temas e, em seguida, as 10 conversas
    # do tema #1 (maior volume) em sequência — o leitor "desenrolado".
    temas = temas_ordenados(conn)
    theme_sel = temas[0][0] if temas else None
    console.print(render_drilldown(conn, theme_sel))
    if theme_sel:
        convs = conversas_do_tema(conn, theme_sel)
        console.print(Text(
            f"\n  LEITOR (não-interativo) — {TEMA_LABEL.get(theme_sel, theme_sel)}: "
            f"{len(convs)} conversas em sequência",
            style="bold yellow"))
        for i, c in enumerate(convs, 1):
            console.print(render_conversa(c, posicao=i, total=len(convs)))


def main() -> None:
    if not DB.exists():
        console.print(
            Panel(
                "banco não encontrado.\n\nRode primeiro:  [bold]python scripts/build_db.py[/bold]",
                title="erro", border_style="red", box=ROUNDED,
            )
        )
        sys.exit(1)

    conn = connect()
    try:
        forcar_nao_tty = "--no-tty" in sys.argv
        if forcar_nao_tty or not sys.stdin.isatty() or not sys.stdout.isatty():
            modo_nao_interativo(conn)
        else:
            loop_interativo(conn)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
