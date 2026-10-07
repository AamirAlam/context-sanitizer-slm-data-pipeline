import shutil

from fastapi.testclient import TestClient

from labelpipe import dataset, db, intake, runner
from labelpipe.review.api import create_app

FIXTURE = __import__("pathlib").Path(__file__).parent / "fixtures" / "dump_with_secret.txt"
SECRETS = [
    "sk-test4f9a8B2cD7eF1gH3iJ5kL6mN0pQrS",
    "eyJhbGciOiJIUzI1NiJ9.ZmFrZXBheWxvYWQ.c2lnbmF0dXJlZmFrZQ",
    "jane.doe@oncall-corp.test",
]


def assert_no_secret(text: str):
    for s in SECRETS:
        for piece in (s, s[len(s) // 2:]):  # whole token and its distinctive tail
            assert piece not in text


def test_secret_never_leaves_server(tmp_path):
    shutil.copy(FIXTURE, tmp_path / FIXTURE.name)
    dbp = tmp_path / "t.db"
    conn = db.connect(dbp)
    doc_id = intake.ingest(conn, tmp_path / FIXTURE.name)
    runner.run(conn)

    raw = conn.execute("SELECT raw_text FROM records WHERE doc_id=?", (doc_id,)).fetchone()[0]
    assert raw == FIXTURE.read_bytes().decode()
    red = {raw[s:e]: label for s, e, label in conn.execute(
        "SELECT start, \"end\", label FROM spans WHERE doc_id=? AND kind='redaction'", (doc_id,))}
    assert red[SECRETS[0]] == "API_KEY"         # raw[start:end] reproduces the token (AC-1)
    assert red[SECRETS[1]] == "BEARER_TOKEN"
    assert red[SECRETS[2]] == "EMAIL_ADDRESS"

    client = TestClient(create_app(dbp))
    html = client.get("/")
    assert html.status_code == 200
    assert_no_secret(html.text)
    r = client.get("/records/next")
    assert r.status_code == 200
    assert_no_secret(r.text)
    body = r.json()
    assert {"redacted": "API_KEY"} in body["segments"]
    assert body["proposals"]

    assert client.post(f"/records/{doc_id}/decision", json={"action": "accept", "span_index": 0}).json() == {"status": "approved"}
    assert client.get("/records/next").status_code == 404

    out = tmp_path / "data" / "labeled.jsonl"
    assert dataset.export(conn, out) == 1
    exported = out.read_text()
    assert_no_secret(exported)
    import json
    row = json.loads(exported)
    assert row["reviewer"] and row["model_version"] == "stub-0" and row["policy_version"] == "v1"
    assert len(row["labels"]) == 1
