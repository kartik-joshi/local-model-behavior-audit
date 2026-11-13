"""Make a scrubbed copy of the raw run data for public release.

Copies every results database (not the pre-rescan backups) and every .jsonl file under audit/results/ into
release/data/, keeping the folder layout. In each copy, the project folder, the home folder and the local
user name are replaced with neutral text in every text cell and line. It then checks that none of them is
left, writes SHA256SUMS and packs release/model-audit-data.tar.gz. The originals are not changed.

Run from the project root:  .venv/bin/python audit/export_release.py
"""
import getpass
import hashlib
import shutil
import sqlite3
import sys
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "audit" / "results"
OUT = ROOT / "release" / "data"
# Longest first, so the project folder becomes "." before the home folder is replaced.
REPLACE = [(str(ROOT), "."), (str(Path.home()), "~"), (getpass.getuser(), "user")]
NEEDLES = [old for old, _ in REPLACE]


def scrub(text: str) -> str:
    for old, new in REPLACE:
        text = text.replace(old, new)
    return text


def export_db(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists():
        dst.unlink()
    with sqlite3.connect(f"file:{src}?mode=ro", uri=True) as s, sqlite3.connect(dst) as d:
        s.backup(d)                      # folds in any WAL contents
    con = sqlite3.connect(dst)
    tables = [r[0] for r in con.execute("select name from sqlite_master where type='table'")]
    for t in tables:
        cols = [r[1] for r in con.execute(f"pragma table_info({t})") if r[2].upper() in ("TEXT", "")]
        for c in cols:
            for old, new in REPLACE:
                con.execute(f'update "{t}" set "{c}" = replace("{c}", ?, ?) where instr("{c}", ?) > 0',
                            (old, new, old))
    con.commit()
    con.execute("pragma journal_mode=delete")
    con.execute("vacuum")
    con.close()


def leftovers_db(path: Path) -> int:
    con = sqlite3.connect(path)
    n = 0
    for (t,) in con.execute("select name from sqlite_master where type='table'").fetchall():
        for c in [r[1] for r in con.execute(f"pragma table_info({t})")]:
            for needle in NEEDLES:
                n += con.execute(f'select count(*) from "{t}" where instr(cast("{c}" as text), ?) > 0',
                                 (needle,)).fetchone()[0]
    con.close()
    return n


def main() -> int:
    if OUT.exists():
        shutil.rmtree(OUT)
    files = []
    for src in sorted(RESULTS.rglob("audit.db")):
        dst = OUT / src.relative_to(RESULTS)
        export_db(src, dst)
        files.append(dst)
    for src in sorted(RESULTS.rglob("*.jsonl")):
        dst = OUT / src.relative_to(RESULTS)
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_text(scrub(src.read_text()))
        files.append(dst)

    bad = []
    for f in files:
        n = leftovers_db(f) if f.suffix == ".db" else sum(f.read_text().count(x) for x in NEEDLES)
        if n:
            bad.append((f, n))
    if bad:
        for f, n in bad:
            print(f"NOT CLEAN: {f.relative_to(ROOT)} ({n} matches)")
        return 1

    sums = OUT / "SHA256SUMS"
    sums.write_text("".join(f"{hashlib.sha256(f.read_bytes()).hexdigest()}  {f.relative_to(OUT)}\n"
                            for f in files))
    archive = OUT.parent / "model-audit-data.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        tar.add(OUT, arcname="data")
    size = sum(f.stat().st_size for f in files)
    print(f"{len(files)} files, {size / 1e6:.0f} MB uncompressed, clean. Archive: {archive.relative_to(ROOT)} "
          f"({archive.stat().st_size / 1e6:.0f} MB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
