import re
from functools import lru_cache

from detect_secrets.core.scan import _process_line_based_plugins
from detect_secrets.settings import default_settings

from presidio_analyzer import AnalyzerEngine, Pattern, PatternRecognizer
from presidio_analyzer.nlp_engine import SpacyNlpEngine
from presidio_analyzer.predefined_recognizers import EmailRecognizer

from .parse import nlp

SECRET_RECOGNIZERS = [
    PatternRecognizer(supported_entity="API_KEY",
                      patterns=[Pattern("sk_key", r"\bsk-[A-Za-z0-9_-]{20,}", 0.95)]),
    PatternRecognizer(supported_entity="BEARER_TOKEN",
                      patterns=[Pattern("bearer", r"(?<=\bBearer\s+)[A-Za-z0-9._~+/=-]{8,}", 0.95)]),
]


@lru_cache(maxsize=1)
def analyzer() -> AnalyzerEngine:
    engine = SpacyNlpEngine()
    # ponytail: blank pipeline = no NER, so PERSON/LOC are not detected; load en_core_web_* here when available
    engine.nlp = {"en": nlp}
    a = AnalyzerEngine(nlp_engine=engine, supported_languages=["en"])
    # stock EmailRecognizer validates via tldextract, which fetches the suffix list over the network
    a.registry.remove_recognizer("EmailRecognizer")
    a.registry.add_recognizer(PatternRecognizer(supported_entity="EMAIL_ADDRESS",
                                                patterns=EmailRecognizer.PATTERNS))
    for r in SECRET_RECOGNIZERS:
        a.registry.add_recognizer(r)
    return a


TOKEN_TAIL = re.compile(r"[\w.~+/=-]*")


def secrets(text: str) -> list[dict]:
    """detect-secrets plugins in-process; verification (network) is never run."""
    starts, pos = [], 0  # line-start index: code-point offset of each line
    for line in text.splitlines(keepends=True):
        starts.append(pos)
        pos += len(line)
    lines = text.splitlines()
    with default_settings() as s:
        # verification only runs when this filter is configured; make sure it never is
        s.disable_filters("detect_secrets.filters.common.is_ignored_due_to_verification_policies")
        # ponytail: private but stable (1.5.x) line scanner; public scan_file would need raw text on disk
        found = list(_process_line_based_plugins(list(enumerate(lines, 1)), filename="raw"))
    out = []
    for f in found:
        line = lines[f.line_number - 1]
        for m in re.finditer(re.escape(f.secret_value), line):
            # plugins may return only a prefix (ghp, JWT header.payload.): extend to the token's end
            end = TOKEN_TAIL.match(line, m.end()).end()
            base = starts[f.line_number - 1]
            out.append({"entity_type": f.type.upper().replace(" ", "_"),
                        "start": base + m.start(), "end": base + end, "score": 1.0, "secret": True})
    return out


def scan(text: str) -> list[dict]:
    """Redaction spans as code-point offsets into text; overlapping hits merged into one span
    covering all of them, typed by the secret scanner when it is involved (secret wins)."""
    hits = [{"entity_type": r.entity_type, "start": r.start, "end": r.end, "score": r.score, "secret": False}
            for r in analyzer().analyze(text=text, language="en")] + secrets(text)
    hits.sort(key=lambda h: (h["start"], -h["end"], not h["secret"], -h["score"], h["entity_type"]))
    merged: list[dict] = []
    for h in hits:
        if merged and h["start"] < merged[-1]["end"]:
            m = merged[-1]
            m["end"] = max(m["end"], h["end"])
            if (h["secret"], h["score"]) > (m["secret"], m["score"]):
                m.update(entity_type=h["entity_type"], score=h["score"], secret=h["secret"])
        else:
            merged.append(dict(h))
    return [{k: m[k] for k in ("entity_type", "start", "end", "score")} for m in merged]
