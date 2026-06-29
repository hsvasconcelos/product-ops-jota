"""Topologia de canais do Jota — os 3 números de WhatsApp.

  · SUPPORT     — 1 número, compartilhado PF+PJ (Mundo 1, reativo).
  · PRODUCT_PF  — linha de produto/proativo, pessoa física (Mundo 2).
  · PRODUCT_PJ  — linha de produto/proativo, PJ + MEI (Mundo 2).

MEI roteia como PJ — mesmo atrito de recebimento, sem distinção.

store facts, derive views: o FATO é o segmento (users.segment); o número
proativo de destino é DERIVADO dele aqui. Nada de telefone guardado — o
segmento já é o suficiente.
"""
from enum import Enum


class WhatsAppLine(str, Enum):
    SUPPORT = "support"
    PRODUCT_PF = "product_pf"
    PRODUCT_PJ = "product_pj"


def proactive_line(segment: str) -> WhatsAppLine:
    """Número proativo (Mundo 2) do cliente, derivado do segmento. MEI = PJ."""
    return WhatsAppLine.PRODUCT_PF if segment == "pf" else WhatsAppLine.PRODUCT_PJ
