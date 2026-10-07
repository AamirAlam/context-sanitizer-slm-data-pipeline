import sqlite3

SCHEMA = """
CREATE TABLE IF NOT EXISTS records (
    doc_id TEXT PRIMARY KEY,
    thread_id TEXT NOT NULL,
    source_system TEXT NOT NULL,
    submitter_id TEXT NOT NULL,
    ingested_at TEXT NOT NULL,
    raw_text TEXT NOT NULL,
    raw_sha256 TEXT NOT NULL,
    status TEXT NOT NULL,
    model_version TEXT,
    reviewer TEXT,
    reviewed_at TEXT
);
CREATE TABLE IF NOT EXISTS spans (
    doc_id TEXT NOT NULL REFERENCES records(doc_id),
    kind TEXT NOT NULL CHECK (kind IN ('redaction', 'label')),
    idx INTEGER NOT NULL,
    start INTEGER NOT NULL,
    "end" INTEGER NOT NULL,
    label TEXT NOT NULL,
    source TEXT NOT NULL,
    PRIMARY KEY (doc_id, kind, idx)
);
CREATE TABLE IF NOT EXISTS decisions (
    id INTEGER PRIMARY KEY,
    doc_id TEXT NOT NULL REFERENCES records(doc_id),
    span_index INTEGER NOT NULL,
    action TEXT NOT NULL,
    reviewer TEXT NOT NULL,
    decided_at TEXT NOT NULL
);
"""


def connect(path) -> sqlite3.Connection:
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.executescript(SCHEMA)
    return conn
