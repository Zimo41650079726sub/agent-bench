"""StatefulSandbox: a Docker container with CWD tracking and snapshots.

All commands run inside one long-lived container so state (files, CWD)
persists across turns, like a real shell session.
"""

from __future__ import annotations

import asyncio
import base64
import shlex
import time
import uuid
from datetime import datetime

CWD_MARKER = "__AGENT_BENCH_CWD__"

# Every sandbox container carries this label so orphans (e.g. the harness
# was SIGKILLed and its finally-based cleanup never ran) can be found and
# removed later without touching anything else on the Docker host.
SANDBOX_LABEL = "agent-bench.sandbox=1"


class SandboxError(RuntimeError):
    pass


async def _run(argv: list[str], *, stdin: bytes | None = None,
               timeout: float | None = None) -> tuple[int, str, str]:
    proc = await asyncio.create_subprocess_exec(
        *argv,
        stdin=asyncio.subprocess.PIPE if stdin is not None else None,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        out, err = await asyncio.wait_for(proc.communicate(stdin), timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        raise
    return proc.returncode or 0, out.decode(errors="replace"), err.decode(errors="replace")


class StatefulSandbox:
    def __init__(self, image: str = "agent-bench:latest",
                 command_timeout: float = 60.0,
                 mem_limit: str = "2g", cpus: float = 2.0,
                 pids_limit: int = 256):
        self.image = image
        self.command_timeout = command_timeout
        self.mem_limit = mem_limit
        self.cpus = cpus
        self.pids_limit = pids_limit
        self.container = f"agent-bench-{uuid.uuid4().hex[:12]}"
        self.cwd = "/workspace"
        self._started = False

    async def start(self) -> None:
        # PYTHONDONTWRITEBYTECODE: the buggy and fixed sources in a task can
        # have identical size, and a fast rewrite can land in the same mtime
        # second — Python would then keep executing stale __pycache__
        # bytecode, corrupting ground-truth checks. No cache, no staleness.
        #
        # Resource caps: a model-written infinite loop or fork bomb must not
        # take the host down. --memory-swap == --memory forbids swap growth.
        code, _, err = await _run([
            "docker", "run", "-d", "--name", self.container,
            "--label", SANDBOX_LABEL,
            "-w", "/workspace", "--network", "none",
            "--memory", self.mem_limit, "--memory-swap", self.mem_limit,
            "--cpus", str(self.cpus), "--pids-limit", str(self.pids_limit),
            "-e", "PYTHONDONTWRITEBYTECODE=1",
            self.image, "sleep", "infinity",
        ], timeout=60)
        if code != 0:
            raise SandboxError(f"docker run failed: {err.strip()}")
        self._started = True

    async def execute(self, command: str) -> tuple[int, str, str]:
        """Run `command` in the current CWD. The wrapper always re-enters the
        tracked CWD, then emits a marker + pwd so the new CWD is recovered
        mechanically. We never parse the command string to guess whether it
        was a `cd` — `cd foo && ls` and subshells would defeat that."""
        wrapped = (
            f"cd {shlex.quote(self.cwd)} && {{ {command}\n}}; __ec=$?; "
            f"echo; echo {CWD_MARKER}; pwd; exit $__ec"
        )
        try:
            code, out, err = await _run(
                ["docker", "exec", self.container, "bash", "-c", wrapped],
                timeout=self.command_timeout,
            )
        except asyncio.TimeoutError:
            return -1, "", "TIMEOUT"
        if CWD_MARKER in out:
            out, _, tail = out.rpartition(CWD_MARKER)
            new_cwd = tail.strip().splitlines()
            if new_cwd:
                self.cwd = new_cwd[-1]
            out = out.rstrip("\n")
        return code, out, err

    async def write_file(self, path: str, content: str) -> tuple[int, str, str]:
        """Write via base64 over stdin: shell-injection-safe and binary-safe."""
        if not path.startswith("/"):
            path = f"{self.cwd}/{path}"
        b64 = base64.b64encode(content.encode()).decode()
        script = (
            f"mkdir -p \"$(dirname {shlex.quote(path)})\" && "
            f"base64 -d > {shlex.quote(path)}"
        )
        try:
            code, out, err = await _run(
                ["docker", "exec", "-i", self.container, "bash", "-c", script],
                stdin=b64.encode(), timeout=self.command_timeout,
            )
        except asyncio.TimeoutError:
            return -1, "", "TIMEOUT"
        return code, out, err

    async def read_file(self, path: str) -> tuple[int, str, str]:
        if not path.startswith("/"):
            path = f"{self.cwd}/{path}"
        try:
            return await _run(
                ["docker", "exec", self.container, "cat", path],
                timeout=self.command_timeout,
            )
        except asyncio.TimeoutError:
            return -1, "", "TIMEOUT"

    async def copy_out(self, container_path: str, host_path: str) -> bool:
        code, _, _ = await _run(
            ["docker", "cp", f"{self.container}:{container_path}", host_path],
            timeout=120,
        )
        return code == 0

    async def copy_in(self, host_path: str, container_path: str) -> None:
        code, _, err = await _run(
            ["docker", "cp", host_path, f"{self.container}:{container_path}"],
            timeout=120,
        )
        if code != 0:
            raise SandboxError(f"docker cp failed: {err.strip()}")

    async def snapshot(self, path: str) -> str | None:
        """sha256 of one file, or None if it does not exist."""
        code, out, _ = await _run(
            ["docker", "exec", self.container, "sha256sum", path],
            timeout=self.command_timeout,
        )
        if code != 0:
            return None
        return out.split()[0]

    async def snapshot_workspace(self) -> dict[str, str]:
        """path -> sha256 for every file under /workspace. Used both for
        tamper detection and for per-turn file first-appearance tracking."""
        code, out, err = await _run(
            ["docker", "exec", self.container, "bash", "-c",
             "cd /workspace && find . -type f -print0 | xargs -0 -r sha256sum"],
            timeout=self.command_timeout,
        )
        if code != 0:
            raise SandboxError(f"snapshot_workspace failed: {err.strip()}")
        snap: dict[str, str] = {}
        for line in out.splitlines():
            parts = line.split(None, 1)
            if len(parts) == 2:
                digest, path = parts
                snap[path.removeprefix("./")] = digest
        return snap

    async def reset(self) -> None:
        """Return the running container to a clean state without recreating
        it: kill every process except PID 1 (and the reset shell itself),
        wipe /workspace and /tmp, and reset the tracked CWD.

        Used by run_pass_k to reuse one container across sequential trials.
        Integrity note: tamper detection anchors on the per-trial setup
        snapshot taken *after* this reset, so reuse cannot mask or fake
        violations; stray processes from the previous trial are killed here.

        Killed strays linger as zombies (PID 1 is `sleep infinity`, which
        never reaps). They hold no CPU/files — only a pid slot, bounded by
        --pids-limit and by the container living for a single run_pass_k.
        """
        if not self._started:
            raise SandboxError("reset() called before start()")
        # /proc is scanned directly: the slim bench image ships no procps.
        script = (
            'for p in /proc/[0-9]*; do pid="${p#/proc/}"; '
            'if [ "$pid" -gt 1 ] && [ "$pid" != "$$" ]; then '
            'kill -9 "$pid" 2>/dev/null; fi; done; '
            'find /workspace -mindepth 1 -delete 2>/dev/null; '
            'find /tmp -mindepth 1 -delete 2>/dev/null; '
            'true'
        )
        code, _, err = await _run(
            ["docker", "exec", self.container, "bash", "-c", script],
            timeout=self.command_timeout,
        )
        if code != 0:
            raise SandboxError(f"sandbox reset failed: {err.strip()}")
        self.cwd = "/workspace"

    async def cleanup(self) -> None:
        """Always called from a finally block by the runner."""
        if not self._started:
            return
        await _run(["docker", "rm", "-f", self.container], timeout=60)
        self._started = False


async def sweep_orphans(max_age_hours: float = 24.0) -> int:
    """Remove labeled sandbox containers older than max_age_hours.

    finally-based cleanup cannot run when the harness dies from SIGKILL,
    so orphaned `sleep infinity` containers accumulate. Called at CLI
    startup. Age-gated so a concurrently running bench (trials can
    legitimately run for hours) is never swept; orphans get collected by
    the next startup after the grace period instead of immediately.
    """
    code, out, _ = await _run(
        ["docker", "ps", "-aq", "--filter", f"label={SANDBOX_LABEL}"],
        timeout=30)
    if code != 0 or not out.strip():
        return 0
    removed = 0
    now = time.time()
    for cid in out.split():
        code, created, _ = await _run(
            ["docker", "inspect", "-f", "{{.Created}}", cid], timeout=30)
        if code != 0:
            continue
        try:
            born = datetime.fromisoformat(
                created.strip().replace("Z", "+00:00")).timestamp()
        except ValueError:
            continue
        if now - born > max_age_hours * 3600:
            code, _, _ = await _run(["docker", "rm", "-f", cid], timeout=60)
            removed += int(code == 0)
    return removed
