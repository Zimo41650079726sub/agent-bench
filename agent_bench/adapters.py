"""LLMAdapter: absorbs per-model differences in tool-call formats.

The run loop never branches on a specific model; swapping the adapter is all
it takes to point the bench at llama-server / Ollama / LM Studio / Hermes.
Zero external dependencies: HTTP goes through urllib in a worker thread.
"""

from __future__ import annotations

import asyncio
import json
import re
import urllib.request
from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass
class ToolCall:
    id: str
    name: str
    # Raw argument string as received; parsed dict (or None on parse failure).
    raw_arguments: str
    arguments: dict | None


@dataclass
class Completion:
    text: str | None
    tool_calls: list[ToolCall] = field(default_factory=list)


class LLMAdapter(ABC):
    @abstractmethod
    async def complete(self, messages: list[dict], tools: list[dict]) -> Completion:
        """Empty tool_calls means the model gave its final answer."""


def _post_json(url: str, payload: dict, api_key: str, timeout: float) -> dict:
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


class OpenAIAdapter(LLMAdapter):
    """Standard OpenAI-compatible tool_calls format."""

    # Long artifact generations (a full HTML page in one write_file call)
    # can exceed 5 minutes on small local hardware — default generously.
    def __init__(self, base_url: str, model: str, api_key: str = "none",
                 sampling: dict | None = None, request_timeout: float = 600.0):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key
        self.sampling = sampling or {}
        self.request_timeout = request_timeout

    async def complete(self, messages: list[dict], tools: list[dict]) -> Completion:
        payload = {
            "model": self.model,
            "messages": messages,
            "tools": tools,
            **self.sampling,
        }
        data = await asyncio.to_thread(
            _post_json, f"{self.base_url}/chat/completions",
            payload, self.api_key, self.request_timeout,
        )
        msg = data["choices"][0]["message"]
        calls = []
        for tc in msg.get("tool_calls") or []:
            raw = tc["function"].get("arguments") or "{}"
            try:
                args = json.loads(raw)
                if not isinstance(args, dict):
                    args = None
            except json.JSONDecodeError:
                args = None
            calls.append(ToolCall(
                id=tc.get("id") or f"call_{len(calls)}",
                name=tc["function"]["name"],
                raw_arguments=raw,
                arguments=args,
            ))
        return Completion(text=msg.get("content"), tool_calls=calls)


class HermesAdapter(OpenAIAdapter):
    """Parses Hermes-style inline tool calls when the server does not emit
    structured tool_calls: <tool_call>{"name": ..., "arguments": {...}}</tool_call>"""

    TOOL_CALL_RE = re.compile(r"<tool_call>\s*(\{.*?\})\s*</tool_call>", re.DOTALL)

    async def complete(self, messages: list[dict], tools: list[dict]) -> Completion:
        completion = await super().complete(messages, tools)
        if completion.tool_calls or not completion.text:
            return completion
        calls = []
        for i, m in enumerate(self.TOOL_CALL_RE.finditer(completion.text)):
            try:
                obj = json.loads(m.group(1))
                name = obj.get("name", "")
                args = obj.get("arguments")
                if not isinstance(args, dict):
                    args = None
            except json.JSONDecodeError:
                name, args = "", None
            calls.append(ToolCall(
                id=f"hermes_{i}", name=name,
                raw_arguments=m.group(1), arguments=args,
            ))
        if calls:
            text = self.TOOL_CALL_RE.sub("", completion.text).strip() or None
            return Completion(text=text, tool_calls=calls)
        return completion


class MockAdapter(LLMAdapter):
    """Plays back a scripted turn sequence. Used to verify the harness
    deterministically (loop, flags, tamper detection) without any LLM."""

    def __init__(self, script: list[Completion]):
        self.script = list(script)
        self.cursor = 0

    async def complete(self, messages: list[dict], tools: list[dict]) -> Completion:
        if self.cursor >= len(self.script):
            return Completion(text="done", tool_calls=[])
        step = self.script[self.cursor]
        self.cursor += 1
        return step


def tool_call_to_openai(tc: ToolCall) -> dict:
    return {
        "id": tc.id,
        "type": "function",
        "function": {"name": tc.name, "arguments": tc.raw_arguments},
    }
