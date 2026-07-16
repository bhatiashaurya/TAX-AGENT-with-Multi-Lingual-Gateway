"""
Ingest a PDF into the RAG corpus as a cleaned markdown document.

This is the "proper" way to add a document as a knowledge source: extract the
text once, clean it, and commit the processed markdown into ``rag/corpus/`` so
it is indexed at startup like every other source. The large binary PDF itself is
NOT committed (see .gitignore) — only the extracted text.

Usage:
    python -m rag.ingest_pdf "<pdf_path>" \
        --title "Income-tax Rules, 2026" \
        --out rag/corpus/income_tax_rules_2026.md

For legal/rule documents it detects rule boundaries (validated by sequential
rule numbering, which suppresses false positives like "Form No. 24.") and
inserts a markdown heading before each rule so the chunker splits per rule.
"""
from __future__ import annotations

import argparse
import re
from pathlib import Path

# Repeated page footer in the CBDT PDF; strip from where it starts to page end.
_FOOTER = re.compile(
    r"Income Tax Department\s*Ministry of Finance,\s*Government of India.*$",
    re.S,
)
# Candidate rule start: "<Heading>. <n>. " — the heading is a capitalised phrase
# with no internal period; the number is 1-3 digits sandwiched by periods.
_RULE = re.compile(
    r"(?<![A-Za-z])([A-Z][A-Za-z0-9 ,;:()\-\[\]\"'/&]{4,140}?)\.\s+(\d{1,3})\.\s"
)


def extract(pdf_path: str) -> tuple[str, int]:
    from pypdf import PdfReader

    reader = PdfReader(pdf_path)
    parts: list[str] = []
    for page in reader.pages:
        try:
            text = page.extract_text() or ""
        except Exception:
            text = ""
        text = _FOOTER.sub("", text).strip()
        if text:
            parts.append(text)
    return "\n".join(parts), len(reader.pages)


def clean(text: str) -> str:
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\s*\n\s*", "\n", text)
    # Repair common PDF line-join artifacts (pypdf drops spaces at line wraps).
    # These two are safe for tax form codes (3CEB, 26AS are digit/upper, untouched):
    text = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", text)   # withinIndia -> within India
    text = re.sub(r"(?<=[;])(?=[A-Za-z(])", " ", text)  # rules;and -> rules; and
    return text.strip()


def add_rule_headings(text: str) -> tuple[str, int]:
    """Insert '## Rule N. Heading' at each real rule boundary.

    A candidate is accepted only if its number continues the sequence
    (current < n <= current + 3), which rejects cross-references and form
    numbers that happen to match the pattern.
    """
    out: list[str] = []
    last = 0
    current = 0
    count = 0
    for m in _RULE.finditer(text):
        n = int(m.group(2))
        if not (current < n <= current + 3):
            continue
        current = n
        count += 1
        out.append(text[last:m.start()])
        out.append(f"\n\n## Rule {n}. {m.group(1).strip()}\n\n")
        last = m.end()
    out.append(text[last:])
    return "".join(out), count


def main() -> None:
    ap = argparse.ArgumentParser(description="Ingest a PDF into the RAG corpus.")
    ap.add_argument("pdf", help="path to the source PDF")
    ap.add_argument("--title", required=True, help="document title (becomes the H1)")
    ap.add_argument("--out", required=True, help="output markdown path (rag/corpus/…)")
    ap.add_argument("--no-rules", action="store_true", help="skip rule-heading detection")
    args = ap.parse_args()

    raw, npages = extract(args.pdf)
    body = clean(raw)
    n_rules = 0
    if not args.no_rules:
        body, n_rules = add_rule_headings(body)

    md = (
        f"# {args.title}\n\n"
        f"_Source: {Path(args.pdf).name} · {npages} pages · extracted into the RAG corpus._\n\n"
        f"{body}\n"
    )
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(md, encoding="utf-8")
    print(
        f"Wrote {out}\n  {len(md):,} chars · {npages} pages · "
        f"{n_rules} rule headings detected"
    )


if __name__ == "__main__":
    main()
