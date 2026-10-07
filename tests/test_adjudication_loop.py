import json
import shutil

from fastapi.testclient import TestClient

from labelpipe import dataset, db, intake, runner
from labelpipe.review.api import create_app
from labelpipe.schema import Span
from tests.conftest import FIXTURE
from tests.test_end_to_end import assert_no_secret


class MisalignedProposer:
    """Proposes 'ayments-api': in bounds, but starts mid-token."""
    model_version = "misaligned-0"

    def propose(self, doc, redactions):
        s = doc.text.index("payments-api") + 1
        return [Span(start=s, end=s + len("ayments-api"), label="SERVICE", source="slm")]


def setup(tmp_path, proposer=None, n=1):
    conn = db.connect(tmp_path / "t.db")
    ids = []
    for i in range(n):  # distinct content per copy, else intake dedups them
        f = tmp_path / f"dump{i}.txt"
        f.write_bytes(FIXTURE.read_bytes() + b"#" * i)
        ids.append(intake.ingest(conn, f))
    runner.run(conn, proposer)
    return conn, TestClient(create_app(tmp_path / "t.db")), ids


def status(conn, doc_id):
    return conn.execute("SELECT status FROM records WHERE doc_id=?", (doc_id,)).fetchone()[0]


def test_invalid_label_loops_through_adjudication_to_validated(tmp_path):
    conn, client, [doc_id] = setup(tmp_path, MisalignedProposer())
    raw = conn.execute("SELECT raw_text FROM records WHERE doc_id=?", (doc_id,)).fetchone()[0]
    s = raw.index("payments-api") + 1

    first = client.get("/records/next").json()
    assert first["validation"] == [f"SERVICE [{s}, {s + 11}): not on token boundaries"]  # live, before acting
    assert first["needs_second_look"] is False

    assert client.post(f"/records/{doc_id}/decision", json={"action": "accept", "span_index": 0}).status_code == 200
    runner.run(conn)
    assert status(conn, doc_id) == "adjudicate"  # gate failed

    second = client.get("/records/next")
    assert_no_secret(second.text)
    second = second.json()
    assert second["doc_id"] == doc_id and second["needs_second_look"] is True
    assert second["adjudication_reasons"] == [f"SERVICE [{s}, {s + 11}): not on token boundaries"]

    fix = {"action": "rewrite", "span_index": 0, "new_start": s - 1, "new_end": s + 11}
    assert client.post(f"/records/{doc_id}/decision", json=fix).json() == {"status": "approved"}
    assert conn.execute("SELECT reasons FROM records WHERE doc_id=?", (doc_id,)).fetchone()[0] is None
    runner.run(conn)
    assert status(conn, doc_id) == "validated"

    out = tmp_path / "data" / "labeled.jsonl"
    assert dataset.export(conn, out) == 1
    [label] = json.loads(out.read_text())["labels"]
    assert raw[label["start"]:label["end"]] == "payments-api" and label["source"] == "human"


def test_unvalidated_records_are_not_exported(tmp_path):
    conn, client, [doc_id] = setup(tmp_path, MisalignedProposer())
    client.post(f"/records/{doc_id}/decision", json={"action": "accept", "span_index": 0})
    assert dataset.export(conn, tmp_path / "out.jsonl") == 0  # approved, not yet validated
    runner.run(conn)
    assert dataset.export(conn, tmp_path / "out.jsonl") == 0  # adjudicate


def test_escalated_record_is_served_before_pending_ones(tmp_path):
    conn, client, (a, b) = setup(tmp_path, n=2)
    assert client.get("/records/next").json()["doc_id"] == a  # normal order
    assert client.post(f"/records/{b}/decision", json={"action": "escalate"}).json() == {"status": "adjudicate"}
    nxt = client.get("/records/next").json()
    assert nxt["doc_id"] == b
    assert nxt["needs_second_look"] is True and nxt["adjudication_reasons"] == ["escalated by reviewer"]
    assert client.post(f"/records/{b}/decision", json={"action": "accept", "span_index": 0}).status_code == 200
    runner.run(conn)
    assert status(conn, b) == "validated"
    assert client.get("/records/next").json()["doc_id"] == a
