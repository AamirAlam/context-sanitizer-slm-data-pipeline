"""AC-6: view_unredacted is denied unless authorized, and every request (allow or deny) is audited; fails closed."""
import json

from fastapi.testclient import TestClient

from labelpipe.review.api import create_app
from tests.test_end_to_end import SECRETS, assert_no_secret

REASON = "verify bearer token boundary for INC-4821"


def client(tmp_path, pending):  # create_app reads the allowlist, so build it after monkeypatching env
    return TestClient(create_app(tmp_path / "t.db"))


def entries(log):
    return [json.loads(line) for line in log.read_text().splitlines()]


def test_unauthorized_is_denied_and_logged(tmp_path, pending, audit_env):
    doc_id = pending[2]
    r = client(tmp_path, pending).post(f"/records/{doc_id}/spans/0/unredacted", json={"reason": REASON})
    assert r.status_code == 403
    assert_no_secret(r.text)
    [e] = entries(audit_env)
    assert (e["action"], e["decision"], e["span_index"]) == ("view_unredacted", "deny", 0)
    assert REASON not in audit_env.read_text() and doc_id not in audit_env.read_text()


def test_authorized_returns_span_and_is_logged(tmp_path, pending, audit_env, monkeypatch):
    monkeypatch.setenv("LABELPIPE_REVIEWER", "a.alam")
    monkeypatch.setenv("LABELPIPE_UNREDACT_ALLOW", "someone, a.alam")
    c, doc_id = client(tmp_path, pending), pending[2]
    n = sum("redacted" in s for s in c.get("/records/next").json()["segments"])
    revealed = [c.post(f"/records/{doc_id}/spans/{i}/unredacted", json={"reason": REASON}) for i in range(n)]
    assert all(r.status_code == 200 and r.headers["cache-control"] == "no-store" for r in revealed)
    assert set(SECRETS) <= {r.json()["text"] for r in revealed}
    log = audit_env.read_text()
    assert [e["decision"] for e in entries(audit_env)] == ["allow"] * n
    for plaintext in [REASON, "a.alam", doc_id, *SECRETS]:
        assert plaintext not in log
    assert_no_secret(c.get("/records/next").text)  # default stays redacted: a reload hides it again
    assert c.post(f"/records/{doc_id}/spans/{n}/unredacted", json={"reason": REASON}).status_code == 404
    assert c.post(f"/records/{doc_id}/spans/0/unredacted", json={"reason": ""}).status_code == 422


def test_audit_down_refuses_and_returns_no_text(tmp_path, pending, audit_env, monkeypatch):
    monkeypatch.setenv("LABELPIPE_UNREDACT_ALLOW", "reviewer")
    c = client(tmp_path, pending)
    audit_env.mkdir()  # log path is a directory -> append fails
    r = c.post(f"/records/{pending[2]}/spans/0/unredacted", json={"reason": REASON})
    assert r.status_code == 503
    assert_no_secret(r.text)
