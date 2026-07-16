"""RAG pipeline tests: chunking, hybrid retrieval, reranking, corpus grounding."""
from __future__ import annotations

from pathlib import Path

from rag.chunker import Document, chunk_document
from rag.retriever import Retriever, build_retriever
from rag.store import InMemoryVectorStore


def test_chunking_splits_on_headings_and_bounds_size():
    doc = Document(
        path="d.md", title="Doc",
        text="# A\n\n" + ("alpha " * 200) + "\n\n## B\n\n" + ("beta " * 200),
    )
    chunks = chunk_document(doc)
    assert len(chunks) >= 2
    # sections are preserved and no chunk is absurdly large
    assert any(c.section == "A" for c in chunks)
    assert any(c.section == "B" for c in chunks)
    assert all(len(c.text) < 2000 for c in chunks)


def test_inmemory_store_ranks_relevant_chunk_first():
    store = InMemoryVectorStore()
    docs = [
        Document(path="gst.md", title="GST", text="GST refund is filed in RFD-01 within two years."),
        Document(path="tds.md", title="TDS", text="TDS on rent under section 194I is deducted at ten percent."),
    ]
    store.add([c for d in docs for c in chunk_document(d)])
    results = store.search("how to file a GST refund", top_k=2)
    assert results
    assert results[0][0].doc_title == "GST"


def test_retriever_returns_grounding_with_scores():
    r = build_retriever()
    assert len(r.store) > 0
    hits = r.retrieve("What is the GST refund timeline?")
    assert hits
    assert all(0.0 <= h.score <= 1.0 for h in hits)
    # the top hit should come from a GST document
    assert "gst" in hits[0].path.lower() or "GST" in hits[0].source


def test_retriever_empty_on_irrelevant_query():
    r = Retriever()
    r.index_documents([Document(path="x.md", title="X", text="The sky is blue and grass is green.")])
    hits = r.retrieve("quantum chromodynamics lattice gauge theory")
    assert hits == []


def test_corpus_covers_core_topics():
    r = build_retriever()
    for query, expect in [
        ("transfer pricing arm's length method", "Transfer Pricing"),
        ("TDS rate for contractor 194C", "TDS"),
        ("section 143(1) intimation notice", "Notices"),
        ("customs duty basic customs duty import", "Customs"),
    ]:
        hits = r.retrieve(query)
        assert hits, f"no retrieval for {query!r}"
        assert any(expect.lower() in h.source.lower() for h in hits), f"{query!r} -> {[h.source for h in hits]}"


def test_rules_2026_ingested_and_retrievable():
    """The ingested Income-tax Rules, 2026 must be indexed and retrievable."""
    r = build_retriever()
    hits = r.retrieve("When do the Income-tax Rules 2026 come into force?")
    assert hits
    assert any("Rules, 2026" in h.source for h in hits)
    assert any("Rule 1" in h.section for h in hits)


def test_ingestion_contract_indexes_new_documents():
    """The user's future Tax Risk dataset maps onto Document; indexing is additive."""
    r = build_retriever()
    before = len(r.store)
    r.index_documents([
        Document(path="risk_ds.md", title="Risk Dataset",
                 text="# Custom Risk\n\nThe widget levy applies at 3% on gadget exports above 5 crore.")
    ])
    assert len(r.store) > before
    hits = r.retrieve("widget levy gadget exports")
    assert any(h.source == "Risk Dataset" for h in hits)
