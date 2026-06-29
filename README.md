# Product Ops · Jota — Atendimento Proativo

Visão e protótipo de um modelo de **atendimento proativo** para o Jota (assistente
financeiro no WhatsApp), pensado para escalar de **300 mil → 1 milhão de clientes**
sem que a operação cresça na mesma proporção.

A tese: **transformar o atendimento de uma central que apaga incêndios numa rede
invisível que antecipa o atrito, protege a confiança e devolve cada problema como
melhoria de produto.**

---

## Os dois mundos

O Jota tem dois canais de WhatsApp, e a estratégia é diferente em cada um:

**Mundo 1 — o número de atendimento (reativo → laboratório).**
Tudo que não foi antecipado cai aqui. Este canal é a *mina de dados*: a gente analisa
todas as conversas, classifica, mede SLA e volumetria, e descobre **o que deveria estar
sendo antecipado no produto**. É o laboratório.

**Mundo 2 — o número do Jota (proativo → antecipação).**
Onde a gente age antes do chamado existir: detecta o atrito na origem, decide se a IA
resolve, assiste ou chama um humano, e responde ancorado em dados reais.

O **loop** entre eles é a tese central: o Mundo 1 alimenta o Mundo 2, para que cada vez
menos coisa precise cair no Mundo 1. *Containment não como meta, mas como consequência
de antecipar melhor.*

---

## O que já está construído (e testado)

| Peça | Arquivo | Mundo |
|---|---|---|
| **Mapa de problemas** — schema tipado do atrito (natureza, criticidade, risco de confiança) + 3 casos-herói | `src/product_ops_jota/friction_model.py` | 2 |
| **Score de priorização** — decide resolver / assistir / humano + prioridade na fila | `src/product_ops_jota/decision.py` | 2 |
| **Banco do laboratório** — schema relacional (4 tabelas) do canal de suporte | `src/product_ops_jota/support_db.py` | 1 |
| **Gerador de conversas** — 1.000 conversas sintéticas rotuladas, ancoradas no ReclameAqui real do Jota | `src/product_ops_jota/generate_support_data.py` | 1 |

Princípios de engenharia aplicados: *store facts, derive views* (o banco guarda fatos;
SLA/idades/scores são funções puras), *policy vs mechanism* (pesos e limiares são
configuração tipada e versionável), e *fail-fast* (schema inconsistente quebra na
construção, não em produção).

---

## Ambiente recomendado

Rode o projeto em Python 3.12 dentro de um venv isolado — isso evita os atritos do Python 3.9/pip antigo do sistema (não precisa de `eval_type_backport` nem de `python3 -m pip`).

```bash
brew install python@3.12
python3.12 -m venv .venv
source .venv/bin/activate
pip install -e .
```

---

## Como rodar

```bash
pip install -e .            # ou: pip install pydantic

# 1) gerar o banco do laboratório (1000 conversas rotuladas)
python scripts/build_db.py

# 2) ver o teaser do painel (volumetria, SLA, naturezas, tendência) em SQL real
python scripts/painel_queries.py

# 3) painel completo do Mundo 1 (laboratório): TUI navegável por teclado — abas 1-5, setas, t p/ drill-down, q sai (num terminal real; cai em modo não-interativo sem TTY)
python scripts/painel.py

# 4) rodar os testes (schema, score, banco)
python tests/test_core.py
```

O banco (`data/jota_support.db`) já vem gerado — o passo 1 só é necessário para
regenerar do zero. É reproduzível (seed fixa).

### Espiar o score em ação

```python
from product_ops_jota.friction_model import HERO_CASES
from product_ops_jota.decision import decide_for_case

resolv = {"pix_key_loop": 0.85, "kyc_failed_onboarding": 0.45, "fala_tap_receipt_anxiety": 0.90}
for c in HERO_CASES:
    d = decide_for_case(c, resolv[c.case_id])
    print(c.case_id, "→", d.action.value, "|", d.reason)
```

---

## Os 3 casos-herói

São reclamações **reais** do Jota (ReclameAqui), não hipóteses:

1. **Loop de chave Pix** — o cliente tenta o mesmo Pix 3x e trava; nada falhou no sistema
   (`behavior_inferred`). É falha de *jornada*, não de Pix.
2. **KYC falhou no onboarding** — `kyc.failed` (`system_signaled`), no momento mais frágil
   da jornada (antes do primeiro valor).
3. **Insegurança no recebimento via Fala Tap** — a venda foi aprovada, mas o plano é D1
   (cai amanhã); a "falha" é de *percepção*, e o custo de troca é quase zero
   (a maquininha tradicional está no bolso). Confiança pura.

---

## Roadmap (mapeado, próximos passos)

- **Painel completo** do Mundo 1 (abas navegáveis: volumetria, tendência, priorização, SLA).
- **RAG** ancorado na base de conhecimento (`data/knowledge_base/`) — híbrido (BM25 + denso
  + re-ranking) para o gerador de respostas do Mundo 2.
- **Pipeline ponta-a-ponta** (conversa → detecção → score → RAG → resposta + passo a passo).
- **Operação humana** — warm handoff com contexto entre os dois números, filas por especialidade.
- **Versionamento da policy** — pesos/limiares como artefato versionado, promovido por gate de evals.

---

*Protótipo de Product Ops. Dado sintético com rótulo determinístico (especificado na
geração) e texto realista; nenhuma informação confidencial de terceiros.*
