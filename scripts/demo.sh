#!/usr/bin/env bash
# Lançador blindado da demo do Jota — um comando: sobe redondo e abre o browser.
#
#   scripts/demo.sh            modo openai (melhor qualidade · precisa de wifi p/ os embeddings)
#   scripts/demo.sh --offline  modo bm25 (retrieval/tema 100% locais; a resposta cai em
#                              template da KB se a OpenAI estiver fora — a demo NÃO trava)
#
# Mata processo velho na porta, carrega a env, sobe o uvicorn, ESPERA ficar de pé
# (health check) e só então abre o navegador. Se não subir, mostra o log.
set -uo pipefail
cd "$(dirname "$0")/.."
PORT=8800
MODE="openai"; [ "${1:-}" = "--offline" ] && MODE="bm25"

echo "▸ derrubando instância antiga (se houver)…"
pkill -f "uvicorn apps.web_demo" 2>/dev/null || true
sleep 1

export JOTA_RAG_MODE="$MODE" PYTHONUNBUFFERED=1   # a OPENAI_API_KEY vem do .env (o app carrega)

echo "▸ subindo a demo (modo $MODE)…"
nohup .venv/bin/uvicorn apps.web_demo:app --port "$PORT" > /tmp/jota_demo.log 2>&1 &
disown

for _ in $(seq 1 45); do   # health check: até ~90s (openai baixa/embeda no boot)
  if curl -s -o /dev/null -w "%{http_code}" "http://127.0.0.1:$PORT/play" 2>/dev/null | grep -q 200; then
    URL="http://localhost:$PORT/play"
    # modo REAL que subiu (sem downgrade silencioso): lê o log do retriever
    REAL=$(grep -oE "RAG: modo [a-z0-9-]+|BM25 fallback" /tmp/jota_demo.log | tail -1)
    echo "✓ no ar!  →  $URL"
    if [ "$MODE" = "openai" ] && echo "$REAL" | grep -qi "bm25\|fallback"; then
      echo "⚠ ATENÇÃO: pedi openai mas subiu em BM25 (${REAL:-?}) — provável soluço de rede no boot."
      echo "  Qualidade reduzida. Cheque a internet e rode 'scripts/demo.sh' de novo."
    else
      echo "  motor: ${REAL:-modo $MODE}"
    fi
    command -v open >/dev/null 2>&1 && open "$URL"
    exit 0
  fi
  sleep 2
done

echo "✗ não subiu em 90s. Últimas linhas do log (/tmp/jota_demo.log):"
tail -15 /tmp/jota_demo.log
exit 1
