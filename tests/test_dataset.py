import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from labelpipe import dataset, db, intake, runner
from labelpipe.review.api import create_app
from tests.conftest import FIXTURE
from tests.test_end_to_end import assert_no_secret

ROOT = Path(__file__).parent.parent
AUDIT = ("source_system", "reviewer", "reviewed_at", "model_version", "policy_version")


def build_db(tmp_path, threads=12, per_thread=2) -> Path:
    """A DB of validated records: `threads` threads x `per_thread` records, each holding the fixture secrets."""
    dbp = tmp_path / "fixture.db"
    conn = db.connect(dbp)
    for i in range(threads):
        for j in range(per_thread):  # same stem in different dirs = same thread; distinct bytes, else intake dedups
            f = tmp_path / "dumps" / str(j) / f"thread{i:02}.txt"
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_bytes(FIXTURE.read_bytes() + f"\n# {i}/{j}\n".encode())
            intake.ingest(conn, f)
    runner.run(conn)
    client = TestClient(create_app(dbp))
    while (r := client.get("/records/next")).status_code == 200:
        client.post(f"/records/{r.json()['doc_id']}/decision", json={"action": "accept", "span_index": 0})
    runner.run(conn)
    assert conn.execute("SELECT COUNT(*) FROM records WHERE status='validated'").fetchone()[0] == threads * per_thread
    conn.close()
    return dbp


def tree_hashes(d: Path) -> dict:
    return {str(f.relative_to(d)): hashlib.sha256(f.read_bytes()).hexdigest() for f in sorted(d.rglob("*")) if f.is_file()}


def test_threads_never_span_splits_and_rows_carry_audit(tmp_path):  # AC-5, AC-1
    conn = db.connect(build_db(tmp_path))
    out = tmp_path / "data"
    assert dataset.export(conn, out) == 24

    split_of = {}
    for f in out.glob("*/*.jsonl"):
        assert_no_secret(f.read_text())
        for row in map(json.loads, f.read_text().splitlines()):
            assert split_of.setdefault(row["thread_id"], f.parent.name) == f.parent.name
            assert row["thread_id"] == f.stem
            assert all(row[c] for c in AUDIT), row
    assert len(split_of) == 12
    assert len(set(split_of.values())) > 1  # the fixture really exercises more than one split
    assert_no_secret((out / "manifest.json").read_text())

    manifest = json.loads((out / "manifest.json").read_text())
    hf = dataset.to_hf(out)
    assert {s: hf[s].num_rows for s in hf} == {s: n for s, n in manifest["records"].items() if n}


def test_split_is_deterministic_and_stable(tmp_path):
    conn = db.connect(build_db(tmp_path))
    a, b = tmp_path / "a", tmp_path / "b"
    dataset.export(conn, a)
    dataset.export(conn, b)
    assert tree_hashes(a) == tree_hashes(b)
    assert dataset.split_by_thread("t", "v1") == dataset.split_by_thread("t", "v1")
    # a thread's split depends only on (seed, thread_id), so more approved records never move it
    before = {f.stem: f.parent.name for f in a.glob("*/*.jsonl")}
    conn.execute("UPDATE records SET status='pending_review' WHERE thread_id='thread00'")
    conn.commit()
    dataset.export(conn, a)
    assert {f.stem: f.parent.name for f in a.glob("*/*.jsonl")} == {k: v for k, v in before.items() if k != "thread00"}


def test_leakage_check_fires(tmp_path):
    conn = db.connect(build_db(tmp_path, threads=1, per_thread=1))
    out = tmp_path / "data"
    dataset.export(conn, out)
    [f] = out.glob("*/*.jsonl")
    other = next(s for s in dataset.SPLITS if s != f.parent.name)
    (out / other).mkdir(exist_ok=True)
    shutil.copy(f, out / other / f.name)
    with pytest.raises(AssertionError, match="leaks across splits"):
        dataset.assert_no_thread_leakage(out)


def test_unsafe_thread_id_refused(tmp_path):
    conn = db.connect(build_db(tmp_path, threads=1, per_thread=1))
    conn.execute("UPDATE records SET thread_id='../escape'")
    with pytest.raises(ValueError, match="unsafe thread_id"):
        dataset.export(conn, tmp_path / "data")


def test_dvc_repro_reproduces_identical_data(tmp_path):  # NFR-6
    work = tmp_path / "work"
    work.mkdir()
    shutil.copy(build_db(tmp_path), work / "labelpipe.db")
    for f in ("dvc.yaml", "params.yaml"):  # the repo's real pipeline definition
        shutil.copy(ROOT / f, work / f)
    env = {**os.environ, "DVC_NO_ANALYTICS": "1",
           "PATH": f"{Path(sys.executable).parent}{os.pathsep}{os.environ['PATH']}"}

    def dvc(*args):
        r = subprocess.run([sys.executable, "-m", "dvc", *args], cwd=work, env=env, capture_output=True, text=True)
        assert r.returncode == 0, r.stdout + r.stderr
        return r.stdout

    dvc("init", "--no-scm")  # local cache only, no remote
    dvc("repro")
    first = tree_hashes(work / "data")
    assert any(k.endswith(".jsonl") for k in first)
    assert "up to date" in dvc("status")  # export did not modify its labelpipe.db dep

    shutil.rmtree(work / "data")
    dvc("repro", "--force")  # recompute, not a cache checkout
    assert tree_hashes(work / "data") == first

    direct = tmp_path / "direct"  # and the stage matches a plain export with the same params
    params = yaml.safe_load((work / "params.yaml").read_text())["export"]
    dataset.export(db.connect(work / "labelpipe.db"), direct, **params)
    assert tree_hashes(direct) == first
