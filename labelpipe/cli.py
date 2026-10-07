import argparse
import os
from pathlib import Path

from . import dataset, db, intake, runner


def main(argv=None):
    p = argparse.ArgumentParser(prog="labelpipe")
    p.add_argument("--db", default=os.environ.get("LABELPIPE_DB", "labelpipe.db"))
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("ingest").add_argument("dir", type=Path)
    sub.add_parser("run")
    s = sub.add_parser("serve")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8000)
    e = sub.add_parser("export")
    e.add_argument("--out", type=Path, default=Path("data"))
    e.add_argument("--seed", default="v1")
    e.add_argument("--version-tag", default="dev")
    a = p.parse_args(argv)

    if a.cmd == "serve":
        import uvicorn
        from .review.api import create_app
        uvicorn.run(create_app(a.db), host=a.host, port=a.port)
        return
    conn = db.connect(a.db)
    if a.cmd == "ingest":
        files = sorted(f for f in a.dir.iterdir() if f.is_file())
        for f in files:
            intake.ingest(conn, f)
        print(f"ingested {len(files)} file(s)")
    elif a.cmd == "run":
        print(f"advanced {runner.run(conn)} record(s)")
    elif a.cmd == "export":
        n = dataset.export(conn, a.out, seed=a.seed, version_tag=a.version_tag)
        print(f"exported {n} record(s) to {a.out}")


if __name__ == "__main__":
    main()
