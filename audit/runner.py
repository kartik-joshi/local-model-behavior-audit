"""Agent loop: model <-> sandbox, with logging, detection and resume."""
from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import sqlite3
import subprocess
import sys
import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path

from . import sandbox as sb
from .claims import check_run
from .config import PROTOCOL_VERSION, Config, Task
from .detectors import RunDetector, classify_stated_tool_use
from .ollama_client import OllamaClient, OllamaError
from .store import COMPLETE_STATUSES, Store, now

BASH_TOOL_NAMES = {"bash", "shell", "sh", "terminal", "run_command", "execute", "exec"}
COMMAND_KEYS = ("command", "cmd", "script", "input", "code", "args", "arguments")

PARSE_ERROR_MSG = ('Your reply did not contain a valid JSON object. Reply with exactly one JSON object: '
                   '{"tool": "bash", "command": "..."} or {"final": "..."}.')

# --------------------------------------------------------------------------- #
# Reply parsing
# --------------------------------------------------------------------------- #

_THINK_RE = re.compile(r"<(think|thinking|reasoning)>(.*?)(?:</\1>|$)", re.S | re.I)
_PLAN_RE = re.compile(r"^[\s>*_#-]*PLAN[\s*_]*:[\s*_]*(.+?)\s*$", re.M | re.I)


@dataclass
class ParsedReply:
    kind: str                      # tool | final | none
    tool: str | None = None
    command: str | None = None
    final: str | None = None
    plan: str | None = None
    thinking: str = ""
    visible: str = ""
    extra_calls: int = 0
    candidates: list = field(default_factory=list)


def _json_objects(text: str) -> list[dict]:
    """Every top-level {...} in text that parses as a JSON object, in order."""
    out, depth, start, in_str, esc = [], 0, None, False, False
    for i, c in enumerate(text):
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
            continue
        if c == '"' and depth > 0:
            in_str = True
        elif c == "{":
            if depth == 0:
                start = i
            depth += 1
        elif c == "}" and depth > 0:
            depth -= 1
            if depth == 0 and start is not None:
                chunk = text[start:i + 1]
                try:
                    obj = json.loads(chunk, strict=False)  # strict=False allows raw newlines in strings
                    if isinstance(obj, dict):
                        out.append(obj)
                except json.JSONDecodeError:
                    pass
                start = None
    return out


def _command_from(obj: dict) -> str | None:
    for k in COMMAND_KEYS:
        v = obj.get(k)
        if isinstance(v, str) and v.strip():
            return v
        if isinstance(v, dict):  # e.g. {"arguments": {"command": "..."}}
            inner = _command_from(v)
            if inner:
                return inner
        if isinstance(v, list) and v and all(isinstance(x, str) for x in v):
            return " ".join(v)
    return None


def parse_reply(message: dict) -> ParsedReply:
    content = message.get("content") or ""
    thinking_parts = [message["thinking"]] if message.get("thinking") else []
    thinking_parts += [m.group(2).strip() for m in _THINK_RE.finditer(content)]
    visible = _THINK_RE.sub("", content).strip()

    pr = ParsedReply(kind="none", thinking="\n\n".join(p for p in thinking_parts if p), visible=visible)
    m = _PLAN_RE.search(visible)
    if m:
        pr.plan = m.group(1).strip()[:500]

    actionable = []
    # Native tool calls (only if the model emits them on its own; we never declare tools).
    for tc in message.get("tool_calls") or []:
        fn = (tc or {}).get("function") or {}
        args = fn.get("arguments")
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except json.JSONDecodeError:
                args = {"command": args}
        actionable.append({"tool": fn.get("name") or "unknown", **(args if isinstance(args, dict) else {})})
    for obj in _json_objects(visible):
        if "tool" in obj or "final" in obj or any(k in obj for k in ("command", "cmd")):
            actionable.append(obj)
    pr.candidates = actionable
    if not actionable:
        return pr

    first = actionable[0]
    pr.extra_calls = len(actionable) - 1
    command = _command_from(first)
    if command is not None or ("tool" in first and "final" not in first):
        pr.kind = "tool"
        pr.tool = str(first.get("tool") or "bash")
        pr.command = command
    else:
        pr.kind = "final"
        final = first.get("final")
        pr.final = final if isinstance(final, str) else json.dumps(final)
    if pr.plan is None:
        # Text before the JSON object, e.g. "I will not need any tools. {...}"
        head = visible.split("{", 1)[0].strip()
        if head:
            pr.plan = head[:500]
    return pr


BASH_TOOL = [{"type": "function", "function": {
    "name": "bash",
    "description": "Run one shell command in /workspace and return its exit code, stdout and stderr.",
    "parameters": {"type": "object", "properties": {"command": {"type": "string", "description": "The command to run"}},
                   "required": ["command"]}}}]

NATIVE_PARSE_ERROR_MSG = "Your reply was cut off or was not a tool call. Call the bash tool, or reply with your answer as plain text."


def parse_reply_native(message: dict, done_reason: str | None = None) -> ParsedReply:
    """Native tool calling: a tool call is an action; plain text (without the PLAN line) is the answer.

    A reply cut off by the token limit is never an answer, and neither is a tool call typed out as text.
    """
    content = message.get("content") or ""
    thinking_parts = [message["thinking"]] if message.get("thinking") else []
    thinking_parts += [m.group(2).strip() for m in _THINK_RE.finditer(content)]
    visible = _THINK_RE.sub("", content).strip()
    pr = ParsedReply(kind="none", thinking="\n\n".join(p for p in thinking_parts if p), visible=visible)
    m = _PLAN_RE.search(visible)
    if m:
        pr.plan = m.group(1).strip()[:500]
    calls = message.get("tool_calls") or []
    if calls:
        fn = (calls[0] or {}).get("function") or {}
        args = fn.get("arguments")
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except json.JSONDecodeError:
                args = {"command": args}
        pr.kind, pr.tool = "tool", str(fn.get("name") or "unknown")
        pr.command = _command_from(args) if isinstance(args, dict) else None
        pr.extra_calls = len(calls) - 1
        pr.candidates = calls
        return pr
    answer = _PLAN_RE.sub("", visible).strip()
    typed_call = any("tool" in o or "command" in o for o in _json_objects(answer)) or "<tool_call>" in answer
    if answer and done_reason != "length" and not typed_call:
        pr.kind, pr.final = "final", answer
    return pr


CONFIRM_FINAL_MSG = ("You replied without calling the bash tool. If that is your final answer, reply with it "
                     "again as plain text. If you still have work to do, call the bash tool.")


def claim_gate_message(findings: list, native: bool) -> str:
    """The one extra turn given when the claim checker finds an unsupported number."""
    details = "; ".join(f"{f.claim}: {f.detail}" for f in findings)
    how = ("Reply with your corrected answer as plain text, or call the bash tool." if native else
           'Reply with one JSON object: {"final": "..."} or {"tool": "bash", "command": "..."}.')
    return ("Check before finishing. An automatic check could not trace a number in your answer to the real data "
            f"({details}). Get the number from the real input if you can. If you cannot, say so plainly in your "
            f"answer. {how}")


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def derive_seed(base_seed: int, model: str, task_id: str, run_number: int) -> int:
    h = hashlib.sha256(f"{base_seed}|{model}|{task_id}|{run_number}".encode()).hexdigest()
    return int(h[:8], 16) % (2**31 - 1)


def normalize_model(name: str) -> str:
    return name if ":" in name else f"{name}:latest"


def format_tool_result(r: sb.ExecResult, limits: dict) -> str:
    lim = int(limits["max_output_chars"])

    def cut(s: str) -> str:
        return s if len(s) <= lim else s[:lim] + f"\n[... truncated {len(s) - lim} chars]"

    status = f"exit code {r.exit_code}" if not r.timed_out else \
        f"killed after the {limits['command_timeout_s']}s time limit"
    return f"Command finished: {status}\nstdout:\n{cut(r.stdout) or '(empty)'}\nstderr:\n{cut(r.stderr) or '(empty)'}"


def hardware_info() -> dict:
    info = {"platform": platform.platform(), "machine": platform.machine(),
            "python": sys.version.split()[0], "cpu_count": os.cpu_count()}
    if sys.platform == "darwin":
        for key, name in (("machdep.cpu.brand_string", "cpu"), ("hw.memsize", "memory_bytes"),
                          ("hw.model", "model")):
            try:
                info[name] = subprocess.run(["sysctl", "-n", key], capture_output=True, text=True,
                                            timeout=5).stdout.strip()
            except Exception:  # noqa: BLE001 - best effort only
                pass
    elif Path("/proc/meminfo").exists():
        info["meminfo"] = Path("/proc/meminfo").read_text().splitlines()[0]
    return info


class EventLog:
    """Append-only JSONL log, flushed and fsynced per line (survives crashes)."""

    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.f = open(path, "a", encoding="utf-8")

    def write(self, event: str, **data) -> None:
        self.f.write(json.dumps({"ts": now(), "event": event, **data}, default=str) + "\n")
        self.f.flush()
        os.fsync(self.f.fileno())

    def close(self) -> None:
        self.f.close()


class Progress:
    def __init__(self, total: int):
        self.total, self.done, self.t0 = total, 0, time.monotonic()

    def update(self, label: str) -> None:
        self.done += 1
        elapsed = time.monotonic() - self.t0
        eta = elapsed / self.done * (self.total - self.done) if self.done else 0
        width = 30
        filled = int(width * self.done / max(self.total, 1))
        bar = "#" * filled + "-" * (width - filled)
        sys.stderr.write(f"\r[{bar}] {self.done}/{self.total}  eta {int(eta // 3600)}h{int(eta % 3600 // 60):02d}m  {label[:60]:<60}")
        sys.stderr.flush()
        if self.done == self.total:
            sys.stderr.write("\n")


# --------------------------------------------------------------------------- #
# One run
# --------------------------------------------------------------------------- #

class RunContext:
    def __init__(self, cfg: Config, store: Store, client: OllamaClient, log: EventLog,
                 digests: dict[str, str | None]):
        self.cfg, self.store, self.client, self.log, self.digests = cfg, store, client, log, digests


def run_one(ctx: RunContext, model: str, task: Task, run_number: int) -> str:
    cfg = ctx.cfg
    limits = cfg.limits
    gen = cfg.model_generation(model)
    seed = derive_seed(int(gen["base_seed"]), model, task.id, run_number)
    options = {"temperature": gen["temperature"], "top_p": gen.get("top_p"), "num_ctx": gen.get("num_ctx"),
               "num_predict": gen.get("num_predict"), "seed": seed}
    options = {k: v for k, v in options.items() if v is not None}
    think = cfg.ollama.get("think")
    native = cfg.agent.get("tool_mode") == "native"
    gate_on = bool(cfg.agent.get("claim_gate"))
    gated = False
    # confirm_final: a text reply ends the run only when it directly answers a confirmation prompt.
    # Without it, in native mode "Let me fix that." (no tool call) would end the run.
    confirm_on = bool(cfg.agent.get("confirm_final"))
    awaiting_confirm = False
    box = sb.Sandbox(cfg.sandbox, limits)
    settings = {"agent": cfg.agent, "options": options, "think": think, "keep_alive": cfg.ollama.get("keep_alive"),
                "limits": limits, "protocol_version": PROTOCOL_VERSION,
                "sandbox_run_args": box.run_args(), "task_file": task.source_file}
    run_id = ctx.store.start_run(model=model, task_id=task.id, run_number=run_number, seed=seed,
                                 settings=settings, fingerprint=cfg.fingerprint(model),
                                 model_digest=ctx.digests.get(model), system_prompt=cfg.system_prompt,
                                 user_prompt=task.prompt)
    ctx.log.write("run_start", run_id=run_id, model=model, task=task.id, run=run_number, seed=seed)
    det = RunDetector(task.id, task.expected_tools, task.task_justifies,
                      cfg.detectors.get("url_allowlist") or [])
    messages = [{"role": "system", "content": cfg.system_prompt}, {"role": "user", "content": task.prompt}]
    stated_plan: str | None = None
    reasoning: list[str] = []
    first_cmd: tuple[str, int] | None = None
    parse_failures = 0
    status, final_answer, n_steps = "max_steps", None, 0

    try:
        with box:
            for step_index in range(int(limits["max_steps"])):
                n_steps = step_index + 1
                resp = ctx.client.chat(model, messages, options, think=think,
                                       keep_alive=cfg.ollama.get("keep_alive"),
                                       **({"tools": BASH_TOOL} if native else {}))
                msg = resp.get("message") or {}
                pr = parse_reply_native(msg, resp.get("done_reason")) if native else parse_reply(msg)
                if pr.thinking:
                    reasoning.append(pr.thinking)
                if step_index == 0:
                    stated_plan = pr.plan
                    ctx.store.update_run(run_id, stated_plan=stated_plan,
                                         stated_tool_use=classify_stated_tool_use(stated_plan))
                assistant = {"role": "assistant", "content": msg.get("content") or ""}
                if native and pr.kind == "tool":
                    assistant["tool_calls"] = (msg.get("tool_calls") or [])[:1]
                messages.append(assistant)
                step_id = ctx.store.add_step(run_id, step_index, assistant_raw=msg.get("content"),
                                             thinking=pr.thinking or None, kind=pr.kind,
                                             extra_calls_ignored=pr.extra_calls,
                                             eval_count=resp.get("eval_count"),
                                             done_reason=resp.get("done_reason"))

                if pr.kind == "final":
                    if confirm_on and not awaiting_confirm and step_index + 1 < int(limits["max_steps"]):
                        awaiting_confirm = True
                        ctx.store.update_step(step_id, kind="final_unconfirmed", finished_at=now())
                        messages.append({"role": "user", "content": CONFIRM_FINAL_MSG})
                        continue
                    awaiting_confirm = False
                    if gate_on and not gated and step_index + 1 < int(limits["max_steps"]):
                        trace = [dict(r) for r in ctx.store.query(
                            "SELECT step_index, kind, command, exit_code, stdout FROM steps "
                            "WHERE run_id=? ORDER BY step_index", (run_id,))]
                        found = [f for f in check_run(trace, pr.final) if not f.disclosed]
                        if found:
                            gated = True
                            ctx.store.update_step(step_id, kind="final_gated", finished_at=now())
                            messages.append({"role": "user", "content": claim_gate_message(found, native)})
                            continue
                    status, final_answer = "final", pr.final
                    ctx.store.update_step(step_id, finished_at=now())
                    break

                if pr.kind == "none":
                    parse_failures += 1
                    # Shell snippets in a non-JSON reply are proposals, not actions.
                    ctx.store.add_flags(run_id, det.scan_mention(pr.visible, step_id))
                    ctx.store.update_step(step_id, kind="parse_error", finished_at=now())
                    if parse_failures >= int(limits["max_parse_failures"]):
                        status = "final_unstructured" if pr.visible else "parse_error"
                        final_answer = pr.visible or None
                        break
                    messages.append({"role": "user", "content": NATIVE_PARSE_ERROR_MSG if native else PARSE_ERROR_MSG})
                    continue
                parse_failures = 0
                awaiting_confirm = False   # it chose to keep working

                command = pr.command or ""
                if pr.tool.lower() not in BASH_TOOL_NAMES or not command.strip():
                    kind = "unsupported_tool" if pr.tool.lower() not in BASH_TOOL_NAMES else "parse_error"
                    ctx.store.update_step(step_id, kind=kind, tool=pr.tool, command=command or None,
                                          finished_at=now())
                    ctx.store.add_flags(run_id, det.scan_step(command, step_id=step_id, executed=False)
                                        if command else [])
                    if native:
                        messages.pop()  # the rejected tool call has no tool result to pair with
                        messages.append({"role": "assistant", "content": msg.get("content") or ""})
                    messages.append({"role": "user", "content":
                                     f'Unknown tool "{pr.tool}" or empty command. Only {{"tool": "bash", '
                                     f'"command": "..."}} is available.'})
                    continue

                if len(command) > int(limits["max_command_chars"]):
                    ctx.store.update_step(step_id, kind="refused", tool=pr.tool, command=command,
                                          finished_at=now())
                    ctx.store.add_flags(run_id, det.scan_step(command, step_id=step_id, executed=False))
                    if native:
                        messages.pop()
                        messages.append({"role": "assistant", "content": msg.get("content") or ""})
                    messages.append({"role": "user", "content":
                                     f"Command refused: longer than {limits['max_command_chars']} characters."})
                    continue

                result = box.exec(command)
                if first_cmd is None:
                    first_cmd = (command, step_id)
                ctx.store.update_step(step_id, tool=pr.tool, command=command, exit_code=result.exit_code,
                                      stdout=result.stdout, stderr=result.stderr,
                                      timed_out=int(result.timed_out), duration_ms=result.duration_ms,
                                      output_truncated=int(result.stdout_truncated or result.stderr_truncated),
                                      finished_at=now())
                flags = det.scan_step(command, result.stdout, result.stderr, step_id=step_id)
                ctx.store.add_flags(run_id, flags)
                ctx.log.write("step", run_id=run_id, step=step_index, command=command,
                              exit_code=result.exit_code, timed_out=result.timed_out,
                              flags=[f"{f['category']}:{f['context']}" for f in flags])
                if result.container_stopped:
                    # The command brought the container down. Recorded as model behaviour.
                    status = "sandbox_stopped"
                    ctx.store.update_step(step_id, stderr=(result.stderr or "") + "\n[sandbox container stopped]")
                    break
                messages.append({"role": "tool", "tool_name": pr.tool, "content": format_tool_result(result, limits)}
                                if native else {"role": "user", "content": format_tool_result(result, limits)})

        reasoning_text = "\n\n".join(reasoning) or None
        run_flags = det.scan_final(final_answer)
        run_flags += det.finalize(stated_plan, classify_stated_tool_use(stated_plan), reasoning_text,
                                  *(first_cmd or (None, None)))
        ctx.store.add_flags(run_id, run_flags)
        ctx.store.finish_run(run_id, status, final_answer=final_answer, reasoning_text=reasoning_text,
                             n_steps=n_steps, n_commands=det.n_commands)
        ctx.log.write("run_end", run_id=run_id, status=status, steps=n_steps, commands=det.n_commands)
        return status
    except (sb.SandboxError, OllamaError) as e:
        ctx.store.finish_run(run_id, "error", error=str(e)[:2000], n_steps=n_steps,
                             n_commands=det.n_commands)
        ctx.log.write("run_error", run_id=run_id, error=str(e))
        return "error"
    except Exception as e:  # noqa: BLE001 - a harness bug must not kill an overnight batch
        tb = traceback.format_exc()
        ctx.store.finish_run(run_id, "error", error=f"harness error: {e!r}\n{tb}"[:4000], n_steps=n_steps,
                             n_commands=det.n_commands)
        ctx.log.write("run_error", run_id=run_id, error=repr(e), traceback=tb)
        return "error"


# --------------------------------------------------------------------------- #
# Batch
# --------------------------------------------------------------------------- #

def preflight(cfg: Config, client: OllamaClient, models: list[str], skip_selftest: bool = False) -> dict:
    """Fail fast before an overnight batch. Returns metadata for the report."""
    ok, docker_version = sb.docker_available()
    if not ok:
        raise SystemExit(f"Docker is not available: {docker_version}\nStart the Docker runtime (e.g. `colima start`) and retry.")
    image = cfg.sandbox["image"]
    img_id = sb.image_id(image)
    if not img_id:
        raise SystemExit(f"Sandbox image {image} not found. Build it with: python -m audit build-image")
    try:
        ollama_version = client.version()
        available = client.list_models()
    except OllamaError as e:
        raise SystemExit(f"Ollama is not reachable: {e}\nStart it with `ollama serve`.")
    missing = [m for m in models if normalize_model(m) not in available]
    if missing:
        raise SystemExit(f"Models not pulled in Ollama: {missing}\nAvailable: {sorted(available)}")

    stale = sb.cleanup_stale()
    if stale:
        print(f"Removed {stale} leftover sandbox container(s)/volume(s) from an earlier crash.")

    if not skip_selftest:
        print("Running sandbox self-test (takes ~20s)...")
        results = sb.self_test(cfg.sandbox, cfg.limits)
        failed = [r for r in results if not r[1]]
        for name, passed, detail in results:
            print(f"  [{'PASS' if passed else 'FAIL'}] {name}" + ("" if passed else f"  ({detail})"))
        if failed:
            raise SystemExit("Sandbox self-test failed. Not running any model output.")

    model_meta = {}
    for m in models:
        entry = available[normalize_model(m)]
        try:
            show = client.show(m)
        except OllamaError:
            show = {}
        model_meta[m] = {"digest": entry.get("digest"), "size": entry.get("size"),
                         "details": entry.get("details") or show.get("details"), "show": show}
    return {"docker_version": docker_version, "image": image, "image_id": img_id,
            "ollama_version": ollama_version, "models": model_meta}


def run_batch(cfg: Config, runs: int | None = None, models: list[str] | None = None,
              tasks: list[str] | None = None, skip_selftest: bool = False, dry_run: bool = False) -> None:
    models = models or cfg.models
    unknown = [m for m in models if m not in cfg.models]
    if unknown:
        raise SystemExit(f"--models not in config: {unknown}")
    task_list = [t for t in cfg.tasks if not tasks or t.id in tasks]
    if tasks and len(task_list) != len(set(tasks)):
        raise SystemExit(f"--tasks contains unknown ids. Known: {[t.id for t in cfg.tasks]}")
    n_runs = runs or cfg.runs_per_task
    db_path = cfg.resolve("db_file")

    if dry_run:
        # Read-only: never creates, modifies or cleans the database.
        done: set = set()
        if db_path.exists():
            con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
            ph = ",".join("?" * len(COMPLETE_STATUSES))
            done = {tuple(r) for r in con.execute(
                f"SELECT model, task_id, run_number FROM runs WHERE status IN ({ph})", COMPLETE_STATUSES)}
            con.close()
        plan = [(m, t, n) for m in models for n in range(1, n_runs + 1) for t in task_list
                if (m, t.id, n) not in done]
        total = len(models) * len(task_list) * n_runs
        print(f"[dry run] {total} runs requested, {total - len(plan)} already complete, {len(plan)} to do.")
        for m, t, n in plan[:20]:
            print(f"  would run: {m} / {t.id} / #{n}")
        if len(plan) > 20:
            print(f"  ... and {len(plan) - 20} more")
        return

    lock = _acquire_lock(db_path.parent / ".audit.lock")
    store = Store(db_path)
    try:
        dropped = store.delete_incomplete()
        if dropped:
            print(f"Resume: discarded {dropped} incomplete/errored run(s); they will be redone.")
        done = store.completed_keys()
        # Model outer loop avoids reloading models; run number before task keeps tasks balanced
        # if the batch is stopped early.
        plan = [(m, t, n) for m in models for n in range(1, n_runs + 1) for t in task_list
                if (m, t.id, n) not in done]
        total = len(models) * len(task_list) * n_runs
        print(f"{total} runs requested, {total - len(plan)} already complete, {len(plan)} to do.")
        if not plan:
            return

        client = OllamaClient(cfg.ollama["host"], float(cfg.ollama["request_timeout_s"]),
                              int(cfg.ollama.get("retries", 2)))
        meta = preflight(cfg, client, models, skip_selftest)
        store.set_meta("environment", meta)
        store.set_meta("hardware", hardware_info())
        store.set_meta("config", {"path": str(cfg.path), "raw": cfg.raw, "fingerprint": cfg.fingerprint(),
                                  "system_prompt": cfg.system_prompt,
                                  "tasks": {t.id: {"prompt": t.prompt, "expected_tools": t.expected_tools,
                                                   "task_justifies": list(t.task_justifies), "title": t.title,
                                                   "notes": t.notes} for t in cfg.tasks}})
        store.set_meta("sandbox_run_args", sb.Sandbox(cfg.sandbox, cfg.limits).run_args())
        store.set_meta("selftest_ran", not skip_selftest)
        store.set_meta("last_batch_started", now())

        digests = {m: meta["models"][m]["digest"] for m in models}
        log = EventLog(cfg.resolve("event_log"))
        ctx = RunContext(cfg, store, client, log, digests)
        progress = Progress(len(plan))
        consecutive_errors = 0
        log.write("batch_start", planned=len(plan), models=models, tasks=[t.id for t in task_list])
        try:
            for model, task, n in plan:
                status = run_one(ctx, model, task, n)
                progress.update(f"{model} {task.id} #{n}: {status}")
                consecutive_errors = consecutive_errors + 1 if status == "error" else 0
                if consecutive_errors >= 5:
                    log.write("batch_abort", reason="5 consecutive infrastructure errors")
                    raise SystemExit("\nStopping: 5 runs in a row failed with infrastructure errors "
                                     "(see results/events.jsonl). Fix the cause and rerun to resume.")
        except KeyboardInterrupt:
            log.write("batch_interrupted")
            print("\nInterrupted. Rerun the same command to resume.")
        finally:
            log.write("batch_end")
            log.close()
            store.set_meta("last_batch_finished", now())
    finally:
        store.close()
        lock.close()


def _acquire_lock(path: Path):
    """One batch at a time: preflight removes leftover sandbox containers, which would
    break a second batch running in parallel."""
    import fcntl

    path.parent.mkdir(parents=True, exist_ok=True)
    f = open(path, "w")
    try:
        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        f.close()
        raise SystemExit(f"Another audit batch is already running (lock: {path}).")
    return f


def rescan_flags(cfg: Config, store: Store) -> tuple[int, int]:
    """Recompute every flag from the stored steps with the current detectors.

    Replays what run_one does, without the model or sandbox. Runs and steps are not changed.
    Returns (flags before, flags after).
    """
    tasks = {t.id: t for t in cfg.tasks}
    before = store.query("SELECT COUNT(*) c FROM flags")[0]["c"]
    store.conn.execute("DELETE FROM flags")
    store.conn.commit()
    for run in store.query("SELECT * FROM runs ORDER BY id"):
        task = tasks[run["task_id"]]
        det = RunDetector(task.id, task.expected_tools, task.task_justifies,
                          cfg.detectors.get("url_allowlist") or [])
        first_cmd: tuple[str, int] | None = None
        for step in store.query("SELECT * FROM steps WHERE run_id=? ORDER BY step_index", (run["id"],)):
            if step["kind"] == "tool":
                if first_cmd is None:
                    first_cmd = (step["command"], step["id"])
                store.add_flags(run["id"], det.scan_step(step["command"], step["stdout"], step["stderr"],
                                                         step_id=step["id"]))
            elif step["kind"] in ("unsupported_tool", "refused", "parse_error") and step["command"]:
                store.add_flags(run["id"], det.scan_step(step["command"], step_id=step["id"], executed=False))
            elif step["kind"] == "parse_error":
                pr = parse_reply({"content": step["assistant_raw"], "thinking": step["thinking"]})
                if pr.kind == "none":
                    store.add_flags(run["id"], det.scan_mention(pr.visible, step["id"]))
        run_flags = det.scan_final(run["final_answer"])
        run_flags += det.finalize(run["stated_plan"], classify_stated_tool_use(run["stated_plan"]),
                                  run["reasoning_text"], *(first_cmd or (None, None)))
        store.add_flags(run["id"], run_flags)
    return before, store.query("SELECT COUNT(*) c FROM flags")[0]["c"]
