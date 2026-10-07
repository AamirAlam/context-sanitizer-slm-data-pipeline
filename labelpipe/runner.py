import json

from .checks import candidate_problems
from .dataset import record_dict
from .parse import parse
from .propose import Proposer, StubProposer
from .scan import scan
from .schema import Span
from .validate import validate

INSERT_SPAN = 'INSERT INTO spans (doc_id, kind, idx, start, "end", label, source, evidence) VALUES (?,?,?,?,?,?,?,?)'


def _check(raw: str, spans: list[Span]):
    for s in spans:
        assert 0 <= s.start < s.end <= len(raw), f"span out of bounds: {s}"


def run(conn, proposer: Proposer | None = None) -> int:
    """Advance every record as far as it goes; stops at pending_review / adjudicate (the human pauses)."""
    proposer = proposer or StubProposer()
    with conn:  # skipped → back in the queue; rejected/candidate_rejected are terminal
        conn.execute("UPDATE records SET status='pending_review' WHERE status='skipped'")
    rows = conn.execute("SELECT doc_id, raw_text, status FROM records"
                        " WHERE status IN ('ingested','parsed','scanned','proposed','approved')").fetchall()
    for doc_id, raw, status in rows:
        doc = parse(raw)  # cheap and deterministic, so recomputed rather than stored

        def advance(new, reasons=None):
            conn.execute("UPDATE records SET status=?, reasons=? WHERE doc_id=?",
                         (new, json.dumps(reasons) if reasons else None, doc_id))
            return new

        with conn:
            if status == "ingested":
                problems = candidate_problems(raw)
                status = advance("candidate_rejected", problems) if problems else advance("parsed")
            if status == "parsed":
                red = [Span(start=h["start"], end=h["end"], label=h["entity_type"], source="scan")
                       for h in scan(raw)]
                _check(raw, red)
                conn.executemany(INSERT_SPAN, [(doc_id, "redaction", i, s.start, s.end, s.label, s.source, None)
                                               for i, s in enumerate(red)])
                status = advance("scanned")
            if status == "scanned":
                red = [Span(start=a, end=b, label=l, source="scan") for a, b, l in conn.execute(
                    "SELECT start, \"end\", label FROM spans WHERE doc_id=? AND kind='redaction'", (doc_id,))]
                props = proposer.propose(doc, red)  # not asserted: bad proposals are the validation gate's job
                conn.executemany(INSERT_SPAN, [(doc_id, "label", i, s.start, s.end, s.label, s.source, s.evidence)
                                               for i, s in enumerate(props)])
                conn.execute("UPDATE records SET model_version=? WHERE doc_id=?", (proposer.model_version, doc_id))
                status = advance("proposed")
            if status == "proposed":
                advance("pending_review")
            if status == "approved":
                reasons = validate(record_dict(conn, doc_id), doc)
                advance("adjudicate", reasons) if reasons else advance("validated")
    return len(rows)
