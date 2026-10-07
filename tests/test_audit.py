import os

import pytest

from labelpipe.review.api import create_app
from tests.test_end_to_end import SECRETS


def test_audit_entry_holds_no_plaintext(pending, audit_env):  # AC-3, NFR-4
    conn, client, doc_id = pending
    client.post(f"/records/{doc_id}/decision", json={"action": "rewrite", "span_index": 0,
                                                    "new_start": 0, "new_end": 4, "new_label": "TIMESTAMP"})
    log = audit_env.read_text()
    for plaintext in [doc_id, "reviewer", "TIMESTAMP", "test-key", *SECRETS]:
        assert plaintext not in log
    assert log.count("hmac-sha256:") == 3  # actor, doc_id, new_label


def test_unwritable_sink_refuses_action_and_persists_nothing(pending, audit_env):  # NFR-4 fail closed
    conn, client, doc_id = pending
    audit_env.mkdir()  # a directory where the log file should be -> open() fails
    r = client.post(f"/records/{doc_id}/decision", json={"action": "rewrite", "span_index": 0,
                                                        "new_start": 0, "new_end": 4})
    assert r.status_code == 503
    assert conn.execute("SELECT COUNT(*) FROM decisions").fetchone()[0] == 0
    assert conn.execute("SELECT status, reviewer FROM records").fetchone() == ("pending_review", None)
    assert conn.execute("SELECT source FROM spans WHERE kind='label'").fetchone() == ("slm",)  # rewrite rolled back


def test_log_is_append_only(pending, audit_env):
    conn, client, doc_id = pending
    audit_env.write_text("earlier entry\n")
    client.post(f"/records/{doc_id}/decision", json={"action": "skip"})
    assert audit_env.read_text().startswith("earlier entry\n")
    assert len(audit_env.read_text().splitlines()) == 2


def test_missing_key_fails_at_startup(tmp_path, monkeypatch):
    monkeypatch.delenv("LABELPIPE_AUDIT_KEY")
    with pytest.raises(RuntimeError, match="LABELPIPE_AUDIT_KEY"):
        create_app(tmp_path / "t.db")
