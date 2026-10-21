"""File validation and text extraction into a normalised, section-aware representation."""

from __future__ import annotations

import io
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import PurePosixPath

from pypdf import PdfReader
from pypdf.errors import PdfReadError

from opspilot.errors import ExtractionError, ValidationFailedError

SUPPORTED_TYPES: dict[str, str] = {
    ".md": "text/markdown",
    ".markdown": "text/markdown",
    ".txt": "text/plain",
    ".pdf": "application/pdf",
}


@dataclass(frozen=True)
class ExtractedPage:
    text: str
    page_number: int | None  # 1-indexed for PDFs; None for text formats


@dataclass(frozen=True)
class ExtractedDocument:
    title: str
    mime_type: str
    pages: list[ExtractedPage] = field(default_factory=list)

    @property
    def is_markdown(self) -> bool:
        return self.mime_type == "text/markdown"


def detect_mime_type(filename: str, data: bytes) -> str:
    """Resolve type from extension and verify the content agrees. Client-sent content types are ignored."""
    suffix = PurePosixPath(filename.lower()).suffix
    mime = SUPPORTED_TYPES.get(suffix)
    if mime is None:
        raise ValidationFailedError(
            f"Unsupported file type '{suffix or filename}'", details={"supported": sorted(SUPPORTED_TYPES)}
        )
    if mime == "application/pdf":
        if not data.startswith(b"%PDF-"):
            raise ValidationFailedError("File has a .pdf extension but is not a PDF")
    else:
        if b"\x00" in data[:8192]:
            raise ValidationFailedError("Text file contains binary content")
        try:
            data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValidationFailedError("Text files must be UTF-8 encoded") from exc
    return mime


def sanitize_filename(filename: str) -> str:
    name = PurePosixPath(filename.replace("\\", "/")).name
    name = re.sub(r"[^A-Za-z0-9._ -]", "_", name).strip(" .")
    return name[:200] or "upload"


def normalise_text(text: str) -> str:
    text = unicodedata.normalize("NFC", text).replace("\r\n", "\n").replace("\r", "\n")
    text = "".join(ch for ch in text if ch in "\n\t" or unicodedata.category(ch)[0] != "C")
    text = re.sub(r"[ \t]+\n", "\n", text)
    return re.sub(r"\n{4,}", "\n\n\n", text).strip()


def _markdown_title(text: str) -> str | None:
    m = re.search(r"^#\s+(.+?)\s*#*\s*$", text, re.M)
    return m.group(1).strip() if m else None


def extract(filename: str, data: bytes, mime_type: str, *, max_pdf_pages: int) -> ExtractedDocument:
    stem = PurePosixPath(filename).stem.replace("_", " ").replace("-", " ").strip() or filename
    if mime_type == "application/pdf":
        return _extract_pdf(data, stem, max_pdf_pages)
    text = normalise_text(data.decode("utf-8"))
    if not text:
        raise ExtractionError("Document contains no text")
    title = (_markdown_title(text) if mime_type == "text/markdown" else None) or stem
    return ExtractedDocument(title=title[:500], mime_type=mime_type, pages=[ExtractedPage(text, None)])


def _extract_pdf(data: bytes, stem: str, max_pages: int) -> ExtractedDocument:
    try:
        reader = PdfReader(io.BytesIO(data), strict=False)
        if reader.is_encrypted:
            raise ExtractionError("Encrypted PDFs are not supported")
        if len(reader.pages) > max_pages:
            raise ExtractionError(f"PDF has {len(reader.pages)} pages; limit is {max_pages}")
        pages = []
        for i, page in enumerate(reader.pages, start=1):
            text = normalise_text(page.extract_text() or "")
            if text:
                pages.append(ExtractedPage(text, i))
        meta_title = (reader.metadata.title if reader.metadata else None) or None
    except PdfReadError as exc:
        raise ExtractionError(f"Could not parse PDF: {exc}") from exc
    if not pages:
        raise ExtractionError("PDF contains no extractable text (scanned PDFs need OCR, which is not supported)")
    title = (meta_title or "").strip() or stem
    return ExtractedDocument(title=title[:500], mime_type="application/pdf", pages=pages)
