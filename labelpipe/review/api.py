"""Review API: the trust boundary. Raw characters inside a redaction never leave the server."""
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from ..db import connect

UI = Path(__file__).parent / "ui" / "index.html"


class Decision(BaseModel):
    action: Literal["accept"]
    span_index: int


def segments(raw: str, redactions) -> list[dict]:
    """Split raw text into plain segments and redaction placeholders (entity type only)."""
    out, pos = [], 0
    for start, end, label in redactions:  # non-overlapping, sorted (scan merges overlaps)
        if start > pos:
            out.append({"text": raw[pos:start]})
        out.append({"redacted": label})
        pos = max(pos, end)
    if pos < len(raw):
        out.append({"text": raw[pos:]})
    return out


def create_app(db_path) -> FastAPI:
    app = FastAPI()
    conn = connect(db_path)
    reviewer = os.environ.get("LABELPIPE_REVIEWER", "reviewer")

    @app.get("/")
    def index():
        return FileResponse(UI)

    @app.get("/records/next")
    def next_record():
        row = conn.execute("SELECT doc_id, raw_text FROM records WHERE status='pending_review'"
                           " ORDER BY ingested_at, doc_id LIMIT 1").fetchone()
        if not row:
            raise HTTPException(404, "no records pending review")
        doc_id, raw = row
        q = 'SELECT start, "end", label FROM spans WHERE doc_id=? AND kind=? ORDER BY '
        red = conn.execute(q + "start", (doc_id, "redaction")).fetchall()
        props = conn.execute(q + "idx", (doc_id, "label")).fetchall()
        return {"doc_id": doc_id, "segments": segments(raw, red),
                "proposals": [{"start": s, "end": e, "label": l, "source": "slm"} for s, e, l in props]}

    @app.post("/records/{doc_id}/decision")
    def decide(doc_id: str, d: Decision):
        with conn:
            row = conn.execute("SELECT status FROM records WHERE doc_id=?", (doc_id,)).fetchone()
            if not row:
                raise HTTPException(404, "unknown record")
            if row[0] != "pending_review":
                raise HTTPException(409, f"record is {row[0]}")
            if not conn.execute("SELECT 1 FROM spans WHERE doc_id=? AND kind='label' AND idx=?",
                                (doc_id, d.span_index)).fetchone():
                raise HTTPException(422, "unknown span_index")
            now = datetime.now(timezone.utc).isoformat()
            conn.execute("INSERT INTO decisions (doc_id, span_index, action, reviewer, decided_at)"
                         " VALUES (?,?,?,?,?)", (doc_id, d.span_index, d.action, reviewer, now))
            conn.execute("UPDATE records SET status='approved', reviewer=?, reviewed_at=? WHERE doc_id=?",
                         (reviewer, now, doc_id))
        return {"status": "approved"}

    return app
