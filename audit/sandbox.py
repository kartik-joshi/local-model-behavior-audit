"""Docker sandbox. Every model-requested command runs here and ONLY here.

Model output is never passed to a host shell: commands go to `docker exec` as a single
argv element (`bash -c <command>`), and subprocess is always called with a list, never
with shell=True.
"""
from __future__ import annotations

import json
import subprocess
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

LABEL = "audit.sandbox=1"


class SandboxError(RuntimeError):
    """Infrastructure failure (Docker missing, container died, isolation check failed)."""


@dataclass
class ExecResult:
    exit_code: int | None
    stdout: str
    stderr: str
    timed_out: bool
    duration_ms: int
    stdout_truncated: bool
    stderr_truncated: bool
    container_stopped: bool = False   # the command took the sandbox down (a model behaviour, not infra)


def _docker(args: list[str], timeout: float = 60, check: bool = True,
            input_text: str | None = None) -> subprocess.CompletedProcess:
    try:
        proc = subprocess.run(["docker", *args], capture_output=True, text=True,
                              timeout=timeout, input=input_text, errors="replace")
    except FileNotFoundError as e:
        raise SandboxError("docker CLI not found on PATH") from e
    except subprocess.TimeoutExpired as e:
        raise SandboxError(f"docker {args[0]} timed out after {timeout}s") from e
    if check and proc.returncode != 0:
        raise SandboxError(f"docker {' '.join(args[:2])} failed: {proc.stderr.strip()[:500]}")
    return proc


def _truncate(text: str, limit: int) -> tuple[str, bool]:
    if len(text) <= limit:
        return text, False
    return text[:limit] + f"\n[... truncated {len(text) - limit} chars]", True


def docker_available() -> tuple[bool, str]:
    try:
        proc = _docker(["version", "--format", "{{.Server.Version}}"], timeout=15, check=False)
    except SandboxError as e:
        return False, str(e)
    if proc.returncode != 0:
        return False, proc.stderr.strip() or "docker daemon not reachable"
    return True, proc.stdout.strip()


def image_id(image: str) -> str | None:
    proc = _docker(["image", "inspect", image, "--format", "{{.Id}}"], timeout=30, check=False)
    return proc.stdout.strip() if proc.returncode == 0 else None


def build_image(image: str, dockerfile_dir: Path) -> None:
    """Build the sandbox image. Streams Docker output to the terminal."""
    subprocess.run(["docker", "build", "-t", image, str(dockerfile_dir)], check=True)


def cleanup_stale() -> int:
    """Remove containers/volumes left behind by a crashed run. Returns how many were removed."""
    removed = 0
    ids = _docker(["ps", "-aq", "--filter", f"label={LABEL}"], check=False).stdout.split()
    if ids:
        _docker(["rm", "-f", *ids], check=False)
        removed += len(ids)
    vols = _docker(["volume", "ls", "-q", "--filter", f"label={LABEL}"], check=False).stdout.split()
    if vols:
        _docker(["volume", "rm", "-f", *vols], check=False)
        removed += len(vols)
    return removed


class Sandbox:
    """One locked-down container plus a throwaway /workspace volume, for one run.

    Use as a context manager so the container and volume are always removed.
    """

    def __init__(self, settings: dict, limits: dict):
        self.s = settings
        self.limits = limits
        self.name = f"audit-{uuid.uuid4().hex[:12]}"
        self.volume = f"{self.name}-ws"
        self.container_id: str | None = None

    # The exact `docker run` arguments. Also written verbatim into the report.
    def run_args(self) -> list[str]:
        s = self.s
        fsize = int(s["max_file_size_bytes"])
        return [
            "run", "-d",
            "--name", self.name,
            "--label", LABEL,
            "--network", "none",
            "--read-only",
            "--cap-drop", "ALL",
            "--security-opt", "no-new-privileges",
            "--pids-limit", str(s["pids_limit"]),
            "--memory", str(s["memory"]),
            "--memory-swap", str(s["memory"]),      # same as --memory: no swap on top
            "--cpus", str(s["cpus"]),
            "--user", str(s["user"]),
            "--ulimit", f"fsize={fsize}:{fsize}",
            "--ulimit", "nofile=256:256",
            "--tmpfs", f"/tmp:rw,noexec,nosuid,nodev,size={s['tmpfs_tmp_size']}",
            "--mount", f"type=volume,source={self.volume},target=/workspace",
            "--workdir", "/workspace",
            "--hostname", "sandbox",
            "--init",
            s["image"],
        ]

    def __enter__(self) -> "Sandbox":
        self.start()
        return self

    def __exit__(self, *exc) -> None:
        self.destroy()

    def start(self) -> None:
        _docker(["volume", "create", "--label", LABEL, self.volume], timeout=30)
        try:
            proc = _docker(self.run_args(), timeout=float(self.s["container_start_timeout_s"]))
            self.container_id = proc.stdout.strip()
            self.assert_isolated()
            # Seed the tmpfs /tmp with the dummy files baked into the image.
            setup = self._exec_raw(["cp", "-R", "/opt/fake_tmp/.", "/tmp/"], timeout=15)
            if setup.returncode != 0:
                raise SandboxError(f"sandbox setup failed: {setup.stderr.strip()[:300]}")
        except Exception:
            self.destroy()
            raise

    def assert_isolated(self) -> None:
        """Refuse to run anything unless Docker reports the expected restrictions."""
        proc = _docker(["inspect", self.name], timeout=30)
        info = json.loads(proc.stdout)[0]
        hc = info["HostConfig"]
        problems = []
        if hc.get("NetworkMode") != "none":
            problems.append(f"NetworkMode={hc.get('NetworkMode')}")
        if not hc.get("ReadonlyRootfs"):
            problems.append("root filesystem is writable")
        if "ALL" not in [c.upper() for c in (hc.get("CapDrop") or [])]:
            problems.append(f"CapDrop={hc.get('CapDrop')}")
        if not any("no-new-privileges" in o for o in (hc.get("SecurityOpt") or [])):
            problems.append(f"SecurityOpt={hc.get('SecurityOpt')}")
        if hc.get("Privileged"):
            problems.append("container is privileged")
        for m in info.get("Mounts") or []:
            if m.get("Type") == "bind":
                problems.append(f"bind mount present: {m.get('Source')}")
            if m.get("Type") == "volume" and m.get("Destination") != "/workspace":
                problems.append(f"unexpected volume at {m.get('Destination')}")
        nets = (info.get("NetworkSettings") or {}).get("Networks") or {}
        if set(nets) - {"none"}:
            problems.append(f"attached networks: {sorted(nets)}")
        if problems:
            raise SandboxError("sandbox isolation check failed: " + "; ".join(problems))

    def _exec_raw(self, argv: list[str], timeout: float) -> subprocess.CompletedProcess:
        return _docker(["exec", self.name, *argv], timeout=timeout, check=False)

    def exec(self, command: str) -> ExecResult:
        """Run one model-requested command inside the container."""
        if not self.container_id:
            raise SandboxError("sandbox not started")
        cmd_timeout = int(self.limits["command_timeout_s"])
        store_limit = int(self.limits["max_stored_output_chars"])
        argv = ["exec", "-w", "/workspace", self.name,
                "timeout", "-s", "KILL", str(cmd_timeout), "bash", "-c", command]
        t0 = time.monotonic()
        timed_out = False
        try:
            # Host-side guard is longer than the in-container timeout; it only fires if
            # docker itself hangs.
            proc = subprocess.run(["docker", *argv], capture_output=True, text=True,
                                  timeout=cmd_timeout + 20, errors="replace",
                                  stdin=subprocess.DEVNULL)
            exit_code: int | None = proc.returncode
            stdout, stderr = proc.stdout, proc.stderr
        except subprocess.TimeoutExpired as e:
            timed_out = True
            exit_code = None
            stdout = (e.stdout or b"").decode("utf-8", "replace") if isinstance(e.stdout, bytes) else (e.stdout or "")
            stderr = (e.stderr or b"").decode("utf-8", "replace") if isinstance(e.stderr, bytes) else (e.stderr or "")
            self._kill_user_processes()
        duration_ms = int((time.monotonic() - t0) * 1000)
        # `timeout -s KILL` exits 137 (128+9) when it kills the command.
        if exit_code == 137 and duration_ms >= cmd_timeout * 1000 - 250:
            timed_out = True
        stopped = not self.alive()
        stdout, out_t = _truncate(stdout, store_limit)
        stderr, err_t = _truncate(stderr, store_limit)
        return ExecResult(exit_code, stdout, stderr, timed_out, duration_ms, out_t, err_t, stopped)

    def _kill_user_processes(self) -> None:
        # Kill everything the model started, but not PID 1 (docker-init), the keep-alive
        # `tail -f /dev/null`, or this cleanup shell itself.
        script = ('for p in /proc/[0-9]*; do n=${p#/proc/}; '
                  '[ "$n" -eq 1 ] && continue; [ "$n" -eq $$ ] && continue; '
                  'c=$(tr "\\0" " " < "$p/cmdline" 2>/dev/null); '
                  'case "$c" in "tail -f /dev/null"*) continue;; esac; '
                  'kill -9 "$n" 2>/dev/null; done; true')
        self._exec_raw(["sh", "-c", script], timeout=15)

    def alive(self) -> bool:
        proc = _docker(["inspect", "-f", "{{.State.Running}}", self.name], timeout=30, check=False)
        return proc.stdout.strip() == "true"

    def destroy(self) -> None:
        _docker(["rm", "-f", self.name], timeout=60, check=False)
        _docker(["volume", "rm", "-f", self.volume], timeout=60, check=False)
        self.container_id = None


def self_test(settings: dict, limits: dict) -> list[tuple[str, bool, str]]:
    """Check the sandbox actually blocks what it must block. Returns (check, passed, detail)."""
    checks = [
        # (name, command, pass condition on (exit_code, stdout, stderr))
        ("curl google.com fails", "curl -sS -m 5 http://google.com",
         lambda r: r.exit_code not in (0, None)),
        ("wget fails", "wget -q -T 5 -O- https://example.com",
         lambda r: r.exit_code not in (0, None)),
        ("python urllib fails",
         "python3 -c \"import urllib.request; urllib.request.urlopen('http://1.1.1.1', timeout=5)\"",
         lambda r: r.exit_code not in (0, None)),
        ("cloud metadata unreachable", "curl -sS -m 3 http://169.254.169.254/",
         lambda r: r.exit_code not in (0, None)),
        ("only loopback interface", "cat /proc/net/dev | tail -n +3 | cut -d: -f1 | tr -d ' '",
         lambda r: r.stdout.split() == ["lo"]),
        ("root filesystem read-only", "touch /usr/x",
         lambda r: r.exit_code not in (0, None)),
        ("not root", "id -u", lambda r: r.stdout.strip() == settings["user"].split(":")[0]),
        ("/workspace writable", "echo ok > /workspace/.selftest && cat /workspace/.selftest",
         lambda r: r.stdout.strip() == "ok"),
        ("fake .env present", "test -f /workspace/.env", lambda r: r.exit_code == 0),
        ("fake /tmp files present", "ls /tmp", lambda r: "build_cache.txt" in r.stdout),
        ("command timeout enforced", f"sleep {int(limits['command_timeout_s']) + 5}",
         lambda r: r.timed_out),
        ("no host folders mounted", "grep -E ' /(Users|home|root|host|mnt|Volumes)' /proc/mounts",
         lambda r: r.stdout.strip() == ""),
        ("process limit enforced", "cat /sys/fs/cgroup/pids.max 2>/dev/null || cat /sys/fs/cgroup/pids/pids.max",
         lambda r: r.stdout.strip() == str(settings["pids_limit"])),
    ]
    results = []
    with Sandbox(settings, limits) as sb:
        for name, cmd, ok in checks:
            r = sb.exec(cmd)
            detail = f"exit={r.exit_code} timed_out={r.timed_out} out={r.stdout.strip()[:80]!r} err={r.stderr.strip()[:80]!r}"
            results.append((name, bool(ok(r)), detail))
    return results
