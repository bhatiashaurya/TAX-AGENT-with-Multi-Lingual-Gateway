"""Retrieval orchestration: search -> rerank -> grounding chunks + confidence."""
from __future__ import annotations

from pathlib import Path

from config.settings import settings
from llm.base import GroundingChunk
from rag.chunker import Document, chunk_document
from rag.store import InMemoryVectorStore, VectorStore, tokenize


class Retriever:
    def __init__(self, store: VectorStore | None = None) -> None:
        self.store = store or InMemoryVectorStore()

    # ------------------------------------------------------------------ #
    # Indexing
    # ------------------------------------------------------------------ #
    def index_documents(self, docs: list[Document]) -> int:
        chunks = [c for doc in docs for c in chunk_document(doc)]
        self.store.add(chunks)
        return len(chunks)

    def index_directory(self, directory: Path) -> int:
        """Index every .md/.txt file under ``directory``.

        Drop the Tax Risk Assessment dataset (or any corpus) in here and
        restart — no code changes needed.
        """
        docs: list[Document] = []
        if not directory.is_dir():
            return 0
        for path in sorted(directory.rglob("*")):
            if path.suffix.lower() not in (".md", ".txt"):
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            title = _title_of(text) or path.stem.replace("_", " ").title()
            docs.append(Document(path=str(path.relative_to(directory)), title=title, text=text))
        return self.index_documents(docs)

    # ------------------------------------------------------------------ #
    # Retrieval
    # ------------------------------------------------------------------ #
    def retrieve(self, query: str, history: str = "") -> list[GroundingChunk]:
        """Two-stage retrieval: hybrid search, then rerank.

        The rerank stage rewards query-term coverage and penalises chunks that
        only matched on background vocabulary — a cheap stand-in for a
        cross-encoder that keeps the POC dependency-free.
        """
        expanded = query if not history else f"{query} {history}"
        candidates = self.store.search(expanded, settings.RAG_TOP_K)
        if not candidates:
            return []

        q_terms = set(tokenize(query))
        reranked: list[tuple[float, GroundingChunk]] = []
        for chunk, score in candidates:
            c_terms = set(tokenize(chunk.text))
            coverage = len(q_terms & c_terms) / max(1, len(q_terms))
            title_bonus = 0.15 if q_terms & set(tokenize(chunk.doc_title + " " + chunk.section)) else 0.0
            final = 0.6 * score + 0.35 * coverage + title_bonus
            reranked.append(
                (
                    final,
                    GroundingChunk(
                        chunk_id=chunk.chunk_id,
                        source=chunk.doc_title,
                        path=chunk.doc_path,
                        section=chunk.section,
                        text=chunk.text,
                        score=round(min(1.0, final), 3),
                    ),
                )
            )
        reranked.sort(key=lambda r: -r[0])
        kept = [g for score, g in reranked[: settings.RAG_RERANK_TOP_N] if score >= settings.RAG_MIN_SCORE]
        return kept


def _title_of(text: str) -> str | None:
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("# "):
            return line[2:].strip()
        if line:
            break
    return None


def build_retriever() -> Retriever:
    retriever = Retriever()
    corpus_dir = Path(__file__).resolve().parent.parent / settings.RAG_CORPUS_DIR
    retriever.index_directory(corpus_dir)
    return retriever
