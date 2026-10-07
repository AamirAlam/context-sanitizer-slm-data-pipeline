from typing import Protocol

from .schema import Span


class Proposer(Protocol):
    model_version: str

    def propose(self, doc, redactions: list[Span]) -> list[Span]: ...


class StubProposer:
    """Proposes the first word token that does not touch a redaction."""
    model_version = "stub-0"

    def propose(self, doc, redactions):
        for t in doc:
            s, e = t.idx, t.idx + len(t)
            if not (t.is_space or t.is_punct) and all(e <= r.start or s >= r.end for r in redactions):
                return [Span(start=s, end=e, label="UNKNOWN", source="slm")]
        return []
