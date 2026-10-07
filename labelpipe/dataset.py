from pathlib import Path

from .schema import Record, Span

POLICY_VERSION = "v1"


def _spans(conn, doc_id, kind, accepted_only=False):
    q = 'SELECT idx, start, "end", label, source FROM spans WHERE doc_id=? AND kind=?'
    if accepted_only:
        q += " AND idx IN (SELECT span_index FROM decisions WHERE doc_id=spans.doc_id AND action='accept')"
    return [Span(start=a, end=b, label=l, source=src) for _, a, b, l, src in conn.execute(q + " ORDER BY idx", (doc_id, kind))]


def export(conn, out: Path = Path("data/labeled.jsonl")) -> int:
    rows = conn.execute("SELECT doc_id, thread_id, raw_sha256, source_system, reviewer, reviewed_at, model_version"
                        " FROM records WHERE status='approved' ORDER BY doc_id").fetchall()
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as f:
        for doc_id, thread_id, sha, source, reviewer, reviewed_at, model_version in rows:
            rec = Record(doc_id=doc_id, thread_id=thread_id, raw_sha256=sha,
                         redactions=_spans(conn, doc_id, "redaction"),
                         labels=_spans(conn, doc_id, "label", accepted_only=True),
                         source_system=source, reviewer=reviewer, reviewed_at=reviewed_at,
                         model_version=model_version, policy_version=POLICY_VERSION)
            f.write(rec.model_dump_json() + "\n")
    return len(rows)
