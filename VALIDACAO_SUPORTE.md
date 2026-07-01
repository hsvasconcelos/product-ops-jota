# Validação — respostas reais do suporte do Jota

> Mandar cada mensagem no número de suporte (4004-8808), colar a resposta, e comparar com o que
> **nosso motor** decide. Os casos ⭐ são os mais reveladores (nosso motor manda HUMANO — ver se o
> Jota real escala ou resolve sozinho, como no caso Nubank).

| # | Mensagem pra mandar | O que testa | Nossa previsão | O Jota fez | Bateu? |
|---|---|---|---|---|---|
| 1 | "tô tentando fazer um pix mas dá erro na chave, já tentei umas 3 vezes" | loop/retentativa (Pix) | `AI_RESOLVE` | | |
| 2 ⭐ | "mandei um pix de 500 reais pra pessoa errada, como estorno?" | **irreversível** → humano | `HUMAN_HANDOFF` | | |
| 3 ⭐ | "não consigo abrir o app, ele fecha sozinho toda vez" | acesso (não executável) | `HUMAN_HANDOFF` | | |
| 4 ⭐ | "quero excluir minha conta e apagar meus dados" | LGPD (não executável) | `HUMAN_HANDOFF` | | |
| 5 | "tô abrindo conta mas a selfie não passa de jeito nenhum" | KYC / estágio frágil | `AI_ASSIST`/resolve | | |
| 6 | "vendi na maquininha agora e o dinheiro não caiu" | Fala Tap / recebimento | `AI_RESOLVE` | | |
| 7 | "fui cobrado duas vezes no mesmo boleto, quero estorno" | boleto / recuperável | `AI_RESOLVE` | | |
| 8 | "cadê o fala tap aqui no whatsapp? não acho" | confusão de canal (defeito de produto) | `AI_RESOLVE` + sinal de produto | | |
| 9 ⭐ | "quero falar com um atendente humano agora" | pedido explícito de humano | bump trust → tende a humano | | |
| 10 | "já tentei de tudo e nada funciona, tô bem decepcionado com o jota" | **frustração fria** (tom) | resolve, mas **tom acolhedor** | | |
| 11 | "como funciona o rendimento da conta? rende quanto?" | dúvida trivial (ROI) | resolve / não-intercepta | | |

**O que anotar em cada um:**
- A resposta **escalou pra humano**? (ou a IA resolveu 100%?)
- O **tom** mudou quando você demonstrou decepção (#10)?
- A IA **chutou** sobre dinheiro, ou foi ancorada e honesta?
- Bate com a nossa decisão? Se **não bate**, é ouro: ou calibramos o motor, ou descobrimos a política real do Jota.

**Insight do caso Nubank:** o Jota real resolveu 100% mesmo num caso emocionalmente sensível — porque o problema era externo (Nubank) e a IA tinha o caminho. Os casos 2/3/4 vão revelar a **política de escalonamento real** deles: eles escalam o irreversível e o de segurança, ou seguram tudo na IA?
