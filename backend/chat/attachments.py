"""Attachment text extraction for chat context.

Supports txt/md/csv/json/log directly and PDF via pypdf when installed. Extracted
text is truncated and folded into the user turn so the model can reason over it.
"""
from __future__ import annotations

import io
from dataclasses import dataclass

from config.settings import settings

_TEXT_EXT = {".txt", ".md", ".csv", ".json", ".log", ".tsv", ".yaml", ".yml"}
_MAX_EXTRACT_CHARS = 6000


@dataclass
class ExtractedAttachment:
    filename: str
    kind: str
    chars: int
    text: str
    truncated: bool


class AttachmentError(Exception):
    pass


def extract(filename: str, data: bytes, content_type: str = "") -> ExtractedAttachment:
    if len(data) > settings.max_attachment_bytes:
        raise AttachmentError(
            f"Attachment exceeds {settings.MAX_ATTACHMENT_MB} MB limit "
            f"({len(data) // 1024} KB provided)."
        )
    ext = ("." + filename.rsplit(".", 1)[-1].lower()) if "." in filename else ""

    if ext == ".pdf" or content_type == "application/pdf":
        text = _extract_pdf(data)
        kind = "pdf"
    elif ext in _TEXT_EXT or content_type.startswith("text/"):
        text = data.decode("utf-8", errors="replace")
        kind = ext.lstrip(".") or "text"
    else:
        raise AttachmentError(
            f"Unsupported attachment type '{ext or content_type or 'unknown'}'. "
            "Supported: txt, md, csv, json, log, pdf."
        )

    truncated = len(text) > _MAX_EXTRACT_CHARS
    return ExtractedAttachment(
        filename=filename, kind=kind, chars=len(text),
        text=text[:_MAX_EXTRACT_CHARS], truncated=truncated,
    )


def _extract_pdf(data: bytes) -> str:
    try:
        from pypdf import PdfReader
    except ImportError as e:  # pragma: no cover - env dependent
        raise AttachmentError("PDF support requires the 'pypdf' package.") from e
    try:
        reader = PdfReader(io.BytesIO(data))
        pages = [page.extract_text() or "" for page in reader.pages[:20]]
        return "\n\n".join(pages).strip() or "[No extractable text in PDF]"
    except Exception as e:
        raise AttachmentError(f"Could not read PDF: {e}") from e


def fold_into_prompt(user_text: str, attachments: list[ExtractedAttachment]) -> str:
    if not attachments:
        return user_text
    blocks = []
    for att in attachments:
        note = " (truncated)" if att.truncated else ""
        blocks.append(f'<attachment name="{att.filename}"{note}>\n{att.text}\n</attachment>')
    return user_text + "\n\n" + "\n".join(blocks)
