from functools import lru_cache

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


def scan(text: str) -> list[dict]:
    """Redaction spans as code-point offsets into text; overlapping hits merged into one span."""
    hits = sorted(analyzer().analyze(text=text, language="en"), key=lambda r: (r.start, -r.end))
    merged: list[dict] = []
    for r in hits:
        if merged and r.start < merged[-1]["end"]:
            m = merged[-1]
            m["end"] = max(m["end"], r.end)
            if r.score > m["score"]:
                m["entity_type"], m["score"] = r.entity_type, r.score
        else:
            merged.append({"entity_type": r.entity_type, "start": r.start, "end": r.end, "score": r.score})
    return merged
