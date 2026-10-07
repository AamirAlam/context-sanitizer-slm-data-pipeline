from labelpipe import db, intake, runner
from labelpipe.parse import parse

SECRET = "sk-live9Zz8Yy7Xx6Ww5Vv4Uu3Tt2Ss1"
# emoji (1 code point, 4 UTF-8 bytes), combining accent (e + U+0301), CJK, CRLF before the secret
RAW = "deploy 😀 café 失败\r\nkey=" + SECRET + " done 👩‍💻\n"


def test_offsets_round_trip_in_code_points(tmp_path):
    f = tmp_path / "unicode.txt"
    f.write_bytes(RAW.encode("utf-8"))
    conn = db.connect(tmp_path / "t.db")
    doc_id = intake.ingest(conn, f)
    runner.run(conn)

    raw = conn.execute("SELECT raw_text FROM records WHERE doc_id=?", (doc_id,)).fetchone()[0]
    assert raw == RAW  # CRLF and combining marks preserved verbatim
    spans = conn.execute("SELECT kind, start, \"end\", label FROM spans WHERE doc_id=?", (doc_id,)).fetchall()
    red = [(s, e) for k, s, e, _ in spans if k == "redaction"]
    assert (RAW.index(SECRET), RAW.index(SECRET) + len(SECRET)) in red
    assert any(raw[s:e] == SECRET for s, e in red)

    doc = parse(raw)
    for kind, s, e, _ in spans:
        if kind == "label":
            assert doc.char_span(s, e, alignment_mode="strict").text == raw[s:e]
