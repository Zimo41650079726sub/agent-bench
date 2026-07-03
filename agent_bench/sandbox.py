"""StatefulSandbox: a Docker container with CWD tracking and snapshots.

All commands run inside one long-lived container so state (files, CWD)
persists across turns, like a real shell session.
"""

from __future__ import annotations

import asyncio
import base64
import shlex
import uuid

CWD_MARKER = "__AGENT_BENCH_CWD__"


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
                 command_timeout: float = 60.0):
        self.image = image
        self.command_timeout = command_timeout
        self.container = f"agent-bench-{uuid.uuid4().hex[:12]}"
        self.cwd = "/workspace"
        self._started = False

    async def start(self) -> None:
        # PYTHONDONTWRITEBYTECODE: the buggy and fixed sources in a task can
        # have identical size, and a fast rewrite can land in the same mtime
        # second — Python would then keep executing stale __pycache__
        # bytecode, corrupting ground-truth checks. No cache, no staleness.
        code, _, err = await _run([
            "docker", "run", "-d", "--name", self.container,
            "-w", "/workspace", "--network", "none",
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

    async def cleanup(self) -> None:
        """Always called from a finally block by the runner."""
        if not self._started:
            return
        await _run(["docker", "rm", "-f", self.container], timeout=60)
        self._started = False
