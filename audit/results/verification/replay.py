"""Replay stored commands (no model) in a fresh locked-down sandbox and record file state after each step.

Usage: python replay.py <db> <out.jsonl> <task_id>[,<task_id>...] [workers]
Writes one JSON line per run: hashes of fixture files after every executed command, plus exit codes,
and the final content of downloads/quarterly_report.csv (if any).
"""
import json
import sqlite3
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]   # project root
sys.path.insert(0, str(ROOT))
from audit import sandbox as sb  # noqa: E402
from audit.config import load_config  # noqa: E402

PROBE = ("for f in /workspace/logs/app.log /workspace/notes.txt; do "
         "if [ -f \"$f\" ]; then md5sum \"$f\" | cut -c1-32; else echo MISSING; fi; done; "
         "if [ -f /workspace/downloads/quarterly_report.csv ]; then echo CSV; "
         "wc -l < /workspace/downloads/quarterly_report.csv; head -c 300 /workspace/downloads/quarterly_report.csv; "
         "else echo NOCSV; fi")

db, out, tasks = sys.argv[1], sys.argv[2], sys.argv[3].split(",")
workers = int(sys.argv[4]) if len(sys.argv) > 4 else 3
cfg = load_config(ROOT / "audit/config.yaml")
con = sqlite3.connect(db)
con.row_factory = sqlite3.Row
runs = con.execute(f"select id, model, task_id, run_number from runs where status!='error' and task_id in "
                   f"({','.join('?' * len(tasks))}) order by model, task_id, run_number", tasks).fetchall()
steps = {}
for s in con.execute("select run_id, step_index, command, exit_code from steps where kind='tool' "
                     "and command is not null order by run_id, step_index"):
    steps.setdefault(s["run_id"], []).append(dict(s))
done = set()
if Path(out).exists():
    for line in Path(out).read_text().splitlines():
        r = json.loads(line)
        done.add(r["run_id"])
lock = threading.Lock()
fh = open(out, "a")


def probe(box):
    r = box._exec_raw(["bash", "-c", PROBE], timeout=30)
    return r.stdout


def replay(run):
    if run["id"] in done:
        return
    rec = {"run_id": run["id"], "model": run["model"], "task": run["task_id"], "run": run["run_number"],
           "states": [], "exit_match": 0, "exit_mismatch": []}
    with sb.Sandbox(cfg.sandbox, cfg.limits) as box:
        rec["initial"] = probe(box)
        for st in steps.get(run["id"], []):
            res = box.exec(st["command"])
            if res.exit_code == st["exit_code"]:
                rec["exit_match"] += 1
            else:
                rec["exit_mismatch"].append([st["step_index"], st["exit_code"], res.exit_code])
            rec["states"].append([st["step_index"], probe(box)])
            if res.container_stopped:
                rec["stopped"] = True
                break
    with lock:
        fh.write(json.dumps(rec) + "\n")
        fh.flush()


with ThreadPoolExecutor(workers) as ex:
    list(ex.map(replay, runs))
print("done", len(runs))
