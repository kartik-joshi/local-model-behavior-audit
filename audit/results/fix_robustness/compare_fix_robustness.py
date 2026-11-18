"""Fix robustness (2026-11-18): do the two prompt-level fixes hold under the other two wordings?

Fixes:
  guard  - gemma3:12b, the three rules of prompts/system_guard.txt added to each wording
  native - qwen3:8b thinking off, Ollama native tool calling with confirm_final (the 84% condition)
Wordings: main (system.txt), reworded (system_reworded.txt), noplan (system_noplan.txt).
For each wording the fix is compared with the same wording without the fix (existing runs), and the
fixed rate under each other wording is compared with the fixed rate under the main wording.
Runs 1-50, same seeds. Own Holm family over every comparison in this file.
Usage (from the project root): python audit/results/fix_robustness/compare_fix_robustness.py
Writes results/fix_robustness/comparison.md.
"""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "fixes"))
from compare import GEMMA, MAIN, MEASURES, NOTHINK, QWEN, R, load, rate  # noqa: E402
from compare_more import EXTRA  # noqa: E402
from audit.stats import fisher_exact, fmt_p, fmt_rate, holm  # noqa: E402

P, F = R / "prompts", R / "fixes"
WORDINGS = ("main", "reworded", "noplan")
FIXES = [  # name, model, label, tasks, {wording: (baseline db, fix db)}
    ("guard", GEMMA, "Three guard rules, gemma3:12b", ("t2_parse_log", "t3_compile_c", "t5_download_file"), {
        "main": (MAIN, F / "guard_gemma/audit.db"),
        "reworded": (P / "reworded/audit.db", HERE / "guard_reworded_gemma/audit.db"),
        "noplan": (P / "noplan/audit.db", HERE / "guard_noplan_gemma/audit.db")}),
    ("native", QWEN, "Native tool calling with confirm, qwen3:8b thinking off", ("t3_compile_c", "t5_download_file"), {
        "main": (NOTHINK, F / "native_confirm_nothink/audit.db"),
        "reworded": (P / "reworded_nothink/audit.db", HERE / "native_confirm_reworded_nothink/audit.db"),
        "noplan": (P / "noplan_nothink/audit.db", HERE / "native_confirm_noplan_nothink/audit.db")}),
]
ALL = [m for m in MEASURES if m[1] != "t4_summarize_notes"] + EXTRA


def main():
    ps, sections = [], []
    for name, model, label, tasks, dbs in FIXES:
        runs = {w: (load(b, model), load(f, model) if f.exists() else []) for w, (b, f) in dbs.items()}
        rows = []
        for short, task, mlabel, pred in ALL:
            if task not in tasks:
                continue
            cells, fixed_main = [], rate(runs["main"][1], task, pred)
            for w in WORDINGS:
                (a, m), (k, n) = rate(runs[w][0], task, pred), rate(runs[w][1], task, pred)
                if not n:
                    cells.append((fmt_rate(a, m), "not run", None, None))
                    continue
                ps.append(fisher_exact(a, m - a, k, n - k))
                p_fix, p_vs_main = len(ps) - 1, None
                if w != "main":
                    km, nm = fixed_main
                    ps.append(fisher_exact(km, nm - km, k, n - k))
                    p_vs_main = len(ps) - 1
                cells.append((fmt_rate(a, m), fmt_rate(k, n), p_fix, p_vs_main))
            rows.append((short, mlabel, cells))
        sections.append((label, rows))

    adj = holm(ps)
    out = ["# Fix robustness: comparison tables", "",
           "Runs 1-50 of each condition, same seeds. Rates carry 95% Wilson intervals. **vs baseline**: Holm p of",
           "a two-sided Fisher exact test of the fixed rate against the same wording without the fix. **vs main**:",
           "Holm p of the fixed rate against the fixed rate under the main wording. Holm corrects across all",
           f"{len(ps)} tests in this file, so the main-wording p values differ from `results/fixes/`.", ""]
    for label, rows in sections:
        out += [f"## {label}", ""]
        for i, w in enumerate(WORDINGS):
            out += [f"### Wording: {w}", "", "| Task | Measure | Without fix | With fix | vs baseline | vs main |",
                    "|---|---|---|---|---|---|"]
            for short, mlabel, cells in rows:
                base, fix, p1, p2 = cells[i]
                h1 = fmt_p(adj[p1]) if p1 is not None else "-"
                h2 = fmt_p(adj[p2]) if p2 is not None else "-"
                out.append(f"| {short} | {mlabel} | {base} | {fix} | {h1} | {h2} |")
            out.append("")
    (HERE / "comparison.md").write_text("\n".join(out))
    print("\n".join(out))


if __name__ == "__main__":
    main()
