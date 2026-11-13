"""SQLite storage for runs, steps and flags. Commits after every write (crash-safe)."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS runs (
    id               INTEGER PRIMARY KEY,
    model            TEXT NOT NULL,
    task_id          TEXT NOT NULL,
    run_number       INTEGER NOT NULL,
    status           TEXT NOT NULL,          -- running | final | final_unstructured | max_steps | parse_error | sandbox_stopped | error
    seed             INTEGER NOT NULL,
    settings_json    TEXT NOT NULL,          -- generation options, limits, think flag, sandbox args
    config_fingerprint TEXT NOT NULL,
    model_digest     TEXT,
    system_prompt    TEXT NOT NULL,
    user_prompt      TEXT NOT NULL,
    stated_plan      TEXT,                   -- the "PLAN:" sentence from the first reply
    stated_tool_use  TEXT,                   -- yes | no | unclear | unstated
    reasoning_text   TEXT,                   -- all thinking text the model exposed, joined
    final_answer     TEXT,
    n_steps          INTEGER NOT NULL DEFAULT 0,
    n_commands       INTEGER NOT NULL DEFAULT 0,
    started_at       TEXT NOT NULL,
    finished_at      TEXT,
    error            TEXT,
    UNIQUE (model, task_id, run_number)
);

CREATE TABLE IF NOT EXISTS steps (
    id               INTEGER PRIMARY KEY,
    run_id           INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    step_index       INTEGER NOT NULL,
    started_at       TEXT NOT NULL,
    finished_at      TEXT,
    assistant_raw    TEXT,                   -- full reply text as returned by Ollama
    thinking         TEXT,                   -- message.thinking or <think> block, if any
    kind             TEXT NOT NULL,          -- tool | final | parse_error | unsupported_tool | refused
    tool             TEXT,
    command          TEXT,
    exit_code        INTEGER,
    stdout           TEXT,
    stderr           TEXT,
    timed_out        INTEGER NOT NULL DEFAULT 0,
    duration_ms      INTEGER,
    output_truncated INTEGER NOT NULL DEFAULT 0,
    extra_calls_ignored INTEGER NOT NULL DEFAULT 0,
    eval_count       INTEGER,                -- tokens generated (from Ollama)
    done_reason      TEXT,                   -- Ollama done_reason, e.g. "length" when num_predict cut the reply
    UNIQUE (run_id, step_index)
);

CREATE TABLE IF NOT EXISTS flags (
    id               INTEGER PRIMARY KEY,
    run_id           INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    step_id          INTEGER REFERENCES steps(id) ON DELETE CASCADE,
    category         TEXT NOT NULL,
    severity         TEXT NOT NULL,          -- info | low | medium | high
    rule_id          TEXT NOT NULL,
    context          TEXT NOT NULL,          -- invoked | executed_file | mentioned | output | run
    source           TEXT NOT NULL,          -- command | output | plan | reasoning | run
    evidence         TEXT,                   -- the matched text
    command          TEXT,                   -- the full command of the step (copy, for the report)
    task_related     INTEGER NOT NULL,       -- 1 = category listed in the task's task_justifies
    detail           TEXT
);

CREATE INDEX IF NOT EXISTS idx_steps_run ON steps(run_id);
CREATE INDEX IF NOT EXISTS idx_flags_run ON flags(run_id);
CREATE INDEX IF NOT EXISTS idx_flags_cat ON flags(category);
"""

# Statuses that mean "the model finished; do not redo this run on resume".
COMPLETE_STATUSES = ("final", "final_unstructured", "max_steps", "parse_error", "sandbox_stopped")


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


class Store:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.conn = sqlite3.connect(str(path), timeout=30)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=NORMAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    # meta -------------------------------------------------------------------
    def set_meta(self, key: str, value) -> None:
        self.conn.execute("INSERT INTO meta(key, value) VALUES (?, ?) "
                          "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                          (key, json.dumps(value)))
        self.conn.commit()

    def get_meta(self) -> dict:
        return {r["key"]: json.loads(r["value"]) for r in self.conn.execute("SELECT * FROM meta")}

    # runs -------------------------------------------------------------------
    def completed_keys(self) -> set[tuple[str, str, int]]:
        q = f"SELECT model, task_id, run_number FROM runs WHERE status IN ({','.join('?' * len(COMPLETE_STATUSES))})"
        return {(r[0], r[1], r[2]) for r in self.conn.execute(q, COMPLETE_STATUSES)}

    def delete_incomplete(self) -> int:
        """Drop runs that crashed or hit infra errors; they are redone on resume."""
        q = f"DELETE FROM runs WHERE status NOT IN ({','.join('?' * len(COMPLETE_STATUSES))})"
        cur = self.conn.execute(q, COMPLETE_STATUSES)
        self.conn.commit()
        return cur.rowcount

    def start_run(self, *, model: str, task_id: str, run_number: int, seed: int, settings: dict,
                  fingerprint: str, model_digest: str | None, system_prompt: str, user_prompt: str) -> int:
        # A leftover row for the same key (crash, or an error run) is replaced.
        self.conn.execute("DELETE FROM runs WHERE model=? AND task_id=? AND run_number=?",
                          (model, task_id, run_number))
        cur = self.conn.execute(
            "INSERT INTO runs(model, task_id, run_number, status, seed, settings_json, config_fingerprint, "
            "model_digest, system_prompt, user_prompt, started_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (model, task_id, run_number, "running", seed, json.dumps(settings, sort_keys=True),
             fingerprint, model_digest, system_prompt, user_prompt, now()))
        self.conn.commit()
        return cur.lastrowid

    def update_run(self, run_id: int, **fields) -> None:
        if not fields:
            return
        cols = ", ".join(f"{k}=?" for k in fields)
        self.conn.execute(f"UPDATE runs SET {cols} WHERE id=?", (*fields.values(), run_id))
        self.conn.commit()

    def finish_run(self, run_id: int, status: str, **fields) -> None:
        self.update_run(run_id, status=status, finished_at=now(), **fields)

    # steps ------------------------------------------------------------------
    def add_step(self, run_id: int, step_index: int, **fields) -> int:
        fields = {"started_at": now(), **fields}
        cols = ["run_id", "step_index", *fields]
        cur = self.conn.execute(
            f"INSERT INTO steps({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
            (run_id, step_index, *fields.values()))
        self.conn.commit()
        return cur.lastrowid

    def update_step(self, step_id: int, **fields) -> None:
        cols = ", ".join(f"{k}=?" for k in fields)
        self.conn.execute(f"UPDATE steps SET {cols} WHERE id=?", (*fields.values(), step_id))
        self.conn.commit()

    # flags ------------------------------------------------------------------
    def add_flags(self, run_id: int, flags: list[dict]) -> None:
        if not flags:
            return
        self.conn.executemany(
            "INSERT INTO flags(run_id, step_id, category, severity, rule_id, context, source, evidence, "
            "command, task_related, detail) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            [(run_id, f.get("step_id"), f["category"], f["severity"], f["rule_id"], f["context"],
              f["source"], f.get("evidence"), f.get("command"), int(f["task_related"]), f.get("detail"))
             for f in flags])
        self.conn.commit()

    # reading (report) ---------------------------------------------------------
    def query(self, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
        return list(self.conn.execute(sql, params))
