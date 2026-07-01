"""Detecção de atrito → RAG → resposta recomendada + passo a passo.
=============================================================================
O ENTREGÁVEL do case, em um script simples e auditável.

Lê de um JSON exemplos de "conversas ruins" (mensagens do cliente + a resposta
ruim do bot) e, para CADA caso:

  [1] aplica uma LÓGICA OBJETIVA de detecção de atrito (regras/contadores/eventos
      auditáveis — não "achismo" de LLM): tema, natureza e os sinais que dispararam;
  [2] consulta o RAG (base de conhecimento do Jota) por evidência ancorada;
  [3] gera a RESPOSTA RECOMENDADA ao cliente, ancorada na evidência (sem inventar);
  [4] entrega o PASSO A PASSO claro de resolução.

Determinístico de ponta a ponta: mesma conversa → mesma detecção, mesma resposta.
(Um LLM poderia, opcionalmente, só dar o acabamento de linguagem por cima do mesmo
conteúdo ancorado — sem nunca decidir nem inventar procedimento.)

Uso:
    python scripts/resolver_conversas_ruins.py                 # todos os casos
    python scripts/resolver_conversas_ruins.py ex_pix_loop     # um caso
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from product_ops_jota.classifier import classify_conversation
from product_ops_jota.friction_model import SupportTheme
from product_ops_jota.rag import Retriever

DATA = Path(__file__).resolve().parents[1] / "data" / "conversas_ruins.json"

# Abertura empática por tema (pergunta de confirmação — se errar, o cliente corrige).
ACK = {
    SupportTheme.PIX: "vi que você pode estar tendo dificuldade com um Pix",
    SupportTheme.FALA_TAP: "vi que pode ser sobre o recebimento de uma venda no Fala Tap",
    SupportTheme.KYC: "vi que pode estar travando na abertura/verificação da conta",
    SupportTheme.BOLETO: "vi que pode ser sobre uma cobrança",
    SupportTheme.ACCOUNT_ACCESS: "vi que pode estar com dificuldade pra acessar o app",
    SupportTheme.ACCOUNT_DATA: "vi que é sobre seus dados ou a sua conta",
    SupportTheme.YIELD_OPEN_FINANCE: "vi que pode ser sobre rendimento ou Open Finance",
    SupportTheme.OTHER: "me conta um pouco mais pra eu te ajudar",
}


def resposta_recomendada(theme, doc) -> tuple[str, list[str]]:
    """Resposta ancorada no doc recuperado (determinística, sem alucinar)."""
    if doc is None:
        return ("Não tenho um procedimento ancorado pra isso na base — vou te conectar "
                "com um atendente, levando todo o contexto, sem te fazer repetir nada."), []
    ack = ACK.get(theme, ACK[SupportTheme.OTHER])
    return f"Oi! {ack}, certo? {doc.content}", doc.steps


def linha(c: str = "─", n: int = 76) -> str:
    return c * n


def resolver(conv: dict, rag: Retriever) -> None:
    msgs = [(m["sender"], m["text"], m["sent_at"]) for m in conv["mensagens"]]
    events = [(e["event_type"], e["occurred_at"]) for e in conv.get("eventos", [])]

    # [1] DETECÇÃO OBJETIVA
    det = classify_conversation(msgs, events, conv["started_at"])
    # [2] RAG (consulta por texto do cliente + tema)
    texto_cliente = " ".join(m["text"] for m in conv["mensagens"] if m["sender"] == "customer")
    docs = rag.retrieve(f"{texto_cliente} {det.predicted_theme.value.replace('_', ' ')}", top_k=1)
    doc = docs[0] if docs else None
    # [3]+[4] resposta recomendada + passo a passo
    corpo, passos = resposta_recomendada(det.predicted_theme, doc)

    print(f"\n{linha('═')}")
    print(f"CASO: {conv['id']} — {conv.get('descricao', '')}")
    print(linha())
    print("Conversa (o que aconteceu):")
    for m in conv["mensagens"]:
        marca = "  ← resposta ruim" if m["sender"] in ("bot", "human_agent") else ""
        print(f"  {m['sender']:>11}: {m['text']}{marca}")

    def _sig(s):
        txt = s.tipo
        if s.valor is not None:
            txt += f"={s.valor}" + (f"≥{s.limiar}" if s.limiar is not None else "")
        return txt + ("" if s.deterministico else " (calibrado)")

    disparados = [s for s in det.signals if s.disparou]
    print(f"\n[1] DETECÇÃO OBJETIVA DE ATRITO")
    print(f"    tema......: {det.predicted_theme.value} ({det.theme_confidence:.2f})")
    print(f"    natureza..: {det.predicted_nature.value} ({det.nature_confidence:.2f})")
    print(f"    sinais....: " + (", ".join(_sig(s) for s in disparados) or "—"))

    print(f"\n[2] EVIDÊNCIA RECUPERADA (RAG · {rag.mode})")
    if doc:
        print(f"    {doc.id} · \"{doc.title}\"  (score {doc.score:.3f})")
    else:
        print("    nenhum procedimento relevante na base")

    print(f"\n[3] RESPOSTA RECOMENDADA AO CLIENTE")
    print(f"    {corpo}")

    print(f"\n[4] PASSO A PASSO DE RESOLUÇÃO")
    if passos:
        for i, p in enumerate(passos, 1):
            print(f"    {i}. {p}")
    else:
        print("    (encaminhar a um humano com o contexto)")


def main() -> None:
    if not DATA.exists():
        print(f"não achei {DATA}"); sys.exit(1)
    convs = json.loads(DATA.read_text(encoding="utf-8"))
    alvo = sys.argv[1] if len(sys.argv) > 1 else None
    if alvo:
        convs = [c for c in convs if c["id"] == alvo]
        if not convs:
            print(f"caso '{alvo}' não encontrado"); sys.exit(1)

    rag = Retriever()
    for conv in convs:
        resolver(conv, rag)
    print(f"\n{linha('═')}\n{len(convs)} caso(s) processado(s).")


if __name__ == "__main__":
    main()
