# Fundação — Atendimento Proativo do Jota (v2)

> **O que é.** A base conceitual do sistema, ancorada no **Jota real** (transcript de uso real +
> ReclameAqui) e no **objetivo do case**. É a fonte de verdade: `friction_model`, KB, detecção,
> decisão e cenários **derivam daqui**, não de chute.
>
> **Legenda:** 🟢 fato observado no uso real · 🟡 premissa de modelagem (a confirmar com o time).

---

## 0. A visão de atendimento — os 8 princípios (a fonte)

> Tudo parte daqui. São a tradução do **Jeito Disney / Ritz / Zappos de encantar** para um
> assistente financeiro que vive no WhatsApp. A narrativa do case é top-down: **visão → mecanismo**.
> Cada pergunta do case é a **execução** de um princípio. Os princípios de engenharia (§3) são o
> *fio de credibilidade* — como a gente entrega isso sem quebrar a confiança.

1. **Containment é consequência, não meta.** O sucesso não é a IA "segurar" o chamado — é a jornada
   ser boa o bastante pro problema não nascer. Cada escalonamento é sinal de que o produto ou a
   comunicação falhou.
2. **O atendimento é o laboratório; o produto é a cura.** Todo cluster de chamados que não é bug é
   diagnóstico de jornada. O atendimento é o sensor que aponta onde o produto precisa evoluir.
   Loop: sinal → triagem → ação → medição, com dono em Product Ops.
3. **Cuidar de quem não levanta a mão.** Quem reclama é a minoria visível; a maioria trava em
   silêncio e abandona. Como o Jota já vive no WhatsApp do cliente, tem o dever de agir sobre o
   atrito que ninguém reportou.
4. **Confiança é o produto; protegê-la vem antes de resolver o problema.** *(a chave de abóbada)*
   Em dinheiro, a confiança leva meses pra construir e um "será que meu Pix entrou?" pra rachar — e
   o custo de troca é quase zero (o bancão está no bolso). Desdobramentos: transparência proativa
   vence velocidade; a IA nunca chuta sobre dinheiro; confiança frágil pede humano de propósito.
5. **A natureza do erro define como detectá-lo.** Três naturezas: SYSTEM_SIGNALED (tem evento de
   falha), BEHAVIOR_INFERRED (deduzido do comportamento, sem evento), ABSENCE_DETECTED (a ausência
   de um evento esperado — o silencioso).
6. **Mesmo problema, cuidado sob medida.** A intervenção se calibra por quem está do outro lado:
   PF/PJ/MEI, idade, letramento digital, histórico, estágio da jornada.
7. **Criticidade é o erro vezes o momento.** Criticidade não é propriedade do erro — é função de
   erro × momento × o que está em jogo. A mesma falha de Pix vira emergência com o seu João
   atendendo um cliente na feira agora.
8. **Cuidar não pode custar mais do que o cuidado entrega.** A proatividade tem custo (janela de
   24h e custo por mensagem do WhatsApp, custo humano). Toda interceptação precisa de ROI —
   cuidado dirigido pra onde o risco justifica. É o que torna a visão escalável de 300k a 1M.

> **Premissa estrutural — Tudo é a mesma conversa.** Suporte e produto vivem no mesmo WhatsApp:
> mesma modalidade, mesmo cliente, e o mesmo modelo enxerga os eventos dos dois lados
> (tecnicamente 3 números, mas *uma só superfície*). Um chamado não é evento de "suporte" à
> parte — é um **momento da jornada do produto que quebrou**. Daí decorre: (a) chamado =
> oportunidade direta de produto; (b) **"um só modelo nos dois mundos" não é escolha de
> arquitetura, é a forma natural**; (c) é o que torna o laboratório (#2) possível e o loop curto.

---

## 1. O que o Jota É (de verdade) 🟢

Assistente financeiro com **conta de pagamento** (não é banco; infra Celcoin), que vive **no
WhatsApp** — "pagar, cobrar e organizar suas finanças, 100% no WhatsApp, seguro e de graça".

**Produtos:** Conta (PF 1×CPF / PJ 1+×CNPJ, **PJ em número separado**) · **Pix** (texto/áudio/
imagem/PDF; confirma antes; mín R$0,01; não permite Pix pra si mesmo) · **Boletos/Radar** (só
WhatsApp) · **Cobranças** (QR Pix + Tap-to-Pay) · **Open Finance/Jota Conecta** (só WhatsApp;
**só um lado** — traz, não compartilha) · **Tarefas** · **Análises** · **Rende+** (100% CDI
automático) · **Fala Tap** (maquininha NFC — **só no App**) · **Extrato** (exporta).

**Não tem (ainda):** cartão, empréstimo, investimentos além do Rende+, conta conjunta, câmbio.

**Canais:** 3 números (Suporte 4004-8808 · Produto PF 4004-8006 · Produto PJ separado). **Fala
Tap só no App** (→ confusão de canal). Suporte humano **9h–20h**, não 24h.

**Negócio:** 100% grátis; monetiza no **Fala Tap**. **Janela 24h do WhatsApp:** proativa fora da
janela é **cobrada do Jota** → interceptar tem custo. Série A US$30M (Haun), ~US$185M; Jota 3.0 vindo.

**Restrições que o próprio Jota admitiu (são fontes de atrito!):** features espalhadas
WhatsApp×App · suporte não-24h · Open Finance só um lado · produtos em construção · educação do
usuário (banco por chat estranha).

---

## 2. O Objetivo

> **Escalar 300k → 1M sem a operação de atendimento crescer na mesma proporção.**

Equilibrar **interceptação inteligente × escala**, sob 3 tensões: **custo** (proativa fora da
janela 24h custa) · **cuidado** (confiança é o produto; proativa ruim destrói mais que o atrito) ·
**carga humana** (time 9–20h não pode ser a vazão de tudo).

**Tese:** a carga humana escala com o **tipo de atrito não-resolvido**, não com o nº de clientes.
O loop (§6) derruba os tipos. *Containment não é meta — é consequência de antecipar melhor.*

---

## 3. Princípio transversal — Determinístico > interpretativo (honesto)

Confiança = previsibilidade + auditabilidade. **Determinístico onde DECIDE; interpretativo só
onde REDIGE.** Mas sendo honesto sobre o que **não dá** pra tornar determinístico:

| Camada | Determinístico? | Como |
|---|---|---|
| Eventos de sistema | ✅ total | fato do backend |
| Ausência | ✅ total | timer/janela esperada |
| Contadores de comportamento (retentativa, repetição) | ✅ total | contagem em janela |
| **Intenção / frustração / confusão** | ⚠️ irredutivelmente fuzzy | **classificador calibrado e auditável** (limiar explícito), **nunca "vibe" de LLM** |
| **Decisão (resolve/assiste/humano)** | ✅ total | gates + limiares versionados |
| **Redação da resposta** | ✏️ interpretativo (contido) | LLM só veste a linguagem de algo já decidido |

**Regra de ouro:** a **interpretação pode DETECTAR, mas a AÇÃO sempre passa por gate
determinístico.** Mesma entrada → mesma decisão. Tira o LLM → ainda detecta, decide e roteia.
Direção: a cada iteração, empurrar o que dá pro lado determinístico e encolher a superfície fuzzy.

---

## 4. Dois tipos de atrito (define o destino)

Distinção que muda a estratégia:

- **Atrito resolvível** — a IA (ou um humano) resolve o caso do cliente agora (estorno, Fala Tap
  travado, KYC, limbo). A interceptação **conclui** aqui.
- **Atrito-sintoma de DEFEITO DE PRODUTO** — confusão de canal, Open Finance só um lado, "Pix
  parcelado prometido". A proativa só **paleia**; a cura é no produto. → estes alimentam o
  **feedback de produto** (§6), senão viram chamado recorrente pra sempre.

> **Insight:** o sistema não é só um deflector de chamado — é também um **motor de feedback de
> produto**. Cada atrito-sintoma que recorre é um bug priorizado por dor real do cliente.

---

## 5. Catálogo de Atrito — set enxuto que cobre a AMPLITUDE

Cortamos de 12 → **5 casos**, cada um prova algo distinto (as 3 naturezas × as 4 saídas de
decisão × os 2 tipos × o volume). *O catálogo continua além destes — são os representativos.*

| Caso | Natureza (detecção) | Decisão | Tipo | O que prova |
|---|---|---|---|---|
| **Estorno duplicado** | 🛰 evento `payment.duplicate` | **IA resolve sozinha** (silencioso→avisa) | resolvível | conserta antes de perceber |
| **Fala Tap travado** | 🛰 `settlement.held` + 🔍 ansiedade | **IA resolve/assiste** com prova | resolvível | dinheiro / ansiedade |
| **Limbo de onboarding** | 🕳 ausência de `onboarding.completed` | **IA reengaja/assiste** | resolvível | a falha silenciosa |
| **Exclusão não cumprida (ludopatia)** | 🕳 `account.reactivated` pós-exclusão | **HUMANO** (IA se recusa), prioridade máx | segurança | julgamento / cuidado |
| **Loop de Pix / confusão de canal** | 🔍 retentativa / "cadê o Fala Tap?" | **IA resolve agora + sinaliza PRODUTO** | resolvível **+ defeito** | volume + feedback de produto |

*(🟡 nomes de evento e mecânica da ludopatia a confirmar com o time.)*

---

## 6. Detecção (tempo real)

- **🛰 system_signaled** — evento do backend (`kyc.failed`, `payment.duplicate`, `settlement.held`,
  `account.reactivated`). Determinístico, certeza ~1.0.
- **🔍 behavior_inferred** — padrão na conversa/uso. *Determinístico:* retentativa (≥N falhas),
  loop (intenção repetida). *Fuzzy calibrado:* frustração (léxico + gatilhos PROCON/ReclameAqui),
  confusão de usabilidade/canal, pedido de humano.
- **🕳 absence_detected** — evento esperado que **não veio** (o mais valioso, ninguém reclama):
  limbo de onboarding, exclusão pedida + reativação, boleto sem pagamento. *Determinístico (timer).*

> O atrito mais perigoso é **ausência** — invisível pro help-desk reativo. O proativo vê o que **não** aconteceu.

---

## 7. Decisão (de princípio, com economia)

**4 sinais:** criticidade (severidade × momento × o que está em jogo × **perfil do cliente** —
literacia/segmento) · trust_risk · resolubilidade · confiança da detecção.

**Resolubilidade — definida, não chutada** *(refinada após validar no suporte real, 30/06)*:
`resolubilidade = (existe procedimento na KB que cobre?) × (a IA consegue ENTREGAR a ajuda in-thread?)`.
Sem procedimento ou que exige **privilégio/decisão humana** (recuperar acesso, executar exclusão
LGPD) → baixa → humano.

> **Correção importante:** *irreversibilidade do fato passado NÃO derruba a resolubilidade.* Um Pix
> pra pessoa errada é irreversível — mas a IA ainda entrega a **melhor ajuda possível** (triar
> erro×golpe, orientar devolução/contestação); um humano também não desfaz. Validado: o suporte do
> Jota resolve esses casos sem humano. **A irreversibilidade vira STAKES** (sobe `trust_risk` e a
> criticidade/prioridade), **não falta de capacidade.** O verdadeiro "humano de propósito" é o
> gate de SEGURANÇA (vulnerável), não o irreversível.

**Economia da interceptação (o gate de ROI, quantitativo):**
> intercepta **se** `E[valor] > custo`, onde
> `valor = P(virar chamado) × custo_do_chamado + confiança_retida`
> `custo = custo_da_proativa (se fora da janela 24h) + risco_de_irritar`

Consequência: trivial + baixo risco + baixo P(chamado) → **resolve silencioso ou não toca** (não
gasta proativa à toa). Alto P(chamado) × alto custo → intercepta.

**Gates (a lógica de interceptação), em ordem:** (0) **segurança/vulnerável (ludopatia,
autoexclusão) → humano SEMPRE, prioridade máxima, IA se recusa** *(o real "humano de propósito")* ·
(1) palpite fraco → não age · (2) ROI/custo → silencioso ou não toca · (3) trust alto sem prova →
humano quente · (4) capacidade → resolve / assiste · (5) baixa capacidade → humano.
*(implementado em `decision.py`; o gate 0 entrou em 30/06.)*

**Multi-toque:** intercepta → observa o desfecho → re-intercepta por outro ângulo → handoff quente
com contexto. Nunca bounce frio. **Melhor ajuda:** menor nível que protege a confiança, e
**antecipando as dúvidas que a própria mensagem gera** (o quê/por quê/me afeta/repete).

---

## 8. O Loop — concreto (não mágico)

```
Atrito tratado por humano OU não-resolvido
        │  registra + CLUSTERIZA por tipo
        ▼
   cluster cruza limiar de frequência (≥N no período)?  →  PROMOÇÃO:
        ├─ (a) vira REGRA DE DETECÇÃO  → proativo no Mundo 2 (previne os próximos)
        ├─ (b) vira DOC DE KB          → a IA passa a resolver ancorada
        └─ (c) vira TICKET DE PRODUTO  → se é defeito (§4), conserta na raiz
```

- **Critério de promoção:** frequência do cluster (dor real) — não opinião.
- **Métrica do loop:** tempo "atrito novo → contido" cai; **carga humana por cliente cai** com o tempo.
- Sem loop: carga humana cresce linear → 300k→1M quebra. Com loop: os **tipos** caem; carga ~plana.

---

## 9. Refator (de brinquedo → derivado daqui)

| Nó | Depois |
|---|---|
| Catálogo | os **5 casos** do §5 (amplitude), tipados (resolvível × defeito) |
| Detecção | taxonomia do §6 — determinística onde dá, classificador calibrado onde é fuzzy; ação sempre gated |
| Decisão | gates do §7 + **resolubilidade definida** + **modelo de ROI/custo** + perfil do cliente |
| KB | docs ancorados no produto real (D0/D1, Open Finance um lado, canais, segurança) |
| Cenários | jornadas fiéis ao uso real (transcript), com eventos reais |
| Loop | pipeline do §8 (cluster → promoção → 3 destinos) |
| Demo | detecta → decide → resolve/escala + funil de escala + o loop |

---

*v2 trava aqui. Próximo: refatorar a base nó por nó, começando pela detecção (determinística).*
