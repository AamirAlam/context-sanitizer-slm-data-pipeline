import shutil
import socket
from pathlib import Path

from fastapi.testclient import TestClient

from labelpipe import db, intake, runner, scan
from labelpipe.review.api import create_app

FIXTURE = Path(__file__).parent / "fixtures" / "dump_aws_jwt.txt"
AWS = "AKIAIOSFODNN7EXAMPLE"
JWT = ("eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0"
       ".dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U")


def spans(text):
    return {text[h["start"]:h["end"]]: h["entity_type"] for h in scan.scan(text)}


def test_new_key_formats_redacted():
    raw = FIXTURE.read_text()
    phase1 = {raw[r.start:r.end] for r in scan.analyzer().analyze(text=raw, language="en")}
    assert AWS not in phase1 and JWT not in phase1  # Presidio + Phase 1 regexes miss them
    red = spans(raw)
    assert red[AWS] == "AWS_ACCESS_KEY"
    assert red[JWT] == "JSON_WEB_TOKEN"  # whole token, not just detect-secrets' header.payload. prefix


def test_new_keys_never_reach_review_payload(tmp_path):
    shutil.copy(FIXTURE, tmp_path / FIXTURE.name)
    conn = db.connect(tmp_path / "t.db")
    intake.ingest(conn, tmp_path / FIXTURE.name)
    runner.run(conn)
    body = TestClient(create_app(tmp_path / "t.db")).get("/records/next").text
    for s in (AWS, JWT, JWT[-20:]):
        assert s not in body


def test_line_offsets_are_code_points_across_crlf_and_multibyte():
    raw = "😀 café 失败\r\n\r\nkey 👩‍💻 " + AWS + "\r\nnext " + JWT + "\n"
    red = spans(raw)
    assert red[AWS] == "AWS_ACCESS_KEY" and red[JWT] == "JSON_WEB_TOKEN"


def test_overlap_secret_span_wins():
    raw = "Authorization: Bearer " + JWT + "\n"
    presidio = [(r.start, r.end, r.entity_type) for r in scan.analyzer().analyze(text=raw, language="en")]
    assert any(t == "BEARER_TOKEN" for *_, t in presidio)  # both detectors fire on the same token
    hits = scan.scan(raw)
    assert [(raw[h["start"]:h["end"]], h["entity_type"]) for h in hits] == [(JWT, "JSON_WEB_TOKEN")]


def test_scan_is_offline_and_deterministic(monkeypatch):  # AC-7
    def no_network(*a, **k):
        raise AssertionError("network access attempted during scan")
    monkeypatch.setattr(socket, "socket", no_network)
    monkeypatch.setattr(socket, "create_connection", no_network)
    scan.analyzer.cache_clear()  # engine construction is part of the scan path too
    raw = FIXTURE.read_text() + "Authorization: Bearer " + JWT + "\n"
    first, second = scan.scan(raw), scan.scan(raw)
    assert first == second and first
