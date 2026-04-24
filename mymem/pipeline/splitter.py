"""
Chunk splitter — divides long documents that exceed a model's context window,
then merges the partial results with a larger model.

Flow:
    1. ChunkSplitter.split(text)  → list of overlapping text chunks
    2. Router compiles each chunk independently  → list of partial wiki pages
    3. ChunkSplitter.merge_prompt(partials)  → single merge prompt for the merge model
    4. Router calls merge model  → final unified wiki page body
"""

from __future__ import annotations

import math


# ---------------------------------------------------------------------------
# ChunkSplitter
# ---------------------------------------------------------------------------

class ChunkSplitter:
    """
    Splits long text into overlapping chunks sized for a target model.

    Args:
        max_tokens:  Maximum tokens per chunk (default 6 000 — safe for 8k models).
        overlap:     Overlap fraction between adjacent chunks (default 10%).
        chars_per_token: Chars-per-token ratio for estimation (default 4).
    """

    def __init__(
        self,
        max_tokens: int = 6_000,
        overlap: float = 0.10,
        chars_per_token: int = 4,
    ) -> None:
        if not 0.0 <= overlap < 1.0:
            raise ValueError("overlap must be in [0, 1)")
        self._max_chars = max_tokens * chars_per_token
        self._overlap   = overlap

    def split(self, text: str) -> list[str]:
        """
        Split text into overlapping chunks.

        Returns a list with at least one element. If the text fits in one
        chunk, returns [text] unchanged.
        """
        if len(text) <= self._max_chars:
            return [text]

        step = int(self._max_chars * (1 - self._overlap))
        if step <= 0:
            step = self._max_chars

        chunks: list[str] = []
        start = 0
        while start < len(text):
            end = min(start + self._max_chars, len(text))
            chunk = text[start:end]
            # Prefer to break on a paragraph boundary rather than mid-sentence
            if end < len(text):
                para_break = chunk.rfind("\n\n")
                sent_break = chunk.rfind(". ")
                break_at = max(para_break, sent_break)
                if break_at > self._max_chars // 2:
                    end = start + break_at + 1
                    chunk = text[start:end]
            chunks.append(chunk.strip())
            start += step
            if start >= len(text):
                break

        return [c for c in chunks if c]

    @property
    def chunk_count(self) -> int:
        """Estimated number of chunks for a given text length (informational)."""
        return 0  # only meaningful after split() is called

    def estimated_chunks(self, text: str) -> int:
        if len(text) <= self._max_chars:
            return 1
        step = max(1, int(self._max_chars * (1 - self._overlap)))
        return math.ceil(len(text) / step)


# ---------------------------------------------------------------------------
# Merge prompt builder
# ---------------------------------------------------------------------------

_MERGE_SYSTEM = """\
You are a wiki editor. You will receive several partial wiki page drafts compiled
from different chunks of the same source document. Your job is to merge them into
a single, coherent wiki page in markdown with YAML frontmatter.

Rules:
- Preserve all unique information from every partial draft.
- Remove duplicate content — keep the best-worded version.
- Maintain Obsidian [[wikilink]] style for cross-references.
- Keep the YAML frontmatter fields: title, domain, tags, sources.
- Output only the final merged wiki page — no commentary.
"""


def merge_prompt(partials: list[str], title: str = "") -> str:
    """
    Build the prompt sent to the merge model to combine partial wiki pages.

    Args:
        partials: List of partial wiki page bodies from each chunk compilation.
        title:    Optional title hint for the merged page.
    """
    header = f"Merge these {len(partials)} partial wiki drafts into one coherent page."
    if title:
        header += f" The page title is: {title}"

    sections = [
        f"--- PARTIAL {i + 1} ---\n{p.strip()}"
        for i, p in enumerate(partials)
    ]
    return header + "\n\n" + "\n\n".join(sections)


def merge_system_prompt() -> str:
    return _MERGE_SYSTEM
