"""RAG do copiloto — retrieval híbrido com FALLBACK gracioso.
=============================================================================
Recupera, da base de conhecimento (data/knowledge_base/jota_kb.json), os
procedimentos mais relevantes pro atrito detectado. É o que ancora a sugestão
do copiloto em FONTE — "a IA nunca chuta sobre dinheiro".

Retrieval em camadas, da mais robusta pra mais cara:
  1. BM25 (rank_bm25)            — esparso, por termo, SEMPRE disponível, offline.
  2. Densos (sentence-transformers, modelo pequeno multilíngue) — QUANDO disponível.
  3. Fusão RRF (reciprocal rank fusion) de (1)+(2).
  4. Re-rank cross-encoder       — QUANDO disponível.

FALLBACK GRACIOSO (o ponto crítico da demo) — duas trancas, nenhuma propaga
exceção pro chamador:
  · tranca 1: `import sentence_transformers` em try/except → ausente ⇒ modo BM25.
  · tranca 2: carregar o modelo em try/except (sem internet / não baixado /
    qualquer erro) ⇒ modo BM25.
O Retriever SEMPRE tem BM25 funcionando, então o sistema roda 100% offline no
mínimo. O modo ativo é logado uma vez no init.
"""
from __future__ import annotations

import json
import logging
import math
import re
import unicodedata
from pathlib import Path

from pydantic import BaseModel, Field

logger = logging.getLogger("product_ops_jota.rag")

# ─── POLICY (config nomeada no topo) ─────────────────────────────────────────
KB_PATH = Path(__file__).resolve().parents[2] / "data" / "knowledge_base" / "jota_kb.json"
TOP_K = 3                      # docs retornados por padrão
RRF_K = 60                     # constante da reciprocal rank fusion (padrão da literatura)
DENSE_MODEL = "paraphrase-multilingual-MiniLM-L12-v2"   # pequeno, multilíngue
CROSS_ENCODER = "cross-encoder/ms-marco-MiniLM-L-6-v2"  # re-rank opcional
RRF_CANDIDATES = 8             # quantos candidatos cada camada manda pra fusão


class RetrievedDoc(BaseModel):
    id: str
    title: str
    content: str
    steps: list[str] = Field(default_factory=list)
    score: float                      # score final (RRF ou BM25, conforme o modo)


def _normalize(text: str) -> str:
    t = unicodedata.normalize("NFKD", text or "")
    t = "".join(ch for ch in t if not unicodedata.combining(ch))
    return t.lower()


def _tokenize(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", _normalize(text))


def _doc_text(doc: dict) -> str:
    """Texto indexável: título + conteúdo + categoria + passos."""
    return " ".join([doc.get("title", ""), doc.get("content", ""),
                     doc.get("category", ""), " ".join(doc.get("steps", []))])


class Retriever:
    """Indexa a KB uma vez; reusa em cada query. Decide o modo no init."""

    def __init__(self, kb_path: Path | str = KB_PATH):
        self.docs: list[dict] = json.loads(Path(kb_path).read_text(encoding="utf-8"))
        self._tokenized = [_tokenize(_doc_text(d)) for d in self.docs]

        # ── BM25 (sempre) ────────────────────────────────────────────────────
        from rank_bm25 import BM25Okapi
        self._bm25 = BM25Okapi(self._tokenized)

        # ── Densos + re-rank (tranca 1 e 2) ──────────────────────────────────
        self._dense_model = None
        self._doc_embeddings = None
        self._cross_encoder = None
        self.mode = "bm25"
        motivo = self._try_load_dense()
        if self._dense_model is not None:
            self.mode = "hibrido"
            logger.info("RAG: modo híbrido (BM25 + densos%s)",
                        " + rerank" if self._cross_encoder else "")
        else:
            logger.info("RAG: modo BM25 fallback (densos indisponíveis: %s)", motivo)

    def _try_load_dense(self) -> str:
        """Tenta habilitar densos. Devolve o motivo da falha (string vazia se ok).
        NUNCA levanta — o chamador segue com BM25."""
        try:
            from sentence_transformers import SentenceTransformer  # tranca 1
        except Exception as e:
            return f"sentence-transformers não instalado ({type(e).__name__})"
        try:
            model = SentenceTransformer(DENSE_MODEL)               # tranca 2
            self._doc_embeddings = model.encode(
                [_doc_text(d) for d in self.docs], normalize_embeddings=True)
            self._dense_model = model
        except Exception as e:  # sem internet, modelo não baixado, etc.
            return f"modelo não carregou ({type(e).__name__})"
        # re-rank é bônus sobre o híbrido; falha dele não derruba os densos
        try:
            from sentence_transformers import CrossEncoder
            self._cross_encoder = CrossEncoder(CROSS_ENCODER)
        except Exception:
            self._cross_encoder = None
        return ""

    # ── ranking helpers ──────────────────────────────────────────────────────
    def _bm25_ranking(self, query: str) -> list[int]:
        scores = self._bm25.get_scores(_tokenize(query))
        return sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)

    def _dense_ranking(self, query: str) -> list[int]:
        import numpy as np
        q = self._dense_model.encode([query], normalize_embeddings=True)[0]
        sims = self._doc_embeddings @ q          # cosseno (já normalizado)
        return sorted(range(len(sims)), key=lambda i: float(sims[i]), reverse=True)

    @staticmethod
    def _rrf(rankings: list[list[int]]) -> dict[int, float]:
        fused: dict[int, float] = {}
        for ranking in rankings:
            for rank, idx in enumerate(ranking[:RRF_CANDIDATES]):
                fused[idx] = fused.get(idx, 0.0) + 1.0 / (RRF_K + rank + 1)
        return fused

    def retrieve(self, query: str, top_k: int = TOP_K) -> list[RetrievedDoc]:
        """Recupera os top_k docs mais relevantes. Funciona em qualquer modo."""
        if self.mode == "hibrido":
            fused = self._rrf([self._bm25_ranking(query), self._dense_ranking(query)])
            ordered = sorted(fused, key=lambda i: fused[i], reverse=True)
            scored = [(i, fused[i]) for i in ordered]
            scored = self._maybe_rerank(query, scored)
        else:
            scores = self._bm25.get_scores(_tokenize(query))
            ordered = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)
            scored = [(i, float(scores[i])) for i in ordered]

        out = []
        for i, sc in scored[:top_k]:
            d = self.docs[i]
            out.append(RetrievedDoc(id=d["id"], title=d["title"], content=d["content"],
                                    steps=d.get("steps", []), score=round(float(sc), 4)))
        return out

    def _maybe_rerank(self, query, scored):
        """Re-rank cross-encoder sobre os candidatos do RRF, se disponível."""
        if not self._cross_encoder:
            return scored
        cand = scored[:RRF_CANDIDATES]
        pairs = [(query, _doc_text(self.docs[i])) for i, _ in cand]
        ce = self._cross_encoder.predict(pairs)
        reord = sorted(zip([i for i, _ in cand], ce), key=lambda t: float(t[1]), reverse=True)
        return [(i, float(s)) for i, s in reord]
