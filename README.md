# Case Product Ops · Jota — Atendimento Proativo

> **O melhor chamado é o que não nasce.**
> A conta de 300 mil para 1 milhão de clientes só fecha se cada vez menos problemas
> precisarem virar chamado: detectar o atrito na origem (pelo evento, pelo comportamento
> ou pela ausência), resolver ancorado em fonte, escalar com contexto — e devolver cada
> atrito como melhoria de produto, para ele não nascer de novo.

Protótipo funcional de **atendimento proativo** para o Jota (assistente financeiro no
WhatsApp): um só motor decidindo nos dois mundos (reativo e proativo), com detecção
determinística, decisão por réguas auditáveis, RAG ancorado em base de conhecimento,
medição de desfecho (fechado ≠ resolvido) e uma suíte de evals que roda no CI.

---

## A apresentação (comece por aqui)

```bash
bash scripts/demo.sh        # sobe tudo e abre http://localhost:8800/play
```

**`/play`** — a apresentação em 7 abas: **visão** (a tese e os 8 princípios) ·
**jornada** (os 4 níveis de proatividade e a régua de onde agir) · **laboratório**
(10 mil conversas rotuladas: onde a jornada quebra, a triagem do motor, lacunas de KB
e um explorador SQL ao vivo) · **lógica & interceptação** (o motor ensinado em duas
fases — medir e decidir — e provado ao vivo, caso a caso) · **evals** (a prova do
próprio motor) · **time de atendimento** (handoff quente, filas, horários e as
preocupações honestas) · **kpis** (o norte e as 4 famílias de métricas).

**`/`** — o mockup de fluxo (Pergunta 5): chat estilo WhatsApp com cenários reativos e
proativos, radar de sinais em tempo real e o painel de engenharia de cada resposta.

**Bot real no Telegram** — o mesmo cérebro em produção (long polling):
`.venv/bin/python apps/telegram_bot.py` · comandos `/demo-*` para os cenários
proativos, `/debug` para ver a engenharia e a saúde (modo RAG · LLM · KB).

## O motor (`src/product_ops_jota/`)

| Peça | O que faz |
|---|---|
| `classifier.py` | detecção: tema (semântica + léxico + juiz no resíduo) e natureza (evento · comportamento · ausência), com sinais auditáveis |
| `decision.py` | a cascata: 4 sinais derivados dos fatos (certeza, criticidade, confiança em jogo, resolubilidade) × 8 réguas em ordem fixa; a primeira que dispara decide |
| `rag.py` | retrieval híbrido (BM25 + densos hospedados + RRF) com fallback gracioso por chamada — um soluço de rede degrada a query, nunca derruba o turno |
| `outcome.py` | desfecho: chamado fechado ≠ atrito resolvido (cura confirmada · confirmação do cliente · silêncio lido por quem falou por último) |
| `handoff.py` | operação humana: pacote de contexto, fila por especialidade, ciência de horário, anti-reincidência |
| `trace.py` | observabilidade: cada decisão vira uma linha auditável (JSONL) |
| `incident.py` | modo incidente: pico do mesmo evento congela a proativa individual e muda para comunicação canônica |

Princípios de engenharia: **determinístico onde decide, LLM só onde redige** ·
*store facts, derive views* · *policy vs mechanism* (todo número vive em tabela
nomeada e recalibrável) · *fail-safe* (na dúvida, pessoa).

## A prova (`evals/` — roda no CI a cada mudança)

Quatro camadas: **contratos** (7 métricas com limiar; reprovou, não entra) ·
**scripted** (15 conversas ponta a ponta) · **cliente-LLM + juiz** (red team barato) ·
**soak** (1.440 conversas overnight, robustez por estilo de cliente).

```bash
.venv/bin/python tests/test_core.py     # o gate rápido (o mesmo do CI)
.venv/bin/python evals/run_all.py       # o scorecard completo
```

Scorecard atual (contra gabarito curado; em produção a régua é o desfecho real):
natureza 100% · tema 85,0% · Hit@3 100% · decisão 15/15 (zero divergências) ·
desfecho binário 88,7% (um terço confirmado por evento de cura). Triagem do motor
nos 5 mil chamados do laboratório: **IA resolve 55,3% · vai para humano 44,7%**,
fail-safe por desenho — e o loop já roda de verdade: a recalibração da política
contra o desfecho (`scripts/recalibrar_policy.py`) e a esteira de promoção de
clusters (`scripts/promover_clusters.py`) provam como esse número cai sem afrouxar
nenhuma régua.

## O dado (honestidade)

A base (`data/jota_support.db`, 10 mil conversas rotuladas + eventos) é **sintética,
com rótulo determinístico e ancorada nas reclamações reais do Jota no ReclameAqui** —
nenhuma informação confidencial de terceiros. O gabarito (`gold_*`) existe só para o
avaliador: o motor nunca o lê. Reproduzível com `python scripts/build_db.py` (seed fixa).

## Ambiente

```bash
brew install python@3.12
python3.12 -m venv .venv && source .venv/bin/activate
pip install -e .
# opcional (.env): OPENAI_API_KEY para redação/juízes; sem ela, a demo roda em template
```

Deploy do bot (Railway/Fly, imagem leve sem torch): ver `docs/DEPLOY.md`.
Fundação conceitual (os 8 princípios e as decisões de modelagem): `docs/FUNDACAO.md`.
