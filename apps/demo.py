"""Demo do MODELO RELACIONAL — Mundo 1 (Product Ops · Jota).

O painel (scripts/painel.py) analisa conversas. Este demo mostra o OUTRO lado:
que o banco é um modelo relacional normalizado de 4 tabelas, e que a análise só
é possível por causa dele. Em especial, materializa as 3 NATUREZAS do atrito por
CORRELAÇÃO entre tabelas — incluindo a `absence_detected`, o atrito silencioso
que vive na tabela `events` (falha de sistema que nunca virou conversa).

Uso:
    python apps/demo.py            # interativo (precisa de TTY)
    python apps/demo.py --no-tty   # imprime as 4 abas em sequência e sai

Teclas (modo interativo):
    1-4        vai direto à aba
    ←  →       aba anterior / próxima
    u          (só na aba 4) escolhe o cliente da visão 360
    q / Ctrl-C sai
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
HEADER = "Product Ops · Jota — O Modelo Relacional por trás do Laboratório"

# Rótulos legíveis (espelham os do painel, mantidos locais p/ o demo ser standalone)
TEMA_LABEL = {
    "pix": "Pix", "account_access": "Acesso à conta", "fala_tap": "Fala Tap",
    "kyc": "KYC / onboarding", "boleto": "Boleto", "account_data": "Dados cadastrais",
    "yield_open_finance": "Rendimento / Open Finance", "other": "Outros",
}
EVENT_LABEL = {
    "kyc.failed": "KYC falhou", "pix.returned": "Pix devolvido",
    "tap_to_pay.settlement_delayed": "Fala Tap — liquidação atrasada",
    "charge.duplicated": "Boleto duplicado", "session.crashed": "App travou / sessão caiu",
    "open_finance.consent_expired": "Open Finance — consentimento expirou",
    "data_change.requested": "Alteração de dados", "generic.error": "Erro genérico",
}
SEG_LABEL = {"mei": "MEI", "pf": "Pessoa física", "pj": "Pessoa jurídica"}
LIT_LABEL = {"low": "baixa", "medium": "média", "high": "alta"}
NAT_INFO = {
    "system_signaled": ("system_signaled", "o sistema SINALIZOU a falha — bug/produto",
                        "correção de BUG/produto", "yellow"),
    "behavior_inferred": ("behavior_inferred", "nada falhou no sistema — atrito de JORNADA",
                         "correção de jornada/comunicação", "cyan"),
    "absence_detected": ("absence_detected", "falha SEM conversa — o cliente sofreu CALADO",
                        "ANTECIPAR no Mundo 2 (proativo)", "magenta"),
}

console = Console()


def safe_div(a: float, b: float, default: float = 0.0) -> float:
    return a / b if b else default


def bar(value: float, maximo: float, width: int = 22, char: str = "█") -> str:
    if maximo <= 0:
        return ""
    return char * max(0, round(width * value / maximo))


def connect() -> sqlite3.Connection:
    return sqlite3.connect(f"file:{DB}?mode=ro", uri=True)


# ─────────────────────────────────────────────────────────────────────────────
# Camada de dados — uma função por aba, tudo SQL real sobre as 4 tabelas.
# ─────────────────────────────────────────────────────────────────────────────
def contagens(conn) -> dict:
    q = lambda t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
    return {t: q(t) for t in ("users", "conversations", "messages", "events")}


def naturezas_materializadas(conn) -> list[tuple]:
    """As 3 naturezas, cada uma derivada de uma correlação DIFERENTE entre tabelas."""
    ss = conn.execute(
        "SELECT COUNT(*) FROM conversations WHERE friction_nature='system_signaled'"
    ).fetchone()[0]
    bi = conn.execute(
        "SELECT COUNT(*) FROM conversations WHERE friction_nature='behavior_inferred'"
    ).fetchone()[0]
    ad = conn.execute(
        "SELECT COUNT(*) FROM events WHERE conversation_id IS NULL"
    ).fetchone()[0]
    return [("system_signaled", ss, "conversa COM evento de falha correlacionado"),
            ("behavior_inferred", bi, "conversa SEM evento de falha"),
            ("absence_detected", ad, "evento de falha SEM conversa (órfão)")]


def users_dados(conn) -> dict:
    seg = conn.execute("SELECT segment, COUNT(*) n FROM users GROUP BY segment ORDER BY n DESC").fetchall()
    lit = conn.execute(
        "SELECT digital_literacy, COUNT(*) n FROM users GROUP BY digital_literacy ORDER BY n DESC"
    ).fetchall()
    age = conn.execute("SELECT age_band, COUNT(*) n FROM users GROUP BY age_band ORDER BY age_band").fetchall()
    reincidentes = conn.execute(
        "SELECT COUNT(*) FROM (SELECT user_id FROM conversations GROUP BY user_id HAVING COUNT(*)>1)"
    ).fetchone()[0]
    # cruzamento: por literacia, tema dominante + criticidade média (users ⋈ conversations)
    rows = conn.execute(
        """SELECT u.digital_literacy, c.theme, COUNT(*) n
           FROM users u JOIN conversations c ON c.user_id=u.user_id
           GROUP BY u.digital_literacy, c.theme"""
    ).fetchall()
    por_lit: dict[str, dict[str, int]] = {}
    for litv, theme, n in rows:
        por_lit.setdefault(litv, {})[theme] = n
    crit_lit = dict(conn.execute(
        """SELECT u.digital_literacy, ROUND(AVG(c.criticality),2)
           FROM users u JOIN conversations c ON c.user_id=u.user_id
           GROUP BY u.digital_literacy"""
    ).fetchall())
    return {"seg": seg, "lit": lit, "age": age, "reincidentes": reincidentes,
            "por_lit": por_lit, "crit_lit": crit_lit}


def events_dados(conn) -> dict:
    # por tipo: total, correlacionados (têm conversa) e órfãos (silenciosos)
    rows = conn.execute(
        """SELECT event_type,
                  COUNT(*) total,
                  SUM(CASE WHEN conversation_id IS NULL THEN 1 ELSE 0 END) orfaos
           FROM events GROUP BY event_type ORDER BY total DESC"""
    ).fetchall()
    return {"por_tipo": rows, "naturezas": naturezas_materializadas(conn)}


def usuarios_interessantes(conn) -> list[str]:
    """Lista p/ a visão 360: o caso real primeiro, depois quem tem mais histórico."""
    ricos = [r[0] for r in conn.execute(
        """SELECT u.user_id
           FROM users u JOIN conversations c ON c.user_id=u.user_id
           LEFT JOIN events e ON e.user_id=u.user_id
           GROUP BY u.user_id
           HAVING COUNT(DISTINCT c.conversation_id)>=2 AND COUNT(DISTINCT e.event_id)>=1
           ORDER BY COUNT(DISTINCT c.conversation_id) DESC, COUNT(DISTINCT e.event_id) DESC
           LIMIT 8""").fetchall()]
    base = ["u_hugo"] if conn.execute(
        "SELECT 1 FROM users WHERE user_id='u_hugo'").fetchone() else []
    return base + [u for u in ricos if u != "u_hugo"]


def visao_360(conn, uid: str) -> dict:
    perfil = conn.execute(
        "SELECT segment, signup_at, age_band, digital_literacy FROM users WHERE user_id=?", (uid,)
    ).fetchone()
    eventos = conn.execute(
        "SELECT event_type, occurred_at, conversation_id FROM events WHERE user_id=? ORDER BY occurred_at",
        (uid,),
    ).fetchall()
    convs = conn.execute(
        """SELECT conversation_id, started_at, theme, friction_nature, criticality, outcome
           FROM conversations WHERE user_id=? ORDER BY started_at""",
        (uid,),
    ).fetchall()
    # mensagens da conversa mais crítica (o "zoom" do 360)
    msgs = []
    foco = None
    if convs:
        foco = max(convs, key=lambda r: r[4])[0]
        msgs = conn.execute(
            "SELECT sender, agent_name, sent_at, text FROM messages WHERE conversation_id=? ORDER BY turn_index",
            (foco,),
        ).fetchall()
    return {"uid": uid, "perfil": perfil, "eventos": eventos, "convs": convs,
            "foco": foco, "msgs": msgs}


# ─────────────────────────────────────────────────────────────────────────────
# Camada de render — uma função por aba (devolve um renderable do rich).
# ─────────────────────────────────────────────────────────────────────────────
def render_modelo(conn) -> Panel:
    c = contagens(conn)
    diagrama = Text(
        "\n"
        "   users (1) ──< (N) conversations (1) ──< (N) messages\n"
        "     └──────────────< (N) events >── (0..1) conversations\n",
        style="bold cyan",
    )
    tab = Table(box=SIMPLE_HEAVY, expand=True, title="As 4 tabelas (fatos guardados; views são derivadas)")
    tab.add_column("Tabela", style="white")
    tab.add_column("Linhas", justify="right", style="bold")
    tab.add_column("O que guarda", style="dim")
    tab.add_row("users", str(c["users"]), "quem é o cliente (segmento, literacia, idade) — existe 1 vez")
    tab.add_row("conversations", str(c["conversations"]), "a unidade de análise: 1 atendimento rotulado")
    tab.add_row("messages", str(c["messages"]), "o grão fino: cada turno (sender, hora) → SLA é derivado")
    tab.add_row("events", str(c["events"]), "sinais de sistema — materializam as naturezas por correlação")

    princ = Text()
    princ.append("Princípios aplicados\n", style="bold yellow")
    princ.append("  · normalização — o cliente existe uma vez; conversas/eventos referenciam.\n", style="dim")
    princ.append("  · store facts, derive views — guardo *_at e rótulos; SLA/idade/score são SQL puro.\n", style="dim")
    princ.append("  · fail-fast — CHECK constraints são os enums do domínio, impostos no INSERT.\n", style="dim")
    princ.append("  · as 3 naturezas EMERGEM da correlação entre tabelas (veja a aba 3).", style="dim")

    return Panel(Group(diagrama, tab, "", princ),
                 title="1 · MODELO RELACIONAL", border_style="cyan", box=ROUNDED)


def render_users(conn) -> Panel:
    d = users_dados(conn)
    total = sum(n for _, n in d["seg"])

    def dist_table(titulo, rows, label_map):
        t = Table(box=SIMPLE_HEAVY, expand=True, title=titulo)
        t.add_column("Categoria"); t.add_column("N", justify="right", style="bold")
        t.add_column("%", justify="right"); t.add_column("", ratio=1)
        maxn = max((n for _, n in rows), default=0)
        for k, n in rows:
            t.add_row(label_map.get(k, k), str(n), f"{safe_div(n, total)*100:.0f}%",
                      Text(bar(n, maxn), style="cyan"))
        return t

    seg_t = dist_table("Segmento (Jota é forte em empreendedor)", d["seg"], SEG_LABEL)
    lit_t = dist_table("Literacia digital", d["lit"], LIT_LABEL)

    # cruzamento: literacia → tema dominante + criticidade média
    cruz = Table(box=SIMPLE_HEAVY, expand=True,
                 title="Quem reclama de quê — atrito × literacia (users ⋈ conversations)")
    cruz.add_column("Literacia"); cruz.add_column("Tema dominante")
    cruz.add_column("% do tema", justify="right"); cruz.add_column("Crit. média", justify="right")
    for litv in ("low", "medium", "high"):
        dist = d["por_lit"].get(litv, {})
        if not dist:
            continue
        theme, n = max(dist.items(), key=lambda kv: kv[1])
        cruz.add_row(LIT_LABEL.get(litv, litv), TEMA_LABEL.get(theme, theme),
                     f"{safe_div(n, sum(dist.values()))*100:.0f}%",
                     str(d["crit_lit"].get(litv, "—")))

    rema = Text(f"\n  {d['reincidentes']} clientes voltaram mais de uma vez — ",
                style="bold white")
    rema.append("a reincidência é candidata nº1 a antecipação no Mundo 2.", style="dim")

    return Panel(Group(seg_t, "", lit_t, "", cruz, rema),
                 title="2 · USERS · QUEM RECLAMA", border_style="cyan", box=ROUNDED)


def render_events(conn) -> Panel:
    d = events_dados(conn)

    tipos = Table(box=SIMPLE_HEAVY, expand=True,
                  title="Eventos de sistema — correlacionado (virou conversa) vs silencioso (órfão)")
    tipos.add_column("Evento"); tipos.add_column("Total", justify="right", style="bold")
    tipos.add_column("Virou conversa", justify="right"); tipos.add_column("Silencioso", justify="right", style="magenta")
    for etype, total, orfaos in d["por_tipo"]:
        tipos.add_row(EVENT_LABEL.get(etype, etype), str(total),
                      str(total - (orfaos or 0)), str(orfaos or 0))

    total_nat = sum(n for _, n, _ in d["naturezas"])
    nat = Table(box=SIMPLE_HEAVY, expand=True,
                title="As 3 NATUREZAS do atrito — cada uma derivada de uma correlação diferente")
    nat.add_column("Natureza"); nat.add_column("N", justify="right", style="bold")
    nat.add_column("%", justify="right"); nat.add_column("Como emerge", style="dim")
    nat.add_column("Ação")
    maxn = max((n for _, n, _ in d["naturezas"]), default=0)
    for key, n, como in d["naturezas"]:
        _, _, acao, cor = NAT_INFO[key]
        cell = Text(f"{key} ")
        cell.append(bar(n, maxn, width=8), style=cor)
        nat.add_row(cell, str(n), f"{safe_div(n, total_nat)*100:.0f}%", como,
                    Text(acao, style=cor))

    ad = next(n for k, n, _ in d["naturezas"] if k == "absence_detected")
    destaque = Text()
    destaque.append(f"\n  ▸ {ad} falhas de sistema NUNCA viraram conversa", style="bold magenta")
    destaque.append(" — é a matéria escura: o cliente sofreu e não reclamou.\n", style="dim")
    destaque.append("    Containment não se mede só por quem ligou; mede-se por quem ", style="dim")
    destaque.append("não precisou ligar", style="italic magenta")
    destaque.append(". É aqui que o Mundo 2 age.", style="dim")

    return Panel(Group(tipos, "", nat, destaque),
                 title="3 · EVENTS · SINAIS & 3 NATUREZAS", border_style="cyan", box=ROUNDED)


SENDER_STYLE = {"customer": "bold white", "bot": "cyan", "human_agent": "green"}


def render_360(conn, uid: str | None) -> Panel:
    lista = usuarios_interessantes(conn)
    uid = uid or (lista[0] if lista else None)

    menu = Table(box=SIMPLE_HEAVY, expand=True, title="Clientes (pressione 'u' e o número)")
    menu.add_column("#", justify="right", style="dim"); menu.add_column("Cliente")
    menu.add_column("", style="dim")
    for i, u in enumerate(lista, 1):
        marca = " ◀" if u == uid else ""
        selo = "  [CASO REAL]" if u == "u_hugo" else ""
        menu.add_row(str(i), u + marca, selo)

    if not uid:
        return Panel(menu, title="4 · VISÃO 360", border_style="cyan", box=ROUNDED)

    d = visao_360(conn, uid)
    perfil = d["perfil"]
    cab = Text()
    cab.append(f"\n  {uid}", style="bold white")
    if uid == "u_hugo":
        cab.append("  [CASO REAL]", style="bold black on yellow")
    if perfil:
        seg, signup, age, lit = perfil
        cab.append(f"\n  {SEG_LABEL.get(seg, seg)} · literacia {LIT_LABEL.get(lit, lit)} · "
                   f"{age} · cliente desde {signup[:10]}", style="dim")

    # linha do tempo unificada: eventos + conversas (o join que a normalização permite)
    tl = Table(box=SIMPLE_HEAVY, expand=True, title="Linha do tempo — events ⋈ conversations (o join 360)")
    tl.add_column("Quando", style="dim"); tl.add_column("Tipo"); tl.add_column("Detalhe")
    linhas = []
    for etype, occ, conv in d["eventos"]:
        det = EVENT_LABEL.get(etype, etype)
        det += "  (virou conversa)" if conv else "  (silencioso — absence_detected)"
        linhas.append((occ, Text("EVENTO", style="magenta"), Text(det)))
    for cid, started, theme, nature, crit, outcome in d["convs"]:
        det = Text(f"{TEMA_LABEL.get(theme, theme)} · {nature} · crit {crit:.1f} · {outcome}")
        linhas.append((started, Text("CONVERSA", style="green"), det))
    for quando, tipo, det in sorted(linhas, key=lambda r: r[0]):
        tl.add_row(quando[:16].replace("T", " "), tipo, det)

    blocos = [menu, cab, tl]
    if d["msgs"]:
        corpo = Text()
        for sender, agente, sent_at, txt in d["msgs"]:
            rotulo = f"{sender} ({agente})" if sender == "human_agent" and agente else sender
            corpo.append(f"  {sent_at[11:16]}  ", style="dim")
            corpo.append(f"{rotulo}: ", style=SENDER_STYLE.get(sender, "white"))
            corpo.append((txt or "") + "\n")
        blocos.append(Panel(corpo, title=f"zoom na conversa mais crítica — {d['foco']}",
                            border_style="dim", box=ROUNDED))

    return Panel(Group(*blocos), title="4 · VISÃO 360", border_style="cyan", box=ROUNDED)


# Registro das abas
ABAS = [
    ("MODELO RELACIONAL", render_modelo),
    ("USERS", render_users),
    ("EVENTS & NATUREZAS", render_events),
    ("VISÃO 360", render_360),
]


def render_header(ativa: int) -> Panel:
    barra = Text()
    for i, (label, _) in enumerate(ABAS):
        estilo = "bold black on cyan" if i == ativa else "cyan"
        barra.append(f" {i + 1} {label} ", style=estilo)
        barra.append("  ")
    return Panel(Text(HEADER, style="bold white", justify="center"),
                 subtitle=barra, border_style="bright_blue", box=ROUNDED)


def render_footer() -> Text:
    return Text("  1-4 abas   ·   ←/→ navega   ·   u escolhe cliente (aba 4)   ·   q sai",
                style="dim", justify="center")


def ler_tecla() -> str:
    """Lê uma tecla sem Enter; traduz setas em LEFT/RIGHT."""
    import termios
    import tty

    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setcbreak(fd)
        ch = sys.stdin.read(1)
        if ch == "\x1b":
            seq = sys.stdin.read(2)
            return {"[C": "RIGHT", "[D": "LEFT", "[A": "UP", "[B": "DOWN"}.get(seq, "ESC")
        return ch
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)


def pedir_user(conn) -> str | None:
    lista = usuarios_interessantes(conn)
    try:
        console.print("\n  número do cliente (Enter cancela): ", end="")
        raw = input().strip()
    except (EOFError, KeyboardInterrupt):
        return None
    if not raw.isdigit():
        return None
    idx = int(raw) - 1
    return lista[idx] if 0 <= idx < len(lista) else None


def loop_interativo(conn) -> None:
    ativa = 0
    user_sel: str | None = None
    while True:
        console.clear()
        console.print(render_header(ativa))
        render_fn = ABAS[ativa][1]
        if render_fn is render_360:
            console.print(render_fn(conn, user_sel))
        else:
            console.print(render_fn(conn))
        console.print(render_footer())

        tecla = ler_tecla()
        if tecla in ("q", "Q", "\x03"):
            break
        elif tecla in ("RIGHT", "DOWN"):
            ativa = (ativa + 1) % len(ABAS)
        elif tecla in ("LEFT", "UP"):
            ativa = (ativa - 1) % len(ABAS)
        elif tecla in "1234":
            ativa = int(tecla) - 1
        elif tecla in ("u", "U") and ABAS[ativa][1] is render_360:
            user_sel = pedir_user(conn) or user_sel
    console.clear()
    console.print("[dim]o modelo relacional é o que torna o laboratório auditável.[/dim]")


def modo_nao_interativo(conn) -> None:
    """Fallback p/ stdin sem TTY: imprime as 4 abas em sequência.

    No 360 mostra DOIS clientes: o caso real (u_hugo, atrito behavior_inferred)
    e o cliente mais rico — pra a linha do tempo com eventos ⋈ conversas
    aparecer sempre, sem depender do seletor interativo.
    """
    console.print(render_header(0))
    console.print(render_modelo(conn))
    console.print(render_users(conn))
    console.print(render_events(conn))
    lista = usuarios_interessantes(conn)
    rico = next((u for u in lista if u != "u_hugo"), None)
    for uid in [u for u in (lista[0] if lista else None, rico) if u]:
        console.print(render_360(conn, uid))


def main() -> None:
    if not DB.exists():
        console.print(Panel(
            "banco não encontrado.\n\nRode primeiro:  [bold]python scripts/build_db.py[/bold]",
            title="erro", border_style="red", box=ROUNDED))
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
