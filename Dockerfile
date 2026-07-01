# Bot Telegram do Jota — imagem HÍBRIDA: BM25 + densos (sentence-transformers) + rerank.
# Mesma qualidade do dev (tema 87.6%, retrieval Hit@3 100%). Os modelos densos são ASSADOS
# na imagem no build → zero download em runtime (cold start rápido, reproduzível, offline).
FROM python:3.12-slim

WORKDIR /app

# torch CPU-only primeiro (wheel enxuto, sem CUDA — economiza ~1.5GB vs o wheel padrão)
RUN pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu

# deps: o pacote + extra [dense] (sentence-transformers) + runtime do bot (httpx, openai)
COPY pyproject.toml ./
COPY src ./src
RUN pip install --no-cache-dir ".[dense]" httpx openai

# assa os 2 modelos densos na imagem (baixa 1x no build, nunca em runtime)
RUN python -c "from sentence_transformers import SentenceTransformer, CrossEncoder; \
SentenceTransformer('paraphrase-multilingual-MiniLM-L12-v2'); \
CrossEncoder('cross-encoder/ms-marco-MiniLM-L-6-v2')"

# só o que o bot precisa em runtime (sem o .db, sem evals) — imagem enxuta
COPY apps/telegram_bot.py apps/copiloto.py ./apps/
COPY data/knowledge_base ./data/knowledge_base
COPY data/conversas_ruins.json ./data/

ENV PYTHONUNBUFFERED=1
# TELEGRAM_BOT_TOKEN e OPENAI_API_KEY vêm como secrets da plataforma (nunca na imagem)
CMD ["python", "apps/telegram_bot.py"]
