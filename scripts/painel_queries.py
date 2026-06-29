"""Teaser do painel (Mundo 1): roda as queries que viram as abas do painel.

É uma PRÉVIA — mostra que o banco responde as perguntas de produto em SQL real.
Uso:
    python scripts/painel_queries.py
"""
import sqlite3
import sys
from pathlib import Path

DB = Path(__file__).resolve().parents[1] / "data" / "jota_support.db"


def main() -> None:
    if not DB.exists():
        sys.exit("banco não encontrado — rode primeiro: python scripts/build_db.py")
    conn = sqlite3.connect(DB)
    q = lambda sql: conn.execute(sql).fetchall()

    print("═" * 60)
    print("PAINEL — Atendimento como laboratório (Mundo 1)")
    print("═" * 60)

    print("\n▸ Volumetria por tema (o que priorizar p/ antecipar)")
    for theme, n in q("SELECT gold_theme, COUNT(*) FROM conversations GROUP BY gold_theme ORDER BY 2 DESC"):
        bar = "█" * (n // 5)
        print(f"  {theme:20} {n:4}  {bar}")

    print("\n▸ Natureza do atrito (só 2 no suporte: quem está aqui FALOU)")
    for nat, n in q("SELECT gold_nature, COUNT(*) FROM conversations GROUP BY gold_nature"):
        print(f"  {nat:20} {n:4}")

    print("\n▸ SLA de 1ª resposta — derivado por window function (não armazenado)")
    row = q("""WITH g AS (SELECT conversation_id, sender, sent_at,
                LAG(sent_at) OVER (PARTITION BY conversation_id ORDER BY turn_index) prev,
                LAG(sender)  OVER (PARTITION BY conversation_id ORDER BY turn_index) ps
              FROM messages)
            SELECT ROUND(AVG((julianday(sent_at)-julianday(prev))*24),1),
                   ROUND(MAX((julianday(sent_at)-julianday(prev))*24),1)
            FROM g WHERE ps='customer' AND sender='human_agent'""")[0]
    print(f"  média {row[0]}h | pior {row[1]}h   (baseline real Jota ~33h, ReclameAqui)")

    print("\n▸ Pedidos de humano IGNORADOS (pediu, sem handoff)")
    row = q("SELECT SUM(asked_for_human), SUM(human_handoff_done) FROM conversations")[0]
    print(f"  {row[0]} pediram · {row[1]} atendidos · {row[0]-row[1]} ignorados")

    print("\n▸ Tendência semanal (semanas com mais atrito por tema — top 5)")
    rows = q("""SELECT strftime('%Y-W%W', started_at) wk, gold_theme, COUNT(*) n
                FROM conversations GROUP BY wk, gold_theme ORDER BY n DESC LIMIT 5""")
    for wk, theme, n in rows:
        print(f"  {wk}  {theme:20} {n}")

    conn.close()
    print("\n(prévia — o painel completo vira abas navegáveis na apresentação)")


if __name__ == "__main__":
    main()
