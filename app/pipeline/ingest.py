"""Document intake.

Deliberately thin: the PDF goes to the API as a document block rather than being
pre-OCR'd or chunked. Claude reads PDFs natively, and pre-extracting text throws
away the layout information that makes tables readable — which is where most of
the financial content in a CIM lives.

pypdf is used only for page counting and a text fallback used by the
deterministic rules engine, never as the extraction path.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from pypdf import PdfReader

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

    reader = PdfReader(str(p))
    pages = [(pg.extract_text() or "") for pg in reader.pages]
    if not any(t.strip() for t in pages):
        raise ValueError(
            f"{p.name} has no extractable text — scanned/image-only PDFs are not supported"
        )

    return IngestedDoc(
        path=p,
        doc=CachedDocument(p),
        page_count=len(reader.pages),
        page_text=pages,
    )
