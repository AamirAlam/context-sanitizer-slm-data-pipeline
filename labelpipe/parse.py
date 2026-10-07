import spacy

nlp = spacy.blank("en")  # tokenizer only: non-destructive, no model download


def parse(raw: str):
    doc = nlp(raw)
    assert doc.text == raw, "parse must not alter raw text"
    return doc
