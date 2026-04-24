"""
Input validators — Pydantic models for every system boundary.

Validates CLI args, ingest requests, and Q&A queries before
any processing begins. Prevents path traversal, oversized payloads,
and malformed inputs from propagating into the pipeline.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Annotated, Literal
from urllib.parse import urlparse

from pydantic import BaseModel, Field, field_validator, model_validator


# ---------------------------------------------------------------------------
# Shared constraints
# ---------------------------------------------------------------------------

_MAX_TITLE_LEN = 200
_MAX_QUERY_LEN = 2_000
_MAX_TAG_LEN = 50
_MAX_TAGS = 20
_SAFE_FILENAME_RE = re.compile(r"^[A-Za-z0-9_\-. ]+$")
_ALLOWED_URL_SCHEMES = {"http", "https"}
_ALLOWED_INGEST_EXTENSIONS = {
    ".md", ".txt", ".pdf", ".html", ".htm", ".rst",
    ".ipynb", ".json", ".csv",
}


# ---------------------------------------------------------------------------
# Ingest request
# ---------------------------------------------------------------------------

class IngestRequest(BaseModel):
    """Validates a request to add a new source document."""

    source: str = Field(..., description="File path or URL to ingest")
    source_type: Literal["article", "paper", "repo", "dataset", "image"] = "article"
    tags: list[Annotated[str, Field(max_length=_MAX_TAG_LEN)]] = Field(
        default_factory=list, max_length=_MAX_TAGS
    )
    title: str | None = Field(default=None, max_length=_MAX_TITLE_LEN)

    @field_validator("source")
    @classmethod
    def validate_source(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("source must not be empty")

        # URL path
        if v.startswith(("http://", "https://")):
            parsed = urlparse(v)
            if parsed.scheme not in _ALLOWED_URL_SCHEMES:
                raise ValueError(f"URL scheme '{parsed.scheme}' not allowed (http/https only)")
            if not parsed.netloc:
                raise ValueError("URL has no host")
            return v

        # File path — prevent traversal
        path = Path(v).resolve()
        try:
            # Must be relative to cwd or an absolute path — no .. escapes
            path.relative_to(Path.cwd())
        except ValueError:
            # Absolute paths outside cwd are allowed if they exist
            if not path.exists():
                raise ValueError(f"File not found: {v}")

        if path.suffix.lower() not in _ALLOWED_INGEST_EXTENSIONS:
            raise ValueError(
                f"File type '{path.suffix}' not supported. "
                f"Allowed: {', '.join(sorted(_ALLOWED_INGEST_EXTENSIONS))}"
            )

        return str(path)

    @field_validator("tags", mode="before")
    @classmethod
    def clean_tags(cls, v: list[str]) -> list[str]:
        return [tag.strip().lower() for tag in v if tag.strip()]


# ---------------------------------------------------------------------------
# Q&A query
# ---------------------------------------------------------------------------

class QAQuery(BaseModel):
    """Validates a research Q&A query."""

    question: str = Field(..., min_length=3, max_length=_MAX_QUERY_LEN)
    top_k: int = Field(default=5, ge=1, le=20)
    language_filter: str | None = Field(default=None, max_length=20)

    @field_validator("question")
    @classmethod
    def strip_question(cls, v: str) -> str:
        return v.strip()


# ---------------------------------------------------------------------------
# Wiki article reference
# ---------------------------------------------------------------------------

class ArticleRef(BaseModel):
    """Validates a reference to a wiki article by title or path."""

    title: str = Field(..., min_length=1, max_length=_MAX_TITLE_LEN)

    @field_validator("title")
    @classmethod
    def validate_title(cls, v: str) -> str:
        v = v.strip()
        # Prevent path traversal via title (titles become filenames)
        if ".." in v or "/" in v or "\\" in v:
            raise ValueError("Title must not contain path separators or '..'")
        if not _SAFE_FILENAME_RE.match(v):
            raise ValueError(
                "Title may only contain letters, numbers, spaces, hyphens, underscores, and dots"
            )
        return v


# ---------------------------------------------------------------------------
# Export request
# ---------------------------------------------------------------------------

class ExportRequest(BaseModel):
    """Validates a request to export a wiki article."""

    article: ArticleRef
    format: Literal["slides", "chart", "markdown"] = "markdown"
    output_dir: Path | None = None

    @model_validator(mode="after")
    def validate_output_dir(self) -> "ExportRequest":
        if self.output_dir is not None:
            resolved = self.output_dir.resolve()
            # Prevent writing outside the project directory
            try:
                resolved.relative_to(Path.cwd())
            except ValueError:
                raise ValueError(
                    f"output_dir must be inside the project directory, got: {self.output_dir}"
                )
        return self


# ---------------------------------------------------------------------------
# File size guard (used in ingest before reading content)
# ---------------------------------------------------------------------------

def check_file_size(path: Path, max_mb: int = 50) -> None:
    """Raise ValueError if file exceeds max_mb."""
    size_mb = path.stat().st_size / 1024**2
    if size_mb > max_mb:
        raise ValueError(
            f"File too large: {size_mb:.1f}MB exceeds limit of {max_mb}MB. "
            "Consider splitting it or increasing security.max_file_size_mb in config.yaml."
        )
