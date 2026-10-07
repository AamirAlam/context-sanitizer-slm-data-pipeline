from pydantic import ValidationError

from .schema import Record


def validate(record: dict, doc) -> list[str]:
    """Reasons the record fails the gate; empty means valid. doc is the spaCy parse of the raw text."""
    raw = doc.text
    try:
        Record.model_validate(record, context={"raw": raw})
        reasons = []
    except ValidationError as e:
        reasons = [line for err in e.errors() for line in err["msg"].removeprefix("Value error, ").splitlines()]
    return reasons + [f"{s.label} [{s.start}, {s.end}): not on token boundaries"
                      for s in record["labels"]
                      if 0 <= s.start < s.end <= len(raw) and doc.char_span(s.start, s.end, alignment_mode="strict") is None]
