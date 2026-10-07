"""Review API: the trust boundary. Raw characters inside a redaction never leave the server."""
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from .. import audit
from ..dataset import POLICY_VERSION, record_dict
from ..db import connect
from ..parse import parse
from ..validate import validate

UI = Path(__file__).parent / "ui" / "index.html"


NEXT_STATUS = {"accept": "approved", "rewrite": "approved", "skip": "skipped",
               "reject": "rejected", "escalate": "adjudicate"}


class Decision(BaseModel):
    action: Literal["accept", "rewrite", "skip", "reject", "escalate"]
    span_index: int | None = None  # required for accept/rewrite
    new_start: int | None = None   # rewrite only: code-point offsets into raw text
    new_end: int | None = None
    new_label: str | None = None   # rewrite only; defaults to the proposed label


class Unredact(BaseModel):
    reason: str = Field(min_length=1)  # explicit, stated purpose; HMAC'd in the audit log


def segments(raw: str, redactions) -> list[dict]:
    """Split raw text into plain segments and redaction placeholders (entity type only)."""
    out, pos = [], 0
    for start, end, label in redactions:  # non-overlapping, sorted (scan merges overlaps)
        if start > pos:
            out.append({"text": raw[pos:start], "start": pos})
        out.append({"redacted": label})
        pos = max(pos, end)
    if pos < len(raw):
        out.append({"text": raw[pos:], "start": pos})
    return out


def create_app(db_path, audit_log: audit.AuditLog | None = None) -> FastAPI:
    audit_log = audit_log or audit.from_env()  # missing HMAC key fails here, at startup
    app = FastAPI()
    conn = connect(db_path)
    reviewer = os.environ.get("LABELPIPE_REVIEWER", "reviewer")
    # ponytail: one env allowlist for the single reviewer role; RBAC/ABAC + policy engine when a 2nd role exists
    unredact_allow = {a.strip() for a in os.environ.get("LABELPIPE_UNREDACT_ALLOW", "").split(",") if a.strip()}

    @app.get("/")
    def index():
        return FileResponse(UI)

    @app.get("/records/next")
    def next_record():
        row = conn.execute("SELECT doc_id, raw_text, status, reasons FROM records"
                           " WHERE status IN ('adjudicate', 'pending_review')"
                           " ORDER BY status='adjudicate' DESC, ingested_at, doc_id LIMIT 1").fetchone()
        if not row:
            raise HTTPException(404, "no records pending review")
        doc_id, raw, status, reasons = row
        q = 'SELECT start, "end", label FROM spans WHERE doc_id=? AND kind=? ORDER BY '
        red = conn.execute(q + "start, idx", (doc_id, "redaction")).fetchall()  # same order as unredact's {i}
        props = conn.execute(q + "idx", (doc_id, "label")).fetchall()
        return {"doc_id": doc_id, "segments": segments(raw, red),
                "proposals": [{"start": s, "end": e, "label": l, "source": "slm"} for s, e, l in props],
                "needs_second_look": status == "adjudicate", "adjudication_reasons": json.loads(reasons or "[]"),
                # live gate over every proposal, so the reviewer sees problems before acting
                "validation": validate(record_dict(conn, doc_id, accepted_only=False), parse(raw))}

    @app.post("/records/{doc_id}/decision")
    def decide(doc_id: str, d: Decision):
        with conn:  # any exception below (incl. the audit 503) rolls the whole decision back
            row = conn.execute("SELECT status, raw_text FROM records WHERE doc_id=?", (doc_id,)).fetchone()
            if not row:
                raise HTTPException(404, "unknown record")
            status, raw = row
            if status not in ("pending_review", "adjudicate"):
                raise HTTPException(409, f"record is {status}")
            if d.action in ("accept", "rewrite") and d.span_index is None:
                raise HTTPException(422, f"{d.action} requires span_index")
            span = None if d.span_index is None else conn.execute(
                "SELECT label FROM spans WHERE doc_id=? AND kind='label' AND idx=?", (doc_id, d.span_index)).fetchone()
            if d.span_index is not None and not span:
                raise HTTPException(422, "unknown span_index")
            if d.action == "rewrite":
                if d.new_start is None or d.new_end is None or not 0 <= d.new_start < d.new_end <= len(raw):
                    raise HTTPException(422, "rewrite requires 0 <= new_start < new_end <= len(raw)")
                d.new_label = d.new_label or span[0]
                conn.execute('UPDATE spans SET start=?, "end"=?, label=?, source=\'human\''
                             " WHERE doc_id=? AND kind='label' AND idx=?",
                             (d.new_start, d.new_end, d.new_label, doc_id, d.span_index))
            elif d.new_start is not None or d.new_end is not None or d.new_label is not None:
                raise HTTPException(422, "new_* fields are only valid for rewrite")
            now = datetime.now(timezone.utc).isoformat()
            conn.execute("INSERT INTO decisions (doc_id, span_index, action, new_start, new_end, new_label,"
                         " reviewer, decided_at) VALUES (?,?,?,?,?,?,?,?)",
                         (doc_id, d.span_index, d.action, d.new_start, d.new_end, d.new_label, reviewer, now))
            new_status = NEXT_STATUS[d.action]
            conn.execute("UPDATE records SET status=?, reviewer=?, reviewed_at=?, reasons=? WHERE doc_id=?",
                         (new_status, reviewer, now,
                          json.dumps(["escalated by reviewer"]) if d.action == "escalate" else None, doc_id))
            try:  # audit before commit: no entry, no action
                audit_log.append({"action": d.action, "actor": reviewer, "doc_id": doc_id, **d.model_dump(exclude={"action"})})
            except audit.AuditUnavailable:
                raise HTTPException(503, "audit log unavailable; action refused")
        return {"status": new_status}

    @app.post("/records/{doc_id}/spans/{i}/unredacted")
    def view_unredacted(doc_id: str, i: int, req: Unredact, response: Response):
        """{i} is the i-th redaction placeholder in /records/next segments. Deny by default; every request is audited."""
        decision = "allow" if reviewer in unredact_allow else "deny"
        try:  # fail closed: no audit entry, no answer (not even a 403)
            audit_log.append({"action": "view_unredacted", "actor": reviewer, "doc_id": doc_id, "span_index": i,
                              "decision": decision, "reason": req.reason, "policy": POLICY_VERSION})
        except audit.AuditUnavailable:
            raise HTTPException(503, "audit log unavailable; request refused")
        if decision == "deny":
            raise HTTPException(403, "not authorized to view unredacted text")
        row = None if i < 0 else conn.execute(
            'SELECT r.raw_text, s.start, s."end" FROM spans s JOIN records r USING (doc_id)'
            " WHERE s.doc_id=? AND s.kind='redaction' ORDER BY s.start, s.idx LIMIT 1 OFFSET ?", (doc_id, i)).fetchone()
        if not row:
            raise HTTPException(404, "unknown redaction")
        raw, start, end = row
        response.headers["Cache-Control"] = "no-store"
        return {"text": raw[start:end]}

    return app
