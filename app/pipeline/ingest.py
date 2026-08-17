"""Document intake.

The PDF is read two ways, and both are used. The structured pass sees the file
itself, because the layout information that makes a table readable is where most
of a CIM's financial content lives. The citation pass sees pypdf's per-page
text, because provenance means naming a page — and because that same text is
what a quote is later verified against.

Validation here is deliberately strict about one thing: a PDF with no
extractable text cannot be verified against, so it is rejected rather than
processed into a memo whose every figure would be uncitable.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from pathlib import Path

from pypdf.errors import PyPdfError

from app.llm.client import CachedDocument


@dataclass
class IngestedDoc:
    path: Path
    doc: CachedDocument
    page_count: int
    page_text: list[str]

    @property
    def full_text(self) -> str:
        return "\n\n".join(self.page_text)

    def find_page(self, needle: str) -> int | None:
        """1-indexed page containing `needle`, for rule-engine citations."""
        low = needle.lower()
        for i, t in enumerate(self.page_text, 1):
            if low in t.lower():
                return i
        return None


def run(path: str | Path) -> IngestedDoc:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"No such document: {p}")
    if p.suffix.lower() != ".pdf":
        raise ValueError(f"Expected a PDF, got {p.suffix}")

    # pypdf raises a family of PyPdfError subclasses on malformed input, and can
    # also surface plain OSError/struct errors on truncated files. Callers (CLI,
    # web, eval) all handle ValueError, so normalise here rather than leaking a
    # library-specific type through three layers.
    #
    # CachedDocument does the pypdf read; reusing its page text rather than
    # extracting a second copy is what guarantees the text a quote is verified
    # against is byte-for-byte the text the model was shown.
    try:
        doc = CachedDocument(p)
    except (PyPdfError, OSError, ValueError, struct.error) as e:
        raise ValueError(f"{p.name} is not a readable PDF: {e}") from e
    if not any(t.strip() for t in doc.page_text):
        raise ValueError(
            f"{p.name} has no extractable text — scanned/image-only PDFs are not supported"
        )

    return IngestedDoc(
        path=p,
        doc=doc,
        page_count=doc.page_count,
        page_text=doc.page_text,
    )
