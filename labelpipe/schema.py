from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ValidationInfo, model_validator


class Span(BaseModel):
    """Offset-only span: code points into the raw text, never the text itself."""
    start: int
    end: int
    label: str
    source: Literal["scan", "slm", "human"]
    evidence: str | None = None  # proposer's note; required when label == "EVIDENCE"


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
    needs_second_look: bool = False
    adjudication_reasons: list[str] = []

    @model_validator(mode="after")
    def _check_spans(self, info: ValidationInfo):
        """Cross-field span rules. Pass context={"raw": raw_text} to also check bounds and evidence policy.
        Messages carry labels and offsets only, never raw text: they are shown to the reviewer."""
        raw = (info.context or {}).get("raw")
        problems = []
        for s in self.labels + self.redactions:
            where = f"{s.label} [{s.start}, {s.end})"
            if s.start >= s.end:
                problems.append(f"{where}: start must be < end")
            elif s.start < 0 or (raw is not None and s.end > len(raw)):
                problems.append(f"{where}: out of bounds" + (f" (text has {len(raw)} chars)" if raw is not None else ""))
        labels = sorted(self.labels, key=lambda s: s.start)
        for a, b in zip(labels, labels[1:]):
            if b.start < a.end:
                problems.append(f"{b.label} [{b.start}, {b.end}): overlaps {a.label} [{a.start}, {a.end})")
        for s in self.labels:
            if s.label == "EVIDENCE" and not (s.evidence or "").strip():
                problems.append(f"EVIDENCE [{s.start}, {s.end}): requires an evidence note")
            if raw is not None and s.evidence and any(raw[r.start:r.end] and raw[r.start:r.end] in s.evidence
                                                      for r in self.redactions):
                problems.append(f"{s.label} [{s.start}, {s.end}): evidence note quotes redacted text")
        if problems:
            raise ValueError("\n".join(problems))
        return self
