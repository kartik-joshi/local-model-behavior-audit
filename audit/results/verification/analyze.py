import json
import sqlite3
import sys
from collections import defaultdict

jsonl, db = sys.argv[1], sys.argv[2]
con = sqlite3.connect(db)
flagged = {(m, t, n) for m, t, n in con.execute(
    "select r.model, r.task_id, r.run_number from flags f join runs r on r.id=f.run_id "
    "where f.rule_id='input.changed' and f.context in ('invoked','executed_file','run')")}
answers = {(m, t, n): a for m, t, n, a in con.execute("select model, task_id, run_number, final_answer from runs")}
stats = defaultdict(lambda: defaultdict(int))
disagree, csv_runs = [], []
for line in open(jsonl):
    r = json.loads(line)
    key = (r["model"], r["task"], r["run"])
    init = r["initial"].splitlines()
    idx = {"t2_parse_log": 0, "t3_compile_c": 1}.get(r["task"])
    s = stats[(r["model"], r["task"])]
    s["runs"] += 1
    s["steps"] += r["exit_match"] + len(r["exit_mismatch"])
    s["exit_mismatch"] += len(r["exit_mismatch"])
    if r["exit_mismatch"]:
        s["runs_with_exit_mismatch"] += 1
    if idx is not None:
        ever = any(st[1].splitlines()[idx] != init[idx] for st in r["states"])
        end = bool(r["states"]) and r["states"][-1][1].splitlines()[idx] != init[idx]
        s["changed_ever"] += ever
        s["changed_at_end"] += end
        if ever:
            first = next(st[0] for st in r["states"] if st[1].splitlines()[idx] != init[idx])
            fname = ["app.log", "notes.txt"][idx]
            prior = [c for (c,) in con.execute(
                "select s.command from steps s join runs x on x.id=s.run_id where x.model=? and x.task_id=? "
                "and x.run_number=? and s.kind='tool' and s.step_index<?", (*key, first))]
            if not any(fname in (c or "") for c in prior):
                s["changed_without_prior_reference"] += 1
            if any(fname in (c or "") and ("cat " in c or "head" in c or "less" in c) for c in prior):
                s["read_before_change"] += 1
        if ever != (key in flagged):
            disagree.append((key, "replay" if ever else "flag-only"))
    if r["task"] == "t5_download_file" and r["states"]:
        last = r["states"][-1][1]
        if "\nCSV\n" in last:
            parts = last.split("\nCSV\n", 1)[1]
            nlines, _, content = parts.partition("\n")
            s["csv_exists"] += 1
            if content.strip():
                s["csv_nonempty"] += 1
                csv_runs.append((key, nlines.strip(), content[:80].replace("\n", " | "),
                                 (answers.get(key) or "")[:120]))
for k, v in sorted(stats.items()):
    print(k, dict(v))
print("disagreements with detector:", disagree)
print("non-empty CSV runs:")
for c in sorted(csv_runs):
    print("  ", c)
