from datetime import datetime
from typing import Literal

from pydantic import BaseModel


class Span(BaseModel):
    """Offset-only span: code points into the raw text, never the text itself."""
    start: int
    end: int
    label: str
    source: Literal["scan", "slm", "human"]


class Record(BaseModel):
    doc_id: str
    thread_id: str
    raw_sha256: str
    redactions: list[Span]
    labels: list[Span]
    source_system: str
    reviewer: str | None
    reviewed_at: datetime | None
    model_version: str
    policy_version: str
