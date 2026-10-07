from pathlib import Path

from .schema import Record, Span

POLICY_VERSION = "v1"


def _spans(conn, doc_id, kind, accepted_only=False):
    q = 'SELECT start, "end", label, source, evidence FROM spans WHERE doc_id=? AND kind=?'
    if accepted_only:
        q += " AND idx IN (SELECT span_index FROM decisions WHERE doc_id=spans.doc_id AND action IN ('accept','rewrite'))"
    return [Span(start=a, end=b, label=l, source=src, evidence=ev)
            for a, b, l, src, ev in conn.execute(q + " ORDER BY idx", (doc_id, kind))]


def record_dict(conn, doc_id, accepted_only=True) -> dict:
    """Record fields from the DB, unvalidated. Labels are the accepted/rewritten spans unless accepted_only=False."""
    _, thread_id, sha, source, reviewer, reviewed_at, model_version = conn.execute(
        "SELECT doc_id, thread_id, raw_sha256, source_system, reviewer, reviewed_at, model_version"
        " FROM records WHERE doc_id=?", (doc_id,)).fetchone()
    return dict(doc_id=doc_id, thread_id=thread_id, raw_sha256=sha,
                redactions=_spans(conn, doc_id, "redaction"), labels=_spans(conn, doc_id, "label", accepted_only),
                source_system=source, reviewer=reviewer, reviewed_at=reviewed_at,
                model_version=model_version, policy_version=POLICY_VERSION)


def export(conn, out: Path = Path("data/labeled.jsonl")) -> int:
    ids = [d for d, in conn.execute("SELECT doc_id FROM records WHERE status='validated' ORDER BY doc_id")]
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as f:
        for doc_id in ids:
            f.write(Record(**record_dict(conn, doc_id)).model_dump_json() + "\n")
    return len(ids)
