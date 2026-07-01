"""Banco de frases — gerado UMA vez com o LLM, ancorado na voz real do Jota.
=============================================================================
Por que existe: queremos 10k conversas REALISTAS, mas com gold labels exatos e
reprodutíveis. Solução: o LLM autora (offline, uma vez) um banco de frases reais
de CLIENTE — por tipo de atrito × literacia digital — e o gerador determinístico
depois AMOSTRA desse banco com seed. Realismo do LLM, escala e reprodutibilidade
do gerador. O runtime (detecção) segue 100% determinístico — isto é só dataset.

Saída: data/banco_frases.json   (resumível: só gera os atritos que faltam)

Uso: python scripts/gerar_banco_frases.py
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

# carrega .env (OPENAI_API_KEY / OPENAI_MODEL)
_envf = ROOT / ".env"
if _envf.exists():
    for _l in _envf.read_text().splitlines():
        _l = _l.strip()
        if _l and not _l.startswith("#") and "=" in _l:
            k, v = _l.split("=", 1); os.environ.setdefault(k, v)

OUT = ROOT / "data" / "banco_frases.json"
MODEL = os.environ.get("OPENAI_MODEL", "gpt-4o")

# Voz real do cliente do Jota (não a do bot) — como as pessoas escrevem no WhatsApp.
ESTILO = (
    "Você gera mensagens REAIS de CLIENTES do Jota (assistente financeiro no WhatsApp), "
    "em português do Brasil, do jeito que as pessoas escrevem mesmo: curtas, informais, "
    "muitas com erro de digitação, sem pontuação, estilo de áudio transcrito, às vezes "
    "tudo minúsculo. NUNCA são mensagens do bot — são do cliente com o problema. "
    "Varie de verdade (não repita estrutura). Contexto do produto: Pix por texto/áudio/foto, "
    "Radar de Boletos, Fala Tap (maquininha no app, recebimento D1), Open Finance, conta PF/PJ/MEI, "
    "Rende+ (100% CDI)."
)

# Os atritos do catálogo (FUNDACAO §5 + os demais), com descrição pro LLM.
ATRITOS = {
    "pix_chave_invalida": "Tenta mandar um Pix mas a chave (CPF/telefone/email) está errada ou não é chave Pix ativa do destinatário; tenta de novo, não entende por que não vai.",
    "pix_pessoa_errada": "Mandou (ou quase) um Pix pra chave/pessoa ERRADA e quer reverter; aflito porque Pix não volta.",
    "kyc_biometria": "Abrindo a conta, a validação da selfie/biometria falhou; travado no cadastro, frustrado por não conseguir nem começar.",
    "onboarding_limbo": "Começou a abrir a conta e travou numa etapa, sem erro claro; ou disseram que 'não pode abrir, é interno'. Sumiu, não concluiu.",
    "fala_tap_travado": "Vendeu pela maquininha Fala Tap e o dinheiro não caiu / está travado; ansioso porque é o faturamento dele, achou que era na hora.",
    "estorno_duplicado": "Foi cobrado em duplicidade (a mesma parcela/cobrança saiu 2x) e quer o estorno; com raiva de ter saído dinheiro a mais.",
    "boleto": "Boleto: agendou e não foi pago, ou quer segunda via / saber de cobrança em aberto no nome dele.",
    "open_finance": "Conectou outro banco (Open Finance) e o saldo não atualiza / sumiu, ou quer trazer dinheiro de lá e não consegue.",
    "rende": "Dúvida sobre o rendimento (100% do CDI): se está rendendo mesmo, quanto rende, quando cai.",
    "account_access": "Não consegue acessar o app / o app fecha sozinho / não entra na conta de jeito nenhum.",
    "account_dados_exclusao": "Quer alterar dados (telefone/email) ou EXCLUIR a conta; em alguns casos pediu exclusão e a conta voltou a funcionar (situação sensível).",
    "confusao_canal": "Confuso sobre ONDE fazer algo: procura o Fala Tap no WhatsApp mas é só no app, ou não acha a função, não entende a usabilidade.",
}

SCHEMA_HINT = (
    'Responda APENAS JSON válido com esta forma exata:\n'
    '{"openers": {"low": [12 frases], "medium": [8 frases], "high": [6 frases]}, '
    '"followups": [10 frases], "bot_bad": [5 frases]}\n'
    "- openers: a PRIMEIRA mensagem do cliente sobre esse problema. low=baixa literacia digital "
    "(curtas, com typo, sem pontuação, estilo áudio); medium=intermediária; high=escreve bem.\n"
    "- followups: mensagens seguintes do MESMO cliente (insistência, frustração, dúvida, pedir humano).\n"
    "- bot_bad: respostas RUINS e genéricas que um bot/atendente daria e que pioram a situação."
)


def gerar_atrito(client, chave: str, desc: str) -> dict:
    msg = (f"Atrito: {desc}\n\n{SCHEMA_HINT}")
    r = client.chat.completions.create(
        model=MODEL, max_tokens=1500,
        response_format={"type": "json_object"},
        messages=[{"role": "system", "content": ESTILO}, {"role": "user", "content": msg}],
    )
    return json.loads(r.choices[0].message.content)


def main() -> None:
    if not os.environ.get("OPENAI_API_KEY"):
        print("falta OPENAI_API_KEY no .env"); sys.exit(1)
    from openai import OpenAI
    client = OpenAI()

    banco = json.loads(OUT.read_text("utf-8")) if OUT.exists() else {}
    for chave, desc in ATRITOS.items():
        if chave in banco and banco[chave].get("openers"):
            print(f"  • {chave}: já existe, pulando"); continue
        print(f"  • {chave}: gerando…", flush=True)
        try:
            banco[chave] = gerar_atrito(client, chave, desc)
            OUT.write_text(json.dumps(banco, ensure_ascii=False, indent=2), encoding="utf-8")  # salva incremental
        except Exception as e:
            print(f"    ! falhou ({type(e).__name__}: {e}) — re-rode pra retomar"); break
    tot = sum(len(v.get("openers", {}).get(k, [])) for v in banco.values() for k in ("low", "medium", "high"))
    print(f"\nbanco: {len(banco)} atritos · {tot} openers · salvo em {OUT}")


if __name__ == "__main__":
    main()
