import json

import pytest

from labelpipe import db, intake, runner
from labelpipe.parse import parse
from labelpipe.schema import Span
from labelpipe.validate import validate

RAW = "payments-api upstream call failed key=SECRETVALUE\n"


def rec(*labels, redactions=()):
    return dict(doc_id="d", thread_id="t", raw_sha256="x", redactions=list(redactions), labels=list(labels),
                source_system="file", reviewer=None, reviewed_at=None, model_version="m", policy_version="v1")


def lab(start, end, label="SERVICE", evidence=None):
    return Span(start=start, end=end, label=label, source="slm", evidence=evidence)


def test_valid_record_passes():
    assert validate(rec(lab(0, 12), lab(13, 21, "EVIDENCE", "upstream named in error")), parse(RAW)) == []


@pytest.mark.parametrize("span, reason", [
    (lab(5, 5), "start must be < end"),          # AC-2: end <= start
    (lab(9, 3), "start must be < end"),
    (lab(-1, 4), "out of bounds"),               # AC-2: out of bounds
    (lab(0, 10**6), "out of bounds"),
])
def test_bad_offsets_rejected(span, reason):
    [r] = validate(rec(span), parse(RAW))
    assert reason in r and f"[{span.start}, {span.end})" in r


def test_misaligned_span_is_char_span_none_and_flagged():  # AC-2
    doc = parse(RAW)
    assert doc.char_span(1, 12, alignment_mode="strict") is None
    assert validate(rec(lab(1, 12)), doc) == ["SERVICE [1, 12): not on token boundaries"]


def test_overlapping_labels_rejected():
    [r] = validate(rec(lab(0, 12), lab(0, 21)), parse(RAW))
    assert "overlaps" in r


@pytest.mark.parametrize("evidence", [None, "", "   "])
def test_evidence_label_without_note_fails(evidence):  # AC-4
    assert validate(rec(lab(13, 21, "EVIDENCE", evidence)), parse(RAW)) == ["EVIDENCE [13, 21): requires an evidence note"]


def test_evidence_quoting_redacted_text_fails_without_echoing_it():  # policy: no raw secret in annotations
    s = RAW.index("SECRETVALUE")
    red = Span(start=s, end=s + 11, label="API_KEY", source="scan")
    [r] = validate(rec(lab(13, 21, "EVIDENCE", "key was SECRETVALUE"), redactions=[red]), parse(RAW))
    assert "quotes redacted text" in r and "SECRETVALUE" not in r


@pytest.mark.parametrize("content, problem", [
    ("", "empty dump"),
    (" \n\t\n", "empty dump"),
    ("log\x00\x01binary", "binary content"),
    ("ERROR payments-api ... [truncated]\n", "truncated dump"),
])
def test_candidate_checks_reject_before_scan(tmp_path, content, problem):  # FR-3
    f = tmp_path / "dump.txt"
    f.write_text(content, encoding="utf-8")
    conn = db.connect(tmp_path / "t.db")
    doc_id = intake.ingest(conn, f)
    runner.run(conn)
    status, reasons = conn.execute("SELECT status, reasons FROM records WHERE doc_id=?", (doc_id,)).fetchone()
    assert status == "candidate_rejected"
    assert any(problem in r for r in json.loads(reasons))
    assert conn.execute("SELECT COUNT(*) FROM spans").fetchone()[0] == 0  # never scanned


def test_duplicate_dump_is_a_single_record(tmp_path):  # FR-3 duplicate: intake keys on content_sha256
    for name in ("a.txt", "b.txt"):
        (tmp_path / name).write_text(RAW)
    conn = db.connect(tmp_path / "t.db")
    assert intake.ingest(conn, tmp_path / "a.txt") == intake.ingest(conn, tmp_path / "b.txt")
    assert conn.execute("SELECT COUNT(*) FROM records").fetchone()[0] == 1
