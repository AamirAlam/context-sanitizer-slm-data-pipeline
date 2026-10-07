import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from labelpipe import db, intake, runner
from labelpipe.review.api import create_app

FIXTURE = Path(__file__).parent / "fixtures" / "dump_with_secret.txt"


@pytest.fixture(autouse=True)
def audit_env(tmp_path, monkeypatch):
    monkeypatch.setenv("LABELPIPE_AUDIT_KEY", "test-key")
    monkeypatch.setenv("LABELPIPE_AUDIT_LOG", str(tmp_path / "audit.jsonl"))
    return tmp_path / "audit.jsonl"


@pytest.fixture
def pending(tmp_path):
    """(conn, client, doc_id) for one fixture dump sitting in pending_review."""
    shutil.copy(FIXTURE, tmp_path / FIXTURE.name)
    conn = db.connect(tmp_path / "t.db")
    doc_id = intake.ingest(conn, tmp_path / FIXTURE.name)
    runner.run(conn)
    return conn, TestClient(create_app(tmp_path / "t.db")), doc_id
