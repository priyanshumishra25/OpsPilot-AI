from pathlib import Path

import pytest

from opspilot.errors import ExtractionError, ValidationFailedError
from opspilot.rag.chunking import Chunker, markdown_sections
from opspilot.rag.extraction import detect_mime_type, extract, normalise_text, sanitize_filename
from tests.fakes import make_pdf

DOCS = Path(__file__).resolve().parents[3] / "demo" / "docs"


def test_mime_detection_rejects_spoofed_and_unsupported() -> None:
    assert detect_mime_type("a.md", b"# hi") == "text/markdown"
    assert detect_mime_type("a.pdf", make_pdf(["x"])) == "application/pdf"
    with pytest.raises(ValidationFailedError):
        detect_mime_type("evil.pdf", b"#!/bin/sh\nrm -rf /")
    with pytest.raises(ValidationFailedError):
        detect_mime_type("a.exe", b"MZ")
    with pytest.raises(ValidationFailedError):
        detect_mime_type("a.txt", b"\x00\x01binary")
    with pytest.raises(ValidationFailedError):
        detect_mime_type("a.txt", "caf\u00e9".encode("latin-1"))


def test_sanitize_filename_strips_paths() -> None:
    assert sanitize_filename("../../etc/passwd") == "passwd"
    assert sanitize_filename("C:\\Users\\x\\run book.md") == "run book.md"
    assert sanitize_filename("<script>.md") == "_script_.md"


def test_normalise_text_removes_control_chars() -> None:
    assert normalise_text("a\r\nb\x07c\u200b\n\n\n\n\nd") == "a\nbc\n\n\nd"


def test_markdown_sections_ignore_headings_in_code_fences() -> None:
    text = "# Title\nintro\n```bash\n# not a heading\n```\n## Steps\n1. do it\n"
    sections = markdown_sections(text)
    assert [s.path for s in sections] == [["Title"], ["Title", "Steps"]]
    assert "# not a heading" in text[sections[0].start : sections[0].end]


@pytest.mark.parametrize("path", sorted(DOCS.glob("*.md")), ids=lambda p: p.name)
def test_chunk_offsets_are_exact_for_corpus(path: Path) -> None:
    data = path.read_bytes()
    doc = extract(path.name, data, detect_mime_type(path.name, data), max_pdf_pages=10)
    chunks = Chunker(chunk_size=500, chunk_overlap=80).chunk(doc)
    assert chunks
    text = doc.pages[0].text
    for c in chunks:
        assert text[c.char_start : c.char_end] == c.content
        assert c.section_path, "markdown chunks carry their heading path"
    assert [c.ordinal for c in chunks] == list(range(len(chunks)))


def test_long_section_is_split_with_overlap() -> None:
    body = " ".join(f"Sentence number {i} explains a step." for i in range(200))
    doc = extract("long.md", f"# Long\n\n{body}".encode(), "text/markdown", max_pdf_pages=10)
    chunks = Chunker(chunk_size=400, chunk_overlap=80).chunk(doc)
    assert len(chunks) > 5
    assert all(len(c.content) <= 400 for c in chunks)
    assert all(c.section_path == ["Long"] for c in chunks)


def test_pdf_extraction_preserves_page_numbers() -> None:
    pdf = make_pdf(["Rollback requires approval.", "Pool size is twenty."])
    doc = extract("guide.pdf", pdf, "application/pdf", max_pdf_pages=10)
    assert [p.page_number for p in doc.pages] == [1, 2]
    chunks = Chunker(chunk_size=500, chunk_overlap=50).chunk(doc)
    assert [c.page_number for c in chunks] == [1, 2]


def test_pdf_page_limit_and_garbage() -> None:
    with pytest.raises(ExtractionError):
        extract("big.pdf", make_pdf(["a", "b", "c"]), "application/pdf", max_pdf_pages=2)
    with pytest.raises(ExtractionError):
        extract("bad.pdf", b"%PDF-1.4\ngarbage", "application/pdf", max_pdf_pages=2)


def test_empty_text_document_rejected() -> None:
    with pytest.raises(ExtractionError):
        extract("e.md", b"   \n\n", "text/markdown", max_pdf_pages=1)
