"""Gera o banco de suporte do zero (1000 conversas rotuladas).

Uso:
    python scripts/build_db.py            # gera data/jota_support.db
    python scripts/build_db.py --n 500    # quantidade custom
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from product_ops_jota.support_db import init_db, seed_real_conversation
from product_ops_jota.generate_support_data import generate

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=1000, help="número de conversas sintéticas")
    ap.add_argument("--out", default="data/jota_support.db", help="caminho do banco")
    args = ap.parse_args()

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)

    conn = init_db(out)
    seed_real_conversation(conn)          # a conversa real (Hugo) como caso-âncora
    generate(conn, n_conversations=args.n)
    n = conn.execute("SELECT COUNT(*) FROM conversations").fetchone()[0]
    print(f"✓ banco gerado em {out} com {n} conversas")
    conn.close()
