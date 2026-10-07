from .parse import parse
from .propose import Proposer, StubProposer
from .scan import scan
from .schema import Span

INSERT_SPAN = 'INSERT INTO spans (doc_id, kind, idx, start, "end", label, source) VALUES (?,?,?,?,?,?,?)'


def _check(raw: str, spans: list[Span]):
    for s in spans:
        assert 0 <= s.start < s.end <= len(raw), f"span out of bounds: {s}"


def run(conn, proposer: Proposer | None = None) -> int:
    """Advance every record as far as it goes; stops at pending_review (the human pause)."""
    proposer = proposer or StubProposer()
    with conn:  # skipped → back in the queue; rejected is terminal; adjudicate waits for a second look
        conn.execute("UPDATE records SET status='pending_review' WHERE status='skipped'")
    rows = conn.execute("SELECT doc_id, raw_text, status FROM records"
                        " WHERE status IN ('ingested','parsed','scanned','proposed')").fetchall()
    for doc_id, raw, status in rows:
        doc = parse(raw)  # cheap and deterministic, so recomputed rather than stored

        def advance(new):
            conn.execute("UPDATE records SET status=? WHERE doc_id=?", (new, doc_id))
            return new

        with conn:
            if status == "ingested":
                status = advance("parsed")
            if status == "parsed":
                red = [Span(start=h["start"], end=h["end"], label=h["entity_type"], source="scan")
                       for h in scan(raw)]
                _check(raw, red)
                conn.executemany(INSERT_SPAN, [(doc_id, "redaction", i, s.start, s.end, s.label, s.source)
                                               for i, s in enumerate(red)])
                status = advance("scanned")
            if status == "scanned":
                red = [Span(start=a, end=b, label=l, source="scan") for a, b, l in conn.execute(
                    "SELECT start, \"end\", label FROM spans WHERE doc_id=? AND kind='redaction'", (doc_id,))]
                props = proposer.propose(doc, red)
                _check(raw, props)
                conn.executemany(INSERT_SPAN, [(doc_id, "label", i, s.start, s.end, s.label, s.source)
                                               for i, s in enumerate(props)])
                conn.execute("UPDATE records SET model_version=? WHERE doc_id=?", (proposer.model_version, doc_id))
                status = advance("proposed")
            if status == "proposed":
                advance("pending_review")
    return len(rows)
