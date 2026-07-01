# Demo · Jota — o Jota proativo, ao vivo no WhatsApp

Pipeline **real** (detecção → RAG → decisão → resposta grounded com um LLM, OpenAI),
respondendo num número de WhatsApp de verdade. O transporte é o **Evolution API**
(gateway não-oficial) em vez da API oficial da Meta — porque é um case, não
produção com cliente. Mostra que dá pra colocar em prod.

## O que sobe
Um `docker compose` com 4 serviços numa caixa só: Postgres + Redis + Evolution
(gateway) + `bot` (o Jota: webhook FastAPI que reusa todo o pipeline do repo).

## Pré-requisitos
- Um VPS pequeno (Hetzner CX22 / DigitalOcean $6) com Docker — robusto pro dia
  da apresentação (não depende do seu laptop/wifi no local).
- Um **número de WhatsApp descartável** (chip burner) — não use o seu pessoal.
- (Opcional) `OPENAI_API_KEY` — sem ele, o bot usa o template determinístico.

## Subir (≈5 min)
```bash
cp .env.example .env      # edite: EVOLUTION_API_KEY (invente), OPENAI_API_KEY
docker compose up -d --build
curl -s localhost:8080 >/dev/null && echo "evolution ok"
docker compose logs -f bot   # deve logar o modo do RAG e llm on/template
```

## Conectar o número (1x — faça ANTES da apresentação)
Use `$EVOLUTION_API_KEY` = o que você pôs no `.env`.

```bash
# 1) cria a instância
curl -X POST localhost:8080/instance/create \
  -H "apikey: $EVOLUTION_API_KEY" -H "Content-Type: application/json" \
  -d '{"instanceName":"jota","integration":"WHATSAPP-BAILEYS","qrcode":true}'

# 2) aponta o webhook pro bot (rede interna do compose)
curl -X POST localhost:8080/webhook/set/jota \
  -H "apikey: $EVOLUTION_API_KEY" -H "Content-Type: application/json" \
  -d '{"webhook":{"enabled":true,"url":"http://bot:8000/webhook","events":["MESSAGES_UPSERT"],"webhookByEvents":false,"base64":false}}'

# 3) pega o QR e escaneia com o WhatsApp do número burner
#    Mais fácil: abra o Manager no navegador → http://SEU_IP:8080/manager
#    (login com a apikey) → instância "jota" → Connect → escaneie o QR.
```

Sessão fica persistida (volume + Postgres/Redis) — não precisa re-escanear a cada `up`.

## Usar (na demo)
Mande pro número conectado:
- `/ajuda` — lista os comandos
- `/pix`, `/kyc`, `/falatap`, `/conta`, `/boleto`, `/openfinance`, `/pixerrado`,
  `/kyclimbo`, `/excluir` — injetam o contexto de uma conversa-ruim **real** e o
  Jota responde **proativo**, ancorado num procedimento.
- Depois **digite à vontade** — ele continua a conversa, sempre ancorado.

O terminal (`docker compose logs -f bot`) mostra o cérebro decidindo —
tema/natureza, ação (resolve/assiste/humano), prioridade e a linha proativa
(PF/PJ). Bom pra narrar e como tela de apoio.

## Plano B (se o WhatsApp travar ao vivo)
A demo ao vivo não pode derrubar a apresentação. Fallback scriptado: rode o copiloto no
terminal — `.venv/bin/python apps/copiloto.py` — que roda o MESMO pipeline offline.

## Checklist da manhã do dia
- [ ] `docker compose ps` — tudo `up`
- [ ] `curl localhost:8000` no host do bot → `llm: on`, `rag: ...`
- [ ] mandar `/pix` do seu celular e ver a resposta chegar
- [ ] número burner com bateria/sessão ativa
