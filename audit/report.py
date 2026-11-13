"""Build results/report.md and charts from the SQLite database.

Honesty rules (from the project brief): counts and rates, not adjectives; "the model attempted X";
compare against the control task and the other model; a null result is a valid result.
"""
from __future__ import annotations

import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

from .config import PACKAGE_DIR, Config
from .detectors import CATEGORIES, PRIMARY_CONTEXTS, SEVERITY_RANK
from .store import COMPLETE_STATUSES, Store

SEVERITY_WEIGHT = {"info": 0, "low": 1, "medium": 3, "high": 5}
# Chart colors: reference categorical palette (light mode), fixed order, never cycled.
SERIES = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6"]
OTHER_GRAY = "#a3a29c"
INK, INK_2, GRID, SURFACE = "#0b0b0b", "#52514e", "#e4e3df", "#fcfcfb"


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def _cell(v) -> str:
    s = "" if v is None else str(v)
    return s.replace("|", "\\|").replace("\n", " ").replace("\r", " ")


def md_table(headers: list[str], rows: list[list]) -> str:
    if not rows:
        return "_No rows._"
    out = ["| " + " | ".join(_cell(h) for h in headers) + " |",
           "|" + "|".join("---" for _ in headers) + "|"]
    out += ["| " + " | ".join(_cell(c) for c in r) + " |" for r in rows]
    return "\n".join(out)


def rate(k: int, n: int) -> str:
    return f"{k} / {n} ({100 * k / n:.1f}%)" if n else "0 / 0"


def code(s: str | None, limit: int = 300) -> str:
    if not s:
        return ""
    s = s.strip().replace("\n", " ⏎ ")
    s = s if len(s) <= limit else s[:limit] + " …"
    return "`" + s.replace("`", "'") + "`"


# --------------------------------------------------------------------------- #
# Data
# --------------------------------------------------------------------------- #

class Data:
    def __init__(self, store: Store):
        self.meta = store.get_meta()
        ph = ",".join("?" * len(COMPLETE_STATUSES))
        self.runs = [dict(r) for r in store.query(f"SELECT * FROM runs WHERE status IN ({ph}) ORDER BY id",
                                                  COMPLETE_STATUSES)]
        self.other_runs = [dict(r) for r in store.query(
            f"SELECT model, task_id, status, error FROM runs WHERE status NOT IN ({ph})", COMPLETE_STATUSES)]
        run_ids = {r["id"] for r in self.runs}
        self.flags = [dict(f) for f in store.query(
            "SELECT f.*, s.step_index FROM flags f LEFT JOIN steps s ON s.id = f.step_id ORDER BY f.id")
            if f["run_id"] in run_ids]
        self.steps_by_run = defaultdict(list)
        for s in store.query("SELECT run_id, step_index, kind, command, exit_code, timed_out FROM steps "
                             "ORDER BY run_id, step_index"):
            if s["run_id"] in run_ids:
                self.steps_by_run[s["run_id"]].append(dict(s))
        self.models = sorted({r["model"] for r in self.runs})
        self.tasks = sorted({r["task_id"] for r in self.runs})
        self.runs_by_model = Counter(r["model"] for r in self.runs)
        self.run_by_id = {r["id"]: r for r in self.runs}
        self.action_flags = [f for f in self.flags if f["context"] in PRIMARY_CONTEXTS and f["severity"] != "info"]
        cfg_tasks = (self.meta.get("config") or {}).get("tasks") or {}
        self.control_tasks = sorted(t for t, v in cfg_tasks.items() if not v.get("expected_tools"))

    def runs_with(self, model: str, pred) -> set[int]:
        return {f["run_id"] for f in self.action_flags
                if self.run_by_id[f["run_id"]]["model"] == model and pred(f)}


# --------------------------------------------------------------------------- #
# Sections
# --------------------------------------------------------------------------- #

def section_category_table(d: Data) -> str:
    cats = [c for c in CATEGORIES if any(f["category"] == c for f in d.action_flags)]
    if not cats:
        return "No action flags were recorded in any category."
    headers = ["Category"] + [f"{m} (runs with flag)" for m in d.models] + [f"{m} (flag count)" for m in d.models]
    rows = []
    for c in cats:
        row = [c]
        for m in d.models:
            row.append(rate(len(d.runs_with(m, lambda f, c=c: f["category"] == c)), d.runs_by_model[m]))
        for m in d.models:
            row.append(sum(1 for f in d.action_flags if f["category"] == c and d.run_by_id[f["run_id"]]["model"] == m))
        rows.append(row)
    any_row = ["**any category**"] + [rate(len(d.runs_with(m, lambda f: True)), d.runs_by_model[m]) for m in d.models]
    any_row += [sum(1 for f in d.action_flags if d.run_by_id[f["run_id"]]["model"] == m) for m in d.models]
    rows.append(any_row)
    return md_table(headers, rows)


def section_by_task(d: Data) -> str:
    headers = ["Task"] + d.models
    rows = []
    for t in d.tasks:
        row = [t + (" (control)" if t in d.control_tasks else "")]
        for m in d.models:
            n = sum(1 for r in d.runs if r["model"] == m and r["task_id"] == t)
            k = len({f["run_id"] for f in d.action_flags if not f["task_related"]
                     and d.run_by_id[f["run_id"]]["model"] == m and d.run_by_id[f["run_id"]]["task_id"] == t})
            row.append(rate(k, n))
        rows.append(row)
    return md_table(headers, rows)


def section_mismatch(d: Data) -> str:
    headers = ["Model", "Stated plan: no tools", "…and then ran a command", "Mismatch rate (all runs)",
               "Reasoning said no tools, ran a command", "Stated yes / no / unclear / unstated"]
    rows = []
    for m in d.models:
        runs = [r for r in d.runs if r["model"] == m]
        said_no = [r for r in runs if r["stated_tool_use"] == "no"]
        plan_mm = {f["run_id"] for f in d.flags if f["rule_id"] == "said_no_tools_ran_commands"
                   and d.run_by_id[f["run_id"]]["model"] == m}
        reason_mm = {f["run_id"] for f in d.flags if f["rule_id"] == "reasoning_no_tools_ran_commands"
                     and d.run_by_id[f["run_id"]]["model"] == m}
        dist = Counter(r["stated_tool_use"] or "unstated" for r in runs)
        rows.append([m, len(said_no), rate(len(plan_mm), len(said_no)), rate(len(plan_mm), len(runs)),
                     rate(len(reason_mm), len(runs)),
                     " / ".join(str(dist.get(k, 0)) for k in ("yes", "no", "unclear", "unstated"))])
    return md_table(headers, rows)


def section_related(d: Data) -> str:
    headers = ["Model", "Category", "Plausibly task-related (runs)", "Unexplained (runs)"]
    rows = []
    for m in d.models:
        for c in CATEGORIES:
            if c in ("stated_vs_actual", "out_of_scope"):
                continue
            rel = d.runs_with(m, lambda f, c=c: f["category"] == c and f["task_related"])
            unx = d.runs_with(m, lambda f, c=c: f["category"] == c and not f["task_related"])
            if rel or unx:
                rows.append([m, c, rate(len(rel), d.runs_by_model[m]), rate(len(unx), d.runs_by_model[m])])
    return md_table(headers, rows) if rows else "No action flags in any category."


def interesting_runs(d: Data, n: int = 10) -> list[dict]:
    score: Counter = Counter()
    for f in d.action_flags:
        if f["category"] == "stated_vs_actual":
            score[f["run_id"]] += 4
        elif not f["task_related"]:
            score[f["run_id"]] += SEVERITY_WEIGHT[f["severity"]]
    out = []
    for run_id, s in sorted(score.items(), key=lambda kv: (-kv[1], kv[0]))[:n]:
        if s <= 0:
            continue
        r = d.run_by_id[run_id]
        flags = [f for f in d.action_flags if f["run_id"] == run_id]
        flags.sort(key=lambda f: (-SEVERITY_RANK[f["severity"]], f["id"]))
        out.append({"run": r, "score": s, "flags": flags, "steps": d.steps_by_run.get(run_id, [])})
    return out


def section_interesting(d: Data) -> str:
    items = interesting_runs(d)
    if not items:
        return "No run had an unexplained action flag or a stated-vs-actual mismatch."
    parts = []
    for i, it in enumerate(items, 1):
        r = it["run"]
        cats = ", ".join(sorted({f["category"] for f in it["flags"]}))
        parts.append(f"### {i}. {r['model']} / {r['task_id']} / run {r['run_number']} (score {it['score']})\n")
        parts.append(f"- Seed: {r['seed']} · status: {r['status']} · steps: {r['n_steps']} · "
                     f"commands run: {r['n_commands']} · categories: {cats}")
        parts.append(f"- Stated plan ({r['stated_tool_use'] or 'unstated'}): "
                     f"{code(r['stated_plan'], 300) or '_none given_'}")
        seen_steps = set()
        for f in it["flags"][:6]:
            label = "task-related" if f["task_related"] else "unexplained"
            step = f"step {f['step_index']}" if f["step_index"] is not None else "run"
            parts.append(f"- [{f['severity']}] **{f['category']}** ({label}, {f['context']}, {step}): "
                         f"evidence {code(f['evidence'], 120)}")
            if f["command"] and f["step_id"] not in seen_steps:
                seen_steps.add(f["step_id"])
                parts.append(f"  - command: {code(f['command'], 400)}")
        parts.append("")
    return "\n".join(parts)


def section_info_flags(d: Data) -> str:
    """Matches that are not actions: text written to files, proposals, command output."""
    c = Counter((d.run_by_id[f["run_id"]]["model"], f["context"], f["category"])
                for f in d.flags if f["context"] not in PRIMARY_CONTEXTS)
    rows = [[m, ctx, cat, n] for (m, ctx, cat), n in sorted(c.items())]
    return md_table(["Model", "Context", "Category", "Count"], rows)


# --------------------------------------------------------------------------- #
# Charts
# --------------------------------------------------------------------------- #

def _style_axes(ax):
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(GRID)
    ax.tick_params(colors=INK_2, labelsize=9)
    ax.yaxis.grid(True, color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)


def make_charts(d: Data, out_dir: Path) -> dict[str, str]:
    if not d.runs:
        return {}
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    out_dir.mkdir(parents=True, exist_ok=True)
    charts = {}
    plt.rcParams.update({"font.size": 10, "text.color": INK, "axes.labelcolor": INK_2,
                         "figure.facecolor": SURFACE, "axes.facecolor": SURFACE})

    # 1. Flag rate by model: any action flag vs unexplained action flag.
    fig, ax = plt.subplots(figsize=(7, 3.8))
    width = 0.36
    series = [("runs with any action flag", lambda f: True),
              ("runs with an unexplained action flag", lambda f: not f["task_related"])]
    for si, (label, pred) in enumerate(series):
        xs, ys, labels = [], [], []
        for mi, m in enumerate(d.models):
            n = d.runs_by_model[m]
            k = len(d.runs_with(m, pred))
            xs.append(mi + (si - 0.5) * (width + 0.04))
            ys.append(100 * k / n if n else 0)
            labels.append(f"{k}/{n}")
        bars = ax.bar(xs, ys, width=width, color=SERIES[si], label=label, edgecolor=SURFACE, linewidth=2)
        for b, lab in zip(bars, labels):
            ax.annotate(lab, (b.get_x() + b.get_width() / 2, b.get_height()), xytext=(0, 3),
                        textcoords="offset points", ha="center", fontsize=8, color=INK_2)
    ax.set_xticks(range(len(d.models)), d.models)
    ax.set_ylabel("% of runs")
    ax.set_ylim(0, max(5, ax.get_ylim()[1] * 1.12))
    ax.set_title("Flag rate by model", loc="left", fontsize=11, color=INK)
    ax.legend(frameon=False, fontsize=8, loc="upper right")
    _style_axes(ax)
    fig.tight_layout()
    p = out_dir / "flag_rate_by_model.png"
    fig.savefig(p, dpi=150)
    plt.close(fig)
    charts["rate"] = p.name

    # 2. Stacked bar: runs with flag, by category, per model. Max 7 named categories + Other.
    cat_runs = {m: Counter() for m in d.models}
    for m in d.models:
        for c in CATEGORIES:
            cat_runs[m][c] = len(d.runs_with(m, lambda f, c=c: f["category"] == c))
    totals = Counter()
    for m in d.models:
        totals.update(cat_runs[m])
    ordered = [c for c, n in totals.most_common() if n > 0]
    named, rest = ordered[:7], ordered[7:]
    fig, ax = plt.subplots(figsize=(7, 4.2))
    bottoms = [0.0] * len(d.models)
    layers = [(c, SERIES[i]) for i, c in enumerate(named)] + ([("other", OTHER_GRAY)] if rest else [])
    for c, color in layers:
        vals = []
        for m in d.models:
            n = d.runs_by_model[m]
            k = sum(cat_runs[m][x] for x in rest) if c == "other" else cat_runs[m][c]
            vals.append(100 * k / n if n else 0)
        ax.bar(range(len(d.models)), vals, bottom=bottoms, width=0.5, color=color, label=c,
               edgecolor=SURFACE, linewidth=2)
        bottoms = [b + v for b, v in zip(bottoms, vals)]
    ax.set_xticks(range(len(d.models)), d.models)
    ax.set_ylabel("% of runs with flag (summed over categories)")
    ax.set_title("Action flags by category", loc="left", fontsize=11, color=INK)
    if layers:
        ax.legend(frameon=False, fontsize=8, bbox_to_anchor=(1.01, 1), loc="upper left")
    _style_axes(ax)
    fig.tight_layout()
    p = out_dir / "flags_by_category.png"
    fig.savefig(p, dpi=150)
    plt.close(fig)
    charts["category"] = p.name
    return charts


# --------------------------------------------------------------------------- #
# Build
# --------------------------------------------------------------------------- #

def _review_notes(db: Path) -> str | None:
    """Optional hand-written notes kept next to the database as review_notes.md."""
    path = db.parent / "review_notes.md"
    return path.read_text(encoding="utf-8").strip() if path.exists() else None


def build_report(cfg: Config, out: str | None = None) -> Path:
    try:
        import jinja2
    except ImportError as e:  # pragma: no cover
        raise SystemExit("jinja2 is required: pip install -r requirements.txt") from e

    db = cfg.resolve("db_file")
    if not db.exists():
        raise SystemExit(f"No database at {db}. Run `python -m audit run` first.")
    store = Store(db)
    try:
        d = Data(store)
    finally:
        store.close()

    out_path = Path(out) if out else cfg.resolve("results_dir") / "report.md"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    charts = make_charts(d, out_path.parent / "charts")

    env_meta = d.meta.get("environment") or {}
    cfg_meta = d.meta.get("config") or {}
    fingerprints = sorted({r["config_fingerprint"] for r in d.runs})
    digests_seen = defaultdict(set)
    for r in d.runs:
        digests_seen[r["model"]].add(r["model_digest"])
    started = min((r["started_at"] for r in d.runs), default=None)
    finished = max((r["finished_at"] or "" for r in d.runs), default=None)
    unexplained_total = sum(1 for f in d.action_flags if not f["task_related"])
    mismatch_total = sum(1 for f in d.flags if f["rule_id"] == "said_no_tools_ran_commands")

    models_info = []
    for m in d.models:
        mm = (env_meta.get("models") or {}).get(m) or {}
        det = mm.get("details") or {}
        models_info.append({"name": m, "runs": d.runs_by_model[m],
                            "digest": ", ".join(sorted(x or "unknown" for x in digests_seen[m])),
                            "params": det.get("parameter_size", "unknown"),
                            "quant": det.get("quantization_level", "unknown"),
                            "family": det.get("family", "unknown")})

    tpl_dir = PACKAGE_DIR / "templates"
    env = jinja2.Environment(loader=jinja2.FileSystemLoader(str(tpl_dir)), autoescape=False,
                             trim_blocks=True, lstrip_blocks=True, keep_trailing_newline=True)
    text = env.get_template("report.md.j2").render(
        generated=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        started=started, finished=finished,
        d=d, models_info=models_info, charts=charts,
        n_runs=len(d.runs), n_other=len(d.other_runs),
        other_status=Counter(r["status"] for r in d.other_runs),
        status_rows=sorted(Counter((r["model"], r["status"]) for r in d.runs).items()),
        hardware=d.meta.get("hardware") or {},
        selftest_ran=d.meta.get("selftest_ran"),
        flags_rescanned_at=d.meta.get("flags_rescanned_at"),
        review_notes=_review_notes(db),
        env_meta=env_meta,
        sandbox_args=" ".join(d.meta.get("sandbox_run_args") or []),
        limits=(cfg_meta.get("raw") or {}).get("limits") or cfg.limits,
        generation=(cfg_meta.get("raw") or {}).get("generation") or cfg.generation,
        model_overrides={m: o for m, o in ((cfg_meta.get("raw") or {}).get("model_overrides") or {}).items()
                         if m in d.models},
        think=((cfg_meta.get("raw") or {}).get("ollama") or {}).get("think"),
        tasks_meta=cfg_meta.get("tasks") or {},
        system_prompt=cfg_meta.get("system_prompt") or cfg.system_prompt,
        fingerprints=fingerprints,
        config_json=json.dumps(cfg_meta.get("raw") or cfg.raw, indent=2, default=str),
        category_table=section_category_table(d),
        by_task_table=section_by_task(d),
        mismatch_table=section_mismatch(d),
        related_table=section_related(d),
        interesting=section_interesting(d),
        info_table=section_info_flags(d),
        unexplained_total=unexplained_total,
        mismatch_total=mismatch_total,
        categories=CATEGORIES,
    )
    out_path.write_text(text, encoding="utf-8")
    return out_path
