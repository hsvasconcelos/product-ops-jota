# Bot Telegram do Jota — imagem LEVE com qualidade HÍBRIDA via densos HOSPEDADOS.
# JOTA_RAG_MODE=openai: BM25 + embeddings da OpenAI (text-embedding-3-small) + RRF, SEM torch.
# Worker stateless leve + embedder gerenciado = arquitetura de produção real. ~650MB, ~200MB RAM
# (cabe no trial do Railway, não estoura), e restaura tema ~87% + retrieval sem o bug do "wpp".
#
# Variantes: JOTA_RAG_MODE=bm25 (só léxico, offline) · sem a var + [dense] instalado (densos
# LOCAIS com torch, ~2.9GB, precisa de ~1GB RAM — ver commit 6ac3fa8).
FROM python:3.12-slim

WORKDIR /app

# deps: o pacote (pydantic/rich/sklearn/rank-bm25/numpy) + runtime do bot (httpx, openai). SEM torch.
COPY pyproject.toml ./
COPY src ./src
RUN pip install --no-cache-dir . httpx openai

# só o que o bot precisa em runtime (sem o .db, sem evals) — imagem enxuta
COPY apps/telegram_bot.py apps/copiloto.py ./apps/
COPY data/knowledge_base ./data/knowledge_base
COPY data/conversas_ruins.json ./data/

ENV JOTA_RAG_MODE=openai PYTHONUNBUFFERED=1
# TELEGRAM_BOT_TOKEN e OPENAI_API_KEY vêm como secrets da plataforma (nunca na imagem)
CMD ["python", "apps/telegram_bot.py"]
