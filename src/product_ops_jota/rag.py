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
import os
import re
import unicodedata
from pathlib import Path

from pydantic import BaseModel, Field

logger = logging.getLogger("product_ops_jota.rag")

# ─── POLICY (config nomeada no topo) ─────────────────────────────────────────
KB_PATH = Path(__file__).resolve().parents[2] / "data" / "knowledge_base" / "jota_kb.json"
TOP_K = 3                      # docs retornados por padrão
RRF_K = 60                     # constante da reciprocal rank fusion (padrão da literatura)
DENSE_MODEL = "paraphrase-multilingual-MiniLM-L12-v2"   # pequeno, multilíngue (local)
CROSS_ENCODER = "cross-encoder/ms-marco-MiniLM-L-6-v2"  # re-rank opcional (local)
DENSE_API_MODEL = "text-embedding-3-small"             # densos HOSPEDADOS (sem torch no container)
RRF_CANDIDATES = 8             # quantos candidatos cada camada manda pra fusão


class RetrievedDoc(BaseModel):
    id: str
    title: str
    content: str
    steps: list[str] = Field(default_factory=list)
    score: float                      # score final (RRF ou BM25, conforme o modo)
    requires_human: bool = False      # a KB diz que ESTE procedimento precisa de humano (privilégio)


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
        self._dense_model = None      # modelo local (sentence-transformers), se houver
        self._oai = None              # cliente OpenAI (densos hospedados), se houver
        self._doc_embeddings = None
        self._cross_encoder = None
        self.mode = "bm25"
        motivo = self._try_load_dense()
        if self._doc_embeddings is not None:
            self.mode = "hibrido-api" if self._oai is not None else "hibrido"
            logger.info("RAG: modo %s (BM25 + densos%s)", self.mode,
                        " + rerank" if self._cross_encoder else "")
        else:
            logger.info("RAG: modo BM25 fallback (densos indisponíveis: %s)", motivo)

    def _try_load_dense(self) -> str:
        """Tenta habilitar densos. Devolve o motivo da falha (string vazia se ok).
        NUNCA levanta — o chamador segue com BM25.
          · JOTA_RAG_MODE=bm25   → força leve (sem densos)
          · JOTA_RAG_MODE=openai → densos HOSPEDADOS (sem torch, cabe em RAM pequena)
          · (padrão)             → densos LOCAIS (sentence-transformers), se instalados"""
        mode = os.environ.get("JOTA_RAG_MODE", "").lower()
        if mode == "bm25":
            return "forçado BM25 (JOTA_RAG_MODE=bm25) — deploy leve, sem torch"
        if mode == "openai":
            return self._load_openai_dense()
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

    def _load_openai_dense(self) -> str:
        """Densos HOSPEDADOS (embeddings da OpenAI): qualidade híbrida SEM torch no
        container — worker leve + embedder gerenciado (arquitetura de produção real).
        NUNCA levanta; sem key/rede, o chamador cai no BM25."""
        try:
            from openai import OpenAI
            self._oai = OpenAI()                                  # key vem do ambiente
            self._doc_embeddings = self._oai_embed([_doc_text(d) for d in self.docs])
        except Exception as e:
            self._oai = None
            self._doc_embeddings = None
            return f"openai embeddings indisponível ({type(e).__name__})"
        return ""

    def _oai_embed(self, texts):
        """Embeddings hospedados, normalizados (p/ cosseno). Uma chamada por lote."""
        import numpy as np
        resp = self._oai.embeddings.create(model=DENSE_API_MODEL, input=list(texts))
        vecs = np.array([d.embedding for d in resp.data], dtype="float32")
        norms = np.linalg.norm(vecs, axis=1, keepdims=True)
        return vecs / np.clip(norms, 1e-9, None)

    def doc_similarity(self, query: str, doc) -> float | None:
        """Cosseno (0–1) entre a query e um doc específico — o sinal CALIBRADO pro gate de
        relevância estrito (fail-safe > fail-loud). None em BM25 (sem densos): o chamador cai no juiz."""
        if self._doc_embeddings is None or doc is None:
            return None
        try:
            idx = next((i for i, d in enumerate(self.docs) if d["id"] == doc.id), None)
            if idx is None:
                return None
            q = self._encode([query])[0]
            return float(self._doc_embeddings[idx] @ q)
        except Exception:
            return None

    def _encode(self, texts):
        """Embeddings normalizados p/ cosseno — dispatch local vs hospedado.
        None em BM25 puro (o chamador cai no keyword)."""
        if self._dense_model is not None:
            return self._dense_model.encode(list(texts), normalize_embeddings=True)
        if self._oai is not None:
            return self._oai_embed(texts)
        return None

    # ── ranking helpers ──────────────────────────────────────────────────────
    def _bm25_ranking(self, query: str) -> list[int]:
        scores = self._bm25.get_scores(_tokenize(query))
        return sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)

    def _dense_ranking(self, query: str) -> list[int]:
        q = self._encode([query])[0]
        sims = self._doc_embeddings @ q          # cosseno (já normalizado)
        return sorted(range(len(sims)), key=lambda i: float(sims[i]), reverse=True)

    @staticmethod
    def _rrf(rankings: list[list[int]]) -> dict[int, float]:
        fused: dict[int, float] = {}
        for ranking in rankings:
            for rank, idx in enumerate(ranking[:RRF_CANDIDATES]):
                fused[idx] = fused.get(idx, 0.0) + 1.0 / (RRF_K + rank + 1)
        return fused

    def embed(self, texts):
        """Embeddings normalizados (p/ classificação semântica por cosseno). None se
        rodando em modo BM25 (densos indisponíveis) — o chamador cai no keyword."""
        return self._encode(list(texts))

    def retrieve(self, query: str, top_k: int = TOP_K) -> list[RetrievedDoc]:
        """Recupera os top_k docs mais relevantes. Funciona em qualquer modo."""
        if self._doc_embeddings is not None:          # híbrido (densos locais OU hospedados)
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
                                    steps=d.get("steps", []), score=round(float(sc), 4),
                                    requires_human=d.get("requires_human", False)))
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
