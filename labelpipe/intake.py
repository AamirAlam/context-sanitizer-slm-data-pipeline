import getpass
import hashlib
from datetime import datetime, timezone
from pathlib import Path


def ingest(conn, path: Path, source_system: str = "file") -> str:
    data = path.read_bytes()
    raw = data.decode("utf-8")  # not read_text(): universal newlines would rewrite CRLF and shift offsets
    sha = hashlib.sha256(data).hexdigest()
    doc_id = sha[:16]
    with conn:
        conn.execute(
            "INSERT OR IGNORE INTO records (doc_id, thread_id, source_system, submitter_id,"
            " ingested_at, raw_text, raw_sha256, status) VALUES (?,?,?,?,?,?,?, 'ingested')",
            # ponytail: thread_id = file stem until dumps carry a real thread key
            (doc_id, path.stem, source_system, getpass.getuser(),
             datetime.now(timezone.utc).isoformat(), raw, sha),
        )
    return doc_id
