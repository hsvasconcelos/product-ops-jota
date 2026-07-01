# Bot Telegram do Jota — imagem LEVE (BM25 + LLM-judge, sem torch) pra always-on barato.
# O mesmo cérebro de src/; o RAG roda em modo BM25 (JOTA_RAG_MODE=bm25) — validado: qualidade
# holds (reativo 15/15, proativo 7/7) porque o LLM-judge compensa o tema semântico.
FROM python:3.12-slim

WORKDIR /app

# deps: o pacote (pydantic/rich/sklearn/rank-bm25) + runtime do bot (httpx, openai). SEM [dense].
COPY pyproject.toml ./
COPY src ./src
RUN pip install --no-cache-dir . httpx openai

# só o que o bot precisa em runtime (sem o .db, sem evals) — imagem enxuta
COPY apps/telegram_bot.py apps/copiloto.py ./apps/
COPY data/knowledge_base ./data/knowledge_base
COPY data/conversas_ruins.json ./data/

ENV JOTA_RAG_MODE=bm25 PYTHONUNBUFFERED=1
# TELEGRAM_BOT_TOKEN e OPENAI_API_KEY vêm como secrets da plataforma (nunca na imagem)
CMD ["python", "apps/telegram_bot.py"]
