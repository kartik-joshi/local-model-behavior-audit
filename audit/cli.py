"""Command line interface.

    python -m audit run --config config.yaml --runs 200
    python -m audit report
    python -m audit check-claims
    python -m audit stats --variant-config audit/config_optional_tools.yaml --variant-config audit/config_qwen_nothink.yaml
"""
from __future__ import annotations

import argparse
import sys


def _csv(value: str | None) -> list[str] | None:
    return [v.strip() for v in value.split(",") if v.strip()] if value else None


def cmd_run(args) -> None:
    from .config import load_config
    from .runner import run_batch

    cfg = load_config(args.config)
    run_batch(cfg, runs=args.runs, models=_csv(args.models), tasks=_csv(args.tasks),
              skip_selftest=args.skip_selftest, dry_run=args.dry_run)


def cmd_report(args) -> None:
    from .config import load_config
    from .report import build_report

    cfg = load_config(args.config)
    path = build_report(cfg, out=args.out)
    print(f"Report written to {path}")


def cmd_rescan(args) -> None:
    from .config import load_config
    from .runner import rescan_flags
    from .store import Store, now

    cfg = load_config(args.config)
    store = Store(cfg.resolve("db_file"))
    try:
        before, after = rescan_flags(cfg, store)
        store.set_meta("flags_rescanned_at", now())
    finally:
        store.close()
    print(f"Flags recomputed from stored steps: {before} -> {after}. Runs and steps unchanged.")


def cmd_check_claims(args) -> None:
    from .claims import check_db
    from .config import load_config
    from .store import Store

    cfg = load_config(args.config)
    store = Store(cfg.resolve("db_file"))
    try:
        found = check_db(store)
        runs = {r["id"]: r for r in store.query(
            "SELECT id, model, task_id, run_number, final_answer FROM runs WHERE status != 'error'")}
    finally:
        store.close()
    counts: dict = {}
    rows = []
    for rid, fs in found.items():
        r = runs[rid]
        key = (r["model"], r["task_id"])
        c = counts.setdefault(key, [0, 0, 0])
        c[0] += 1
        if fs and any(not f.disclosed for f in fs):
            c[1] += 1
        elif fs:
            c[2] += 1
        for f in fs:
            rows.append(f"| {r['model']} | {r['task_id']} | {r['run_number']} | {f.claim} | {f.kind} | "
                        f"{'yes' if f.disclosed else 'no'} | {f.detail} |")
    out = cfg.resolve("results_dir") / "claims.md"
    lines = ["# Claim check", "",
             "Numbers stated in final answers, traced back to the command output that printed them",
             "(`audit/claims.py`). Flagged: the number came from data the agent wrote itself, an input file it",
             "wrote over, a failed download, or no tool output at all.", "",
             "| Model | Task | Runs | Flagged, not disclosed | Flagged, disclosed |", "|---|---|---|---|---|"]
    lines += [f"| {m} | {t} | {c[0]} | {c[1]} | {c[2]} |" for (m, t), c in sorted(counts.items())]
    lines += ["", "## Every finding", "", "| Model | Task | Run | Number | Kind | Disclosed | Detail |",
              "|---|---|---|---|---|---|---|"] + sorted(rows)
    out.write_text("\n".join(lines) + "\n")
    print(f"Claim check written to {out}")


def cmd_stats(args) -> None:
    from .config import load_config
    from .stats import build_stats

    cfg = load_config(args.config)
    variants = []
    for vc in args.variant_config or []:
        vcfg = load_config(vc)
        variants.append((vcfg.resolve("results_dir").name, vcfg.resolve("db_file")))
    out = build_stats(cfg.resolve("db_file"), variants, cfg.resolve("results_dir") / "stats.md")
    print(f"Statistics written to {out}")


def cmd_selftest(args) -> None:
    from . import sandbox as sb
    from .config import load_config

    cfg = load_config(args.config)
    ok, info = sb.docker_available()
    if not ok:
        sys.exit(f"Docker is not available: {info}")
    if not sb.image_id(cfg.sandbox["image"]):
        sys.exit(f"Image {cfg.sandbox['image']} not found. Run: python -m audit build-image")
    results = sb.self_test(cfg.sandbox, cfg.limits)
    for name, passed, detail in results:
        print(f"[{'PASS' if passed else 'FAIL'}] {name}  {detail}")
    if not all(r[1] for r in results):
        sys.exit("Sandbox self-test FAILED. Do not run models until this passes.")
    print("All sandbox checks passed.")


def cmd_build_image(args) -> None:
    from . import sandbox as sb
    from .config import load_config

    cfg = load_config(args.config)
    sb.build_image(cfg.sandbox["image"], cfg.base_dir / cfg.sandbox["dockerfile_dir"])


def cmd_models(args) -> None:
    from .config import load_config
    from .ollama_client import OllamaClient, OllamaError
    from .runner import normalize_model

    cfg = load_config(args.config)
    client = OllamaClient(cfg.ollama["host"], timeout=15)
    try:
        available = client.list_models()
    except OllamaError as e:
        sys.exit(str(e))
    for name, m in sorted(available.items()):
        d = m.get("details") or {}
        mark = "*" if name in {normalize_model(x) for x in cfg.models} else " "
        print(f"{mark} {name:<32} {d.get('parameter_size', ''):>8} {d.get('quantization_level', ''):>8}  {m.get('digest', '')[:12]}")
    print("\n* = listed in config")


def cmd_status(args) -> None:
    from .config import load_config
    from .store import Store

    cfg = load_config(args.config)
    path = cfg.resolve("db_file")
    if not path.exists():
        sys.exit(f"No database yet at {path}")
    store = Store(path)
    rows = store.query("SELECT model, task_id, status, COUNT(*) n FROM runs GROUP BY 1,2,3 ORDER BY 1,2,3")
    for r in rows:
        print(f"{r['model']:<24} {r['task_id']:<24} {r['status']:<20} {r['n']}")
    store.close()


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="python -m audit", description="Local Model Behavior Audit")
    p.add_argument("--config", default=None, help="path to config.yaml (default: audit/config.yaml)")
    sub = p.add_subparsers(dest="command", required=True)

    r = sub.add_parser("run", help="run the audit (resumes automatically)")
    r.add_argument("--config", default=argparse.SUPPRESS)
    r.add_argument("--runs", type=int, default=None, help="runs per (model, task); overrides config")
    r.add_argument("--models", default=None, help="comma-separated subset of configured models")
    r.add_argument("--tasks", default=None, help="comma-separated subset of task ids")
    r.add_argument("--skip-selftest", action="store_true", help="skip the sandbox isolation self-test")
    r.add_argument("--dry-run", action="store_true", help="show what would run; touches nothing")
    r.set_defaults(func=cmd_run)

    rep = sub.add_parser("report", help="build results/report.md and charts")
    rep.add_argument("--config", default=argparse.SUPPRESS)
    rep.add_argument("--out", default=None, help="output path (default: results/report.md)")
    rep.set_defaults(func=cmd_report)

    st = sub.add_parser("stats", help="headline rates with confidence intervals -> results/stats.md")
    st.add_argument("--config", default=argparse.SUPPRESS)
    st.add_argument("--variant-config", action="append", default=None,
                    help="config of a variant to compare against (repeatable)")
    st.set_defaults(func=cmd_stats)

    for name, func, text in (("check-claims", cmd_check_claims, "trace numbers in final answers to their source"),
                             ("rescan", cmd_rescan, "recompute flags from stored steps (no model or Docker)"),
                             ("selftest", cmd_selftest, "check the sandbox blocks network etc."),
                             ("build-image", cmd_build_image, "docker build the sandbox image"),
                             ("models", cmd_models, "list models pulled in Ollama"),
                             ("status", cmd_status, "run counts by model, task and status")):
        sp = sub.add_parser(name, help=text)
        sp.add_argument("--config", default=argparse.SUPPRESS)
        sp.set_defaults(func=func)

    args = p.parse_args(argv)
    if args.command == "run" and args.runs is not None and args.runs < 1:
        p.error("--runs must be >= 1")
    args.func(args)
