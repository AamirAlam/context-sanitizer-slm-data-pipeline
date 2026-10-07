import json

import pytest

from labelpipe import dataset, runner

EXPECTED = {"accept": "approved", "rewrite": "approved", "skip": "skipped",
            "reject": "rejected", "escalate": "adjudicate"}


def body(action, doc_raw):
    if action == "accept":
        return {"action": action, "span_index": 0}
    if action == "rewrite":
        s = doc_raw.index("payments-api")
        return {"action": action, "span_index": 0, "new_start": s, "new_end": s + len("payments-api"), "new_label": "SERVICE"}
    return {"action": action}


@pytest.mark.parametrize("action", EXPECTED)
def test_each_action_persists_exactly_one_decision(pending, audit_env, action):  # AC-3
    conn, client, doc_id = pending
    raw = conn.execute("SELECT raw_text FROM records WHERE doc_id=?", (doc_id,)).fetchone()[0]
    r = client.post(f"/records/{doc_id}/decision", json=body(action, raw))
    assert r.status_code == 200, r.text
    assert r.json() == {"status": EXPECTED[action]}
    assert conn.execute("SELECT action FROM decisions WHERE doc_id=?", (doc_id,)).fetchall() == [(action,)]
    assert conn.execute("SELECT status FROM records WHERE doc_id=?", (doc_id,)).fetchone()[0] == EXPECTED[action]
    entries = audit_env.read_text().splitlines()
    assert len(entries) == 1 and json.loads(entries[0])["action"] == action
    if action == "escalate":
        return  # escalated records re-enter review (adjudication loop, test_adjudication_loop.py)
    # a second decision on the same record is refused and not logged
    assert client.post(f"/records/{doc_id}/decision", json=body(action, raw)).status_code == 409
    assert len(audit_env.read_text().splitlines()) == 1


def test_rewrite_exports_new_offsets_as_human(pending, tmp_path):
    conn, client, doc_id = pending
    raw = conn.execute("SELECT raw_text FROM records WHERE doc_id=?", (doc_id,)).fetchone()[0]
    b = body("rewrite", raw)
    assert client.post(f"/records/{doc_id}/decision", json=b).status_code == 200
    runner.run(conn)
    out = tmp_path / "data"
    assert dataset.export(conn, out) == 1
    [f] = out.glob("*/*.jsonl")
    [label] = json.loads(f.read_text())["labels"]
    assert (label["start"], label["end"], label["label"], label["source"]) == (b["new_start"], b["new_end"], "SERVICE", "human")
    assert raw[label["start"]:label["end"]] == "payments-api"


@pytest.mark.parametrize("bad", [
    {"action": "accept"},                                                   # no span_index
    {"action": "accept", "span_index": 99},                                 # unknown span
    {"action": "rewrite", "span_index": 0, "new_start": 5, "new_end": 5},   # empty span
    {"action": "rewrite", "span_index": 0, "new_start": 0, "new_end": 10**6},  # out of bounds
    {"action": "skip", "new_label": "X"},                                   # new_* outside rewrite
    {"action": "approve"},                                                  # not an action
])
def test_invalid_decisions_rejected_without_side_effects(pending, audit_env, bad):
    conn, client, doc_id = pending
    assert client.post(f"/records/{doc_id}/decision", json=bad).status_code == 422
    assert conn.execute("SELECT COUNT(*) FROM decisions").fetchone()[0] == 0
    assert conn.execute("SELECT status FROM records").fetchone()[0] == "pending_review"
    assert not audit_env.exists()


def test_skipped_record_is_requeued_by_runner(pending):
    conn, client, doc_id = pending
    client.post(f"/records/{doc_id}/decision", json={"action": "skip"})
    assert client.get("/records/next").status_code == 404
    runner.run(conn)
    assert client.get("/records/next").json()["doc_id"] == doc_id
