# Deploy do bot Telegram — sempre no ar (sem depender do notebook)

O bot faz **long polling** (não precisa de URL pública/webhook), roda em modo **híbrido**
(BM25 + densos + rerank — os modelos são assados na imagem no build, sem download em runtime).
É a mesma qualidade do dev: detecção de tema 87.6%, retrieval Hit@3 100%. Imagem ~2GB (torch
CPU-only), RAM de runtime ~1GB. O modo leve BM25 (`JOTA_RAG_MODE=bm25`) segue disponível como
fallback, mas medimos que degrada o tema pra ~76% e confunde o retrieval — por isso prod = híbrido.

Precisa de **2 secrets** (nunca no código):
- `TELEGRAM_BOT_TOKEN` (do @BotFather)
- `OPENAI_API_KEY`

---

## Opção A — Railway (GUI, recomendado, mais fácil)

1. Entre em **railway.app** e faça login com o **GitHub**.
2. **New Project → Deploy from GitHub repo →** selecione `product-ops-jota` (autorize o acesso ao repo privado).
3. Railway detecta o **`Dockerfile`** da raiz e builda sozinho.
4. **Variables →** adicione:
   - `TELEGRAM_BOT_TOKEN` = seu token
   - `OPENAI_API_KEY` = sua key
5. **Deploy.** Pronto — o bot fica no ar 24/7 (Railway não hiberna serviços com processo ativo).

Custo: plano hobby, ~US$5/mês de uso (uma instância pequena basta).

---

## Opção B — Fly.io (CLI, tem free allowance)

```bash
# 1. instalar o flyctl (uma vez):  https://fly.io/docs/flyctl/install/
brew install flyctl                      # macOS

# 2. login (abre o browser)
fly auth login

# 3. criar o app a partir do Dockerfile da raiz (NÃO deployar ainda)
fly launch --no-deploy --dockerfile Dockerfile

# 4. secrets (ficam criptografados na Fly, nunca na imagem)
fly secrets set TELEGRAM_BOT_TOKEN=SEU_TOKEN OPENAI_API_KEY=SUA_KEY

# 5. deploy
fly deploy
```

Como é long polling (sem porta HTTP), no `fly.toml` gerado dá pra remover a seção
`[http_service]` — o bot é um worker que só sai puxando updates. Uma máquina
`shared-cpu-1x` com 256–512MB basta.

---

## Verificar

- No Telegram, mande `/start` pro bot — deve responder.
- Feche o notebook / mate o processo local: o bot **continua respondendo** (agora vive na nuvem).
- Logs: Railway (aba Deployments → Logs) ou `fly logs`.

## Parar / atualizar

- Railway: redeploy automático a cada push no `main`.
- Fly: `fly deploy` de novo após um push; `fly apps destroy <app>` pra remover.

> Produção de verdade (fora do case): trocar o BM25 por um embedder hospedado (API), serving
> assíncrono (webhook + fila + workers) e observabilidade/alertas. A imagem leve aqui é o
> "no ar de verdade" com o mesmo cérebro.
