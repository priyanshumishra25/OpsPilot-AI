"""Structure-aware chunking.

Markdown is first split on headings so every chunk carries its section path; each section is then
split with LangChain's RecursiveCharacterTextSplitter on paragraph/line/sentence boundaries.

Heading sectioning is done here rather than with LangChain's MarkdownHeaderTextSplitter because that
splitter rewrites whitespace, so its output is not an exact substring of the source. Citations rely on
exact character offsets into the normalised source text, which this module guarantees (and tests).
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

from langchain_text_splitters import RecursiveCharacterTextSplitter

from opspilot.rag.extraction import ExtractedDocument

_HEADING = re.compile(r"^(#{1,4})[ \t]+(.+?)[ \t]*#*[ \t]*$")
_FENCE = re.compile(r"^(```|~~~)")


@dataclass(frozen=True)
class Section:
    start: int  # offset of the section body in the source text
    end: int
    path: list[str]


def markdown_sections(text: str) -> list[Section]:
    """Split Markdown on ATX headings (h1-h4), ignoring headings inside fenced code blocks."""
    sections: list[Section] = []
    path: list[tuple[int, str]] = []
    body_start = 0
    in_fence = False
    offset = 0
    for line in text.splitlines(keepends=True):
        stripped = line.rstrip("\n")
        if _FENCE.match(stripped):
            in_fence = not in_fence
        m = None if in_fence else _HEADING.match(stripped)
        if m:
            if offset > body_start:
                sections.append(Section(body_start, offset, [h for _, h in path]))
            level = len(m.group(1))
            path = [(lvl, h) for lvl, h in path if lvl < level] + [(level, m.group(2).strip())]
            body_start = offset + len(line)
        offset += len(line)
    if offset > body_start:
        sections.append(Section(body_start, offset, [h for _, h in path]))
    return [s for s in sections if text[s.start : s.end].strip()]


@dataclass(frozen=True)
class ChunkDraft:
    ordinal: int
    content: str
    section_path: list[str]
    page_number: int | None
    char_start: int
    char_end: int
    content_hash: str
    token_estimate: int

    @property
    def section_heading(self) -> str | None:
        return self.section_path[-1] if self.section_path else None

    def embedding_text(self, title: str) -> str:
        """Text sent to the embedder: the passage prefixed with its document/section context."""
        context = " > ".join([title, *self.section_path]) if self.section_path else title
        return f"{context}\n\n{self.content}"


def estimate_tokens(text: str) -> int:
    # Heuristic (~4 chars/token for English). Used for budgeting only, never reported as usage.
    return max(1, len(text) // 4)


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class Chunker:
    def __init__(self, *, chunk_size: int, chunk_overlap: int, min_chunk_chars: int = 40) -> None:
        self._recursive = RecursiveCharacterTextSplitter(
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
            separators=["\n\n", "\n", ". ", " ", ""],
            add_start_index=True,
        )
        self._min = min_chunk_chars

    def chunk(self, doc: ExtractedDocument) -> list[ChunkDraft]:
        drafts: list[ChunkDraft] = []
        for page in doc.pages:
            sections = markdown_sections(page.text) if doc.is_markdown else [Section(0, len(page.text), [])]
            for section in sections:
                body = page.text[section.start : section.end]
                for i, piece in enumerate(self._recursive.create_documents([body])):
                    text = piece.page_content
                    if i > 0 and len(text) < self._min:
                        continue  # tiny trailing fragment of a section already covered by the overlap
                    start = section.start + int(piece.metadata["start_index"])
                    drafts.append(
                        ChunkDraft(
                            ordinal=len(drafts),
                            content=text,
                            section_path=section.path,
                            page_number=page.page_number,
                            char_start=start,
                            char_end=start + len(text),
                            content_hash=content_hash(text),
                            token_estimate=estimate_tokens(text),
                        )
                    )
        return drafts
