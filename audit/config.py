"""Load config.yaml and task files into plain dataclasses."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent
DEFAULT_CONFIG = PACKAGE_DIR / "config.yaml"

# Bump when the agent protocol (system prompt wording, message format) changes,
# so runs made under different protocols are never mixed silently.
PROTOCOL_VERSION = "1"


@dataclass(frozen=True)
class Task:
    id: str
    title: str
    prompt: str
    expected_tools: bool
    task_justifies: tuple[str, ...]
    notes: str
    source_file: str


@dataclass
class Config:
    path: Path
    raw: dict
    models: list[str]
    tasks: list[Task]
    system_prompt_template: str
    runs_per_task: int
    base_dir: Path = field(repr=False)

    # Convenience accessors -------------------------------------------------
    @property
    def ollama(self) -> dict:
        return self.raw["ollama"]

    @property
    def generation(self) -> dict:
        return self.raw["generation"]

    @property
    def limits(self) -> dict:
        return self.raw["limits"]

    @property
    def sandbox(self) -> dict:
        return self.raw["sandbox"]

    @property
    def agent(self) -> dict:
        """Optional experiment switches: tool_mode (json | native), claim_gate (bool)."""
        return self.raw.get("agent") or {}

    @property
    def detectors(self) -> dict:
        return self.raw.get("detectors") or {}

    def resolve(self, key: str) -> Path:
        """Resolve a path value from the config relative to the config file."""
        p = Path(self.raw[key])
        return p if p.is_absolute() else (self.base_dir / p)

    @property
    def system_prompt(self) -> str:
        # str.replace, not str.format: the prompt contains literal JSON braces.
        return self.system_prompt_template.replace("{max_steps}", str(self.limits["max_steps"]))

    def model_generation(self, model: str | None = None) -> dict:
        """Generation settings for one model: `generation` plus any `model_overrides[model]`."""
        overrides = (self.raw.get("model_overrides") or {}).get(model) or {} if model else {}
        return {**self.generation, **overrides}

    def fingerprint(self, model: str | None = None) -> str:
        """Short hash of everything that changes model behaviour, stored with each run.

        A model without overrides gets the same fingerprint as before overrides existed."""
        agent = {"agent": self.agent, "image": self.sandbox.get("image")} if self.agent else {}
        blob = json.dumps(
            {
                **agent,
                "protocol": PROTOCOL_VERSION,
                "system_prompt": self.system_prompt,
                "generation": {k: v for k, v in self.model_generation(model).items() if k != "base_seed"},
                "limits": self.limits,
                "think": self.ollama.get("think"),
                "tasks": {t.id: t.prompt for t in self.tasks},
            },
            sort_keys=True,
        )
        return hashlib.sha256(blob.encode()).hexdigest()[:16]


REQUIRED_TOP = ["models", "ollama", "generation", "limits", "runs_per_task", "tasks_dir",
                "system_prompt_file", "results_dir", "db_file", "event_log", "sandbox"]
REQUIRED_TASK = ["id", "prompt", "expected_tools"]


def _load_yaml(path: Path):
    try:
        import yaml
    except ImportError as e:  # pragma: no cover - environment dependent
        raise SystemExit("PyYAML is required: pip install -r requirements.txt") from e
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def find_config(path: str | None) -> Path:
    """Accept an explicit path, a path relative to the package, or the default."""
    if not path:
        return DEFAULT_CONFIG
    p = Path(path)
    if p.exists():
        return p.resolve()
    alt = PACKAGE_DIR / path
    if alt.exists():
        return alt.resolve()
    raise SystemExit(f"Config file not found: {path}")


def load_task(path: Path) -> Task:
    from .detectors import CATEGORIES  # local import avoids a cycle at module load

    data = _load_yaml(path) or {}
    missing = [k for k in REQUIRED_TASK if k not in data]
    if missing:
        raise SystemExit(f"{path.name}: missing keys {missing}")
    justifies = tuple(data.get("task_justifies") or ())
    unknown = [c for c in justifies if c not in CATEGORIES]
    if unknown:
        raise SystemExit(f"{path.name}: unknown task_justifies categories {unknown}")
    if not isinstance(data["expected_tools"], bool):
        raise SystemExit(f"{path.name}: expected_tools must be true or false")
    return Task(
        id=str(data["id"]),
        title=str(data.get("title", data["id"])),
        prompt=str(data["prompt"]).strip(),
        expected_tools=data["expected_tools"],
        task_justifies=justifies,
        notes=str(data.get("notes", "")).strip(),
        source_file=path.name,
    )


def load_config(path: str | None = None) -> Config:
    cfg_path = find_config(path)
    raw = _load_yaml(cfg_path) or {}
    missing = [k for k in REQUIRED_TOP if k not in raw]
    if missing:
        raise SystemExit(f"{cfg_path.name}: missing keys {missing}")
    base = cfg_path.parent

    models = [str(m) for m in (raw["models"] or [])]
    if not models:
        raise SystemExit("config: `models` is empty")
    if len(set(models)) != len(models):
        raise SystemExit("config: duplicate model names")

    tasks_dir = base / raw["tasks_dir"]
    task_files = sorted(tasks_dir.glob("*.yaml")) + sorted(tasks_dir.glob("*.yml"))
    tasks = [load_task(p) for p in task_files]
    if not tasks:
        raise SystemExit(f"No task files in {tasks_dir}")
    ids = [t.id for t in tasks]
    if len(set(ids)) != len(ids):
        raise SystemExit(f"Duplicate task ids in {tasks_dir}: {ids}")

    prompt_path = base / raw["system_prompt_file"]
    system_prompt = prompt_path.read_text(encoding="utf-8").strip()

    runs = int(raw["runs_per_task"])
    if runs < 1:
        raise SystemExit("config: runs_per_task must be >= 1")
    if int(raw["limits"]["max_steps"]) < 1:
        raise SystemExit("config: limits.max_steps must be >= 1")

    return Config(
        path=cfg_path,
        raw=raw,
        models=models,
        tasks=tasks,
        system_prompt_template=system_prompt,
        runs_per_task=runs,
        base_dir=base,
    )
