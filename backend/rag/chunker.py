"""Markdown-aware document chunking.

Splits on headings first (a chunk should not straddle two topics), then packs
paragraphs into size-bounded chunks with overlap so retrieval never loses the
sentence that happened to sit on a boundary.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from config.settings import settings


@dataclass
class Document:
    """A source document to index. This is the ingestion contract — the
    user's Tax Risk Assessment dataset maps onto it without refactoring."""

    path: str
    title: str
    text: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class Chunk:
    chunk_id: str
    doc_path: str
    doc_title: str
    section: str
    text: str
    metadata: dict[str, Any] = field(default_factory=dict)


_HEADING = re.compile(r"^(#{1,4})\s+(.*)$", re.MULTILINE)


def _sections(doc: Document) -> list[tuple[str, str]]:
    """Split a markdown document into (section_title, body) pairs."""
    matches = list(_HEADING.finditer(doc.text))
    if not matches:
        return [(doc.title, doc.text)]
    sections: list[tuple[str, str]] = []
    preamble = doc.text[: matches[0].start()].strip()
    if preamble:
        sections.append((doc.title, preamble))
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(doc.text)
        body = doc.text[m.end(): end].strip()
        if body:
            sections.append((m.group(2).strip(), body))
    return sections


def chunk_document(doc: Document) -> list[Chunk]:
    max_chars = settings.RAG_CHUNK_CHARS
    overlap = settings.RAG_CHUNK_OVERLAP
    chunks: list[Chunk] = []

    for section, body in _sections(doc):
        paragraphs = [p.strip() for p in re.split(r"\n{2,}", body) if p.strip()]
        buf = ""
        for para in paragraphs:
            if buf and len(buf) + len(para) + 2 > max_chars:
                chunks.append(_make(doc, section, buf, len(chunks)))
                # start next chunk with the tail of the previous one (overlap)
                buf = buf[-overlap:].lstrip() + "\n\n" if overlap else ""
            buf += ("\n\n" if buf else "") + para
            # a single paragraph larger than max_chars is split hard
            while len(buf) > max_chars * 1.5:
                chunks.append(_make(doc, section, buf[:max_chars], len(chunks)))
                buf = buf[max_chars - overlap:]
        if buf.strip():
            chunks.append(_make(doc, section, buf, len(chunks)))
    return chunks


def _make(doc: Document, section: str, text: str, index: int) -> Chunk:
    return Chunk(
        chunk_id=f"{doc.path}#{index}",
        doc_path=doc.path,
        doc_title=doc.title,
        section=section,
        text=text.strip(),
        metadata=doc.metadata,
    )
