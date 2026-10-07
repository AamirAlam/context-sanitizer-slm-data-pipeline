"""Candidate checks, run before scanning so bad inputs never reach review.
Duplicates need no check here: intake keys doc_id on content_sha256 and INSERT OR IGNOREs repeats."""
import re

# ponytail: fixed marker list; add markers as new dump sources show their own
TRUNCATED = re.compile(r"\[truncated\]|<truncated>|\(truncated\)|output truncated", re.IGNORECASE)


def candidate_problems(raw: str) -> list[str]:
    problems = []
    if not raw.strip():
        problems.append("empty dump")
    if "\x00" in raw:
        problems.append("binary content (NUL bytes)")
    if TRUNCATED.search(raw):
        problems.append("truncated dump (truncation marker found)")
    return problems
