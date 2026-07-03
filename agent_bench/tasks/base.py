"""BenchTask / Skill / BenchContext.

Adding a new task means subclassing BenchTask in a new file — the run loop
and evaluation infrastructure are never touched (design principle 2).
"""

from __future__ import annotations

import fnmatch
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Awaitable, Callable, TYPE_CHECKING

if TYPE_CHECKING:
    from ..sandbox import StatefulSandbox
    from ..schema import TurnLog


# Files created as side effects of normal work, never counted as tampering.
DEFAULT_IGNORE = ["__pycache__/*", "*/__pycache__/*", "*.pyc",
                  ".pytest_cache/*", "*/.pytest_cache/*"]


@dataclass
class BenchContext:
    """Everything an evaluator may consult. Evaluators are deterministic:
    they look at logs/snapshots or re-run commands through the sandbox for
    ground truth — never at what the model *said*."""
    sandbox: "StatefulSandbox"
    turn_logs: list["TurnLog"]
    # file path -> turn number on which the file first appeared (or first
    # changed from its setup hash). Derived from per-turn workspace snapshots,
    # so shell-side `echo > file` / heredoc writes are captured too.
    file_first_turn: dict[str, int]
    setup_snapshot: dict[str, str]
    final_snapshot: dict[str, str]

    def first_change_turn(self, path: str) -> int | None:
        return self.file_first_turn.get(path)

    def command_turns(self, pattern: str) -> list["TurnLog"]:
        """Turn logs whose executed command contains `pattern`."""
        hits = []
        for log in self.turn_logs:
            if log.tool_used == "execute_command" and pattern in str(log.args.get("command", "")):
                hits.append(log)
        return hits

    def read_turns(self, path_fragment: str) -> list["TurnLog"]:
        """Turns that read a file: read_file, or read-ish shell commands
        (cat/head/tail/grep/sed/less/more). This observes tool usage, not
        comprehension — stated as such in CONTRIBUTING."""
        readers = ("cat ", "head ", "tail ", "grep ", "sed ", "less ", "more ")
        hits = []
        for log in self.turn_logs:
            if log.tool_used == "read_file" and path_fragment in str(log.args.get("path", "")):
                hits.append(log)
            elif log.tool_used == "execute_command":
                cmd = str(log.args.get("command", ""))
                if path_fragment in cmd and any(r in cmd or cmd.startswith(r.strip())
                                                for r in readers):
                    hits.append(log)
        return hits

    async def harness_exec(self, command: str) -> tuple[int, str, str]:
        """Ground-truth execution by the harness itself. A model turn's exit
        code can be faked (`pytest || true`), so verification flags must call
        this instead of trusting the model's turn logs."""
        return await self.sandbox.execute(command)


@dataclass
class Skill:
    id: str
    description: str
    # Returns False/None (not reached), an int turn number (reached on that
    # turn), or True (reached, but only observable post-hoc so no turn).
    evaluator: Callable[[BenchContext], Awaitable[bool | int | None]]


class BenchTask(ABC):
    id: str = "base"
    # Only these paths (glob patterns) may differ from the setup snapshot.
    # Any other diff / new file => tamper_detected.
    allowed_writes: list[str] = []
    ignore_patterns: list[str] = DEFAULT_IGNORE
    max_turns: int = 15

    @property
    @abstractmethod
    def skills(self) -> list[Skill]: ...

    @abstractmethod
    async def setup(self, sandbox: "StatefulSandbox") -> None: ...

    @abstractmethod
    def get_prompt(self) -> str: ...

    def is_write_allowed(self, path: str) -> bool:
        return any(fnmatch.fnmatch(path, pat) for pat in self.allowed_writes)

    def is_ignored(self, path: str) -> bool:
        return any(fnmatch.fnmatch(path, pat) for pat in self.ignore_patterns)
