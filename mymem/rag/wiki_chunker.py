"""
Wiki page chunker — splits markdown wiki pages into parent-child chunks
for RAG indexing.

Strategy:
  1. Strip YAML frontmatter → extract title, domain, tags
  2. MarkdownHeaderTextSplitter splits on #/##/### headings → sections
  3. Each section text = parent chunk (full section, up to 4096 chars)
  4. RecursiveCharacterTextSplitter splits each section into child chunks (~300 tokens)
  5. Embed text per child: "{page_title} > {heading_path}: {child_text}"
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

from mymem.observability.logger import get_logger
from mymem.wiki.types import slugify

log = get_logger(__name__)

_CHILD_CHUNK_SIZE    = 1200   # chars ≈ 300 tokens at 4 chars/token
_CHILD_CHUNK_OVERLAP = 120    # chars ≈ 30 tokens overlap
_PARENT_MAX_CHARS    = 4096   # truncate parent to ≈ 1024 tokens

_HEADERS_TO_SPLIT_ON = [
    ("#",   "h1"),
    ("##",  "h2"),
    ("###", "h3"),
]


@dataclass(frozen=True)
class WikiChunk:
    source_path:  str
    source_slug:  str
    chunk_index:  int
    text:         str   # child chunk text stored in DB
    embed_text:   str   # prefixed text sent to embedder
    parent_text:  str   # full heading section returned to LLM at query time
    heading_path: str
    chunk_type:   str   # always "child"
    page_title:   str
    domain:       str
    tags:         str


def _extract_frontmatter(content: str) -> tuple[dict[str, str], str]:
    """Strip YAML frontmatter block, return (metadata_dict, body_text)."""
    if not content.startswith("---"):
        return {}, content
    end = content.find("\n---", 3)
    if end == -1:
        return {}, content
    fm_block = content[3:end].strip()
    body = content[end + 4:].lstrip()

    try:
        raw: dict[str, object] = yaml.safe_load(fm_block) or {}
    except yaml.YAMLError as exc:
        log.warning("wiki_chunker: invalid YAML frontmatter — skipping", error=str(exc))
        return {}, body

    # Normalise tags list → comma-separated string
    tags = raw.get("tags", [])
    if isinstance(tags, list):
        tags_str = ",".join(str(t) for t in tags)
    else:
        tags_str = str(tags) if tags else ""

    meta: dict[str, str] = {k: str(v) for k, v in raw.items() if v is not None}
    meta["tags"] = tags_str
    return meta, body


def _build_heading_path(metadata: dict[str, str]) -> str:
    """Build 'H1 > H2 > H3' path from MarkdownHeaderTextSplitter metadata."""
    parts = [v for v in metadata.values() if v]
    return " > ".join(parts)


def chunk_wiki_page(page_path: Path) -> list[WikiChunk]:
    """
    Split a wiki markdown page into parent-child RAG chunks.

    Returns [] on missing file, empty body, or any unexpected error.
    Never raises.
    """
    try:
        content = page_path.read_text(encoding="utf-8")
    except OSError as exc:
        log.warning("wiki_chunker: cannot read page", path=str(page_path), error=str(exc))
        return []

    fm, body = _extract_frontmatter(content)
    page_title  = fm.get("title", page_path.stem)
    domain      = fm.get("domain", "misc")
    tags        = fm.get("tags", "")
    source_path = str(page_path.resolve())
    source_slug = slugify(page_title)

    if not body.strip():
        log.debug("wiki_chunker: empty body — skipping", path=str(page_path))
        return []

    try:
        from langchain_text_splitters import (
            MarkdownHeaderTextSplitter,
            RecursiveCharacterTextSplitter,
        )
    except ImportError:
        log.error(
            "wiki_chunker: langchain-text-splitters not installed. "
            "Run: pip install langchain-text-splitters"
        )
        return []

    # 1. Split by headings — keep header text in section body for parent context
    header_splitter = MarkdownHeaderTextSplitter(
        headers_to_split_on=_HEADERS_TO_SPLIT_ON,
        strip_headers=False,
    )
    sections = header_splitter.split_text(body)

    if not sections:
        sections_data: list[tuple[str, str]] = [("", body)]
    else:
        sections_data = [
            (_build_heading_path(dict(s.metadata)), s.page_content)
            for s in sections
        ]

    # 2. Split each section into child chunks
    child_splitter = RecursiveCharacterTextSplitter(
        chunk_size=_CHILD_CHUNK_SIZE,
        chunk_overlap=_CHILD_CHUNK_OVERLAP,
        separators=["\n\n", "\n", ". ", " ", ""],
    )

    chunks: list[WikiChunk] = []
    chunk_index = 0

    for heading_path, section_text in sections_data:
        section_text = section_text.strip()
        if not section_text:
            continue

        parent_text = section_text[:_PARENT_MAX_CHARS]
        child_texts = child_splitter.split_text(section_text) or [section_text[:_CHILD_CHUNK_SIZE]]

        for child_text in child_texts:
            child_text = child_text.strip()
            if not child_text:
                continue

            prefix = (
                f"{page_title} > {heading_path}: "
                if heading_path
                else f"{page_title}: "
            )
            chunks.append(WikiChunk(
                source_path=source_path,
                source_slug=source_slug,
                chunk_index=chunk_index,
                text=child_text,
                embed_text=prefix + child_text,
                parent_text=parent_text,
                heading_path=heading_path,
                chunk_type="child",
                page_title=page_title,
                domain=domain,
                tags=tags,
            ))
            chunk_index += 1

    log.debug(
        "wiki_chunker: page chunked",
        path=str(page_path),
        sections=len(sections_data),
        chunks=len(chunks),
    )
    return chunks
