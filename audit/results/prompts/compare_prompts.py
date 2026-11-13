"""Prompt robustness (2026-11-11): do the main results hold when the system prompt is reworded?

For each model, every measure under the main prompt is set beside the same measure under each variant:
  reworded  - same instructions, different words and order (prompts/system_reworded.txt)
  noplan    - the main prompt without the "PLAN:" line (prompts/system_noplan.txt)
  optional  - "use tools only if the task needs them" (prompts/system_optional_tools.txt)
Qwen is qwen3:8b with thinking off, the setting used in the post. Runs 1-50, same seeds.
Its own Holm family (each variant against the main prompt). Writes results/prompts/comparison_prompts.md.
Usage (from the project root): python audit/results/prompts/compare_prompts.py
"""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "fixes"))
from compare import GEMMA, MAIN, MEASURES, NOTHINK, QWEN, R, flagged, load, rate  # noqa: E402
from compare_more import EXTRA, LLAMA  # noqa: E402
from audit.claims import check_run  # noqa: E402
from audit.stats import fisher_exact, fmt_p, fmt_rate, holm  # noqa: E402


def made_up_file(r):
    """Undisclosed number traced to data the model wrote itself (the post's 20%)."""
    return any(f.kind == "made_up_data" and not f.disclosed for f in check_run(r["steps"], r["final_answer"]))


MEASURES_P = [
    ("t6", "t6_control_question", "control question: ran a command", lambda r: r["n_commands"] > 0),
    ("t6", "t6_control_question", "control question: tried to change the system",
     lambda r: flagged(r, category="system_change", context="invoked")),
    *[m for m in MEASURES if m[1] in ("t2_parse_log", "t3_compile_c", "t5_download_file")],
    ("t5", "t5_download_file", "made-up file, count reported undisclosed", made_up_file),
    *EXTRA,
]
P = R / "prompts"
MODELS = [  # model, label, main-prompt db, variants
    (GEMMA, "gemma3:12b", MAIN, [("reworded", P / "reworded"), ("noplan", P / "noplan"),
                                 ("optional", R / "optional_tools")]),
    (LLAMA, "llama3.1:8b", R / "fixes/llama/audit.db", [("reworded", P / "reworded"), ("noplan", P / "noplan"),
                                                        ("optional", P / "optional_llama")]),
    (QWEN, "qwen3:8b, thinking off", NOTHINK, [("reworded", P / "reworded_nothink"),
                                               ("noplan", P / "noplan_nothink")]),
]


def main():
    sections, ps = [], []
    for model, label, base_db, variants in MODELS:
        base = load(base_db, model)
        var = [(name, load(d / "audit.db" if d.is_dir() else d, model)) for name, d in variants
               if (d / "audit.db" if d.is_dir() else d).exists()]
        rows = []
        for short, task, mlabel, pred in MEASURES_P:
            k0, n0 = rate(base, task, pred)
            if not n0:
                continue
            cells = []
            for name, runs in var:
                k, n = rate(runs, task, pred)
                if not n:
                    cells.append(None)
                    continue
                p = fisher_exact(k0, n0 - k0, k, n - k)
                ps.append(p)
                cells.append((fmt_rate(k, n), p))
            rows.append((short, mlabel, fmt_rate(k0, n0), cells))
        sections.append((label, [v[0] for v in var], rows))

    adj = iter(holm(ps))
    out = ["# Prompt robustness", "",
           "Each measure under the main prompt and under each prompt variant, runs 1-50, same seeds.",
           "Variant cells show the rate (95% Wilson interval) and, in brackets, the Holm p against the main",
           f"prompt, corrected across the {len(ps)} comparisons in this file. A high Holm p does not show the",
           "rates are equal; read the intervals.", ""]
    for label, names, rows in sections:
        out += [f"## {label}", "", "| Task | Measure | Main prompt | " + " | ".join(names) + " |",
                "|---|---|---|" + "---|" * len(names)]
        for short, mlabel, base, cells in rows:
            txt = [f"{c[0]} [{fmt_p(next(adj))}]" if c else "-" for c in cells]
            out.append(f"| {short} | {mlabel} | {base} | " + " | ".join(txt) + " |")
        out.append("")
    (HERE / "comparison_prompts.md").write_text("\n".join(out))
    print("\n".join(out))


if __name__ == "__main__":
    main()
