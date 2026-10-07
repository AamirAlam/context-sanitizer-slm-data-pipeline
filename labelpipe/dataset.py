import hashlib
import json
import shutil
from collections import defaultdict
from itertools import accumulate
from pathlib import Path

from .schema import Record, Span

POLICY_VERSION = "v1"
SPLITS = ("train", "validation", "test")
RATIOS = (80, 10, 10)  # percent, in SPLITS order


def _spans(conn, doc_id, kind, accepted_only=False):
    q = 'SELECT start, "end", label, source, evidence FROM spans WHERE doc_id=? AND kind=?'
    if accepted_only:
        q += " AND idx IN (SELECT span_index FROM decisions WHERE doc_id=spans.doc_id AND action IN ('accept','rewrite'))"
    return [Span(start=a, end=b, label=l, source=src, evidence=ev)
            for a, b, l, src, ev in conn.execute(q + " ORDER BY idx", (doc_id, kind))]


def record_dict(conn, doc_id, accepted_only=True) -> dict:
    """Record fields from the DB, unvalidated. Labels are the accepted/rewritten spans unless accepted_only=False."""
    _, thread_id, sha, source, reviewer, reviewed_at, model_version = conn.execute(
        "SELECT doc_id, thread_id, raw_sha256, source_system, reviewer, reviewed_at, model_version"
        " FROM records WHERE doc_id=?", (doc_id,)).fetchone()
    return dict(doc_id=doc_id, thread_id=thread_id, raw_sha256=sha,
                redactions=_spans(conn, doc_id, "redaction"), labels=_spans(conn, doc_id, "label", accepted_only),
                source_system=source, reviewer=reviewer, reviewed_at=reviewed_at,
                model_version=model_version, policy_version=POLICY_VERSION)


def split_by_thread(thread_id: str, seed: str, ratios=RATIOS) -> str:
    """Stable split for a whole thread: a hash bucket, not a shuffle, so re-exports never move a thread."""
    bucket = int(hashlib.sha256(f"{seed}:{thread_id}".encode()).hexdigest(), 16) % 100
    for split, edge in zip(SPLITS, accumulate(ratios)):
        if bucket < edge:
            return split
    raise ValueError(f"ratios must sum to 100, got {ratios}")


def assert_no_thread_leakage(out: Path):
    """Every exported row's thread_id appears under exactly one split directory."""
    seen = {}
    for f in sorted(out.glob("*/*.jsonl")):
        for line in f.read_text(encoding="utf-8").splitlines():
            tid = json.loads(line)["thread_id"]
            if seen.setdefault(tid, f.parent.name) != f.parent.name:
                raise AssertionError(f"thread {tid!r} leaks across splits {seen[tid]} and {f.parent.name}")


def export(conn, out: Path = Path("data"), seed: str = "v1", ratios=RATIOS, version_tag: str = "dev") -> int:
    """Validated records → out/<split>/<thread_id>.jsonl (one file per thread) + out/manifest.json batch facts."""
    threads = defaultdict(list)
    for doc_id, tid in conn.execute(
            "SELECT doc_id, thread_id FROM records WHERE status='validated' ORDER BY thread_id, doc_id"):
        if tid in ("", ".", "..") or Path(tid).name != tid:  # thread_id becomes a filename
            raise ValueError(f"unsafe thread_id {tid!r}")
        threads[tid].append(Record(**record_dict(conn, doc_id)).model_dump_json())
    for split in SPLITS:  # drop stale files from earlier exports; leave anything else under out/ alone
        shutil.rmtree(out / split, ignore_errors=True)
    counts = dict.fromkeys(SPLITS, 0)
    for tid, rows in threads.items():
        split = split_by_thread(tid, seed, ratios)
        f = out / split / f"{tid}.jsonl"
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("".join(r + "\n" for r in rows), encoding="utf-8")
        counts[split] += len(rows)
    assert_no_thread_leakage(out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "manifest.json").write_text(json.dumps(
        {"version_tag": version_tag, "seed": seed, "ratios": dict(zip(SPLITS, ratios)),
         "policy_version": POLICY_VERSION, "records": counts}, indent=2) + "\n", encoding="utf-8")
    return sum(counts.values())


def to_hf(out: Path = Path("data")):
    """The export as an HF DatasetDict for the trainer; empty splits are omitted."""
    from datasets import load_dataset  # heavy import, only the trainer path needs it
    files = {s: str(out / s / "*.jsonl") for s in SPLITS if any((out / s).glob("*.jsonl"))}
    return load_dataset("json", data_files=files)
