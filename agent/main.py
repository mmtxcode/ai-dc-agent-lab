"""
main.py — the lab's AI agent. Run from the repo root:

    python agent/main.py                       # all servers in agent/servers.json
    python agent/main.py --servers intersight  # just one (used in Lab 01)

The whole agent loop is in this file so attendees can read it:
  1. Start each MCP server and ask it for its tools   (MCP: list_tools)
  2. Send the question + tool list to the model
  3. If the model asks for a tool, run it on the right server  (MCP: call_tool)
     - tools not marked read-only need a human "y" first
  4. Send the result back to the model, repeat until it answers in plain text
Every tool call is written to logs/audit.jsonl.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import shutil
import sys
import time
from contextlib import AsyncExitStack
from pathlib import Path

import httpx
from dotenv import load_dotenv
from mcp import ClientSession, StdioServerParameters
from mcp.client.sse import sse_client
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamablehttp_client

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "agent"))
from llm import LLM  # noqa: E402

load_dotenv(ROOT / ".env")

AUDIT_LOG = ROOT / "logs" / "audit.jsonl"
MAX_STEPS = int(os.getenv("AGENT_MAX_STEPS", "12"))
AUTO_APPROVE = os.getenv("AGENT_AUTO_APPROVE", "false").lower() == "true"
SHOW_RESULTS = os.getenv("AGENT_SHOW_RESULTS", "true").lower() == "true"

SYSTEM_PROMPT = """You are a data center operations assistant for a lab with three domains:
Cisco Intersight (UCS servers, read-only), Cisco ACI (APIC), and Cisco Nexus Dashboard.
Use the tools to get real data; never invent device names, scores, or faults.
Before any change, state exactly what you will create or delete.
When a question spans domains, gather data from each, then connect the dots.
Nexus Dashboard tools take time ranges; call time_now first to resolve phrases like 'last hour'.
Keep answers short and use tables where they help."""

# ANSI colors (work in the VS Code terminal and Windows Terminal)
C = {"dim": "\033[2m", "cyan": "\033[36m", "yellow": "\033[33m", "green": "\033[32m",
     "red": "\033[31m", "bold": "\033[1m", "off": "\033[0m"}
if os.name == "nt":
    os.system("")  # turns on ANSI handling in older Windows consoles


def audit(entry: dict):
    AUDIT_LOG.parent.mkdir(exist_ok=True)
    entry["ts"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    with AUDIT_LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")


# Servers listed with "read_only": true in servers.json (e.g. Nexus Dashboard's
# built-in MCP server, which Cisco documents as GET-only) count as read-only
# even if their tools don't carry the readOnlyHint annotation.
READ_ONLY_SERVERS: set[str] = set()


def is_read_only(tool, server: str = "") -> bool:
    if server in READ_ONLY_SERVERS:
        return True
    ann = getattr(tool, "annotations", None)
    return bool(ann and getattr(ann, "readOnlyHint", False))


class Agent:
    def __init__(self, server_names: list[str] | None):
        self.llm = LLM()
        self.stack = AsyncExitStack()
        self.tools = {}          # tool name -> (server name, session, mcp tool)
        self.server_names = server_names
        self.messages = [{"role": "system", "content": SYSTEM_PROMPT}]

    async def _open(self, spec: dict):
        """Open one MCP connection. Two kinds, set in agent/servers.json:
           local  — {"command", "args"}: we start the server; MCP runs over stdio
           remote — {"url", "headers"}: the server already runs (e.g. on Nexus
                    Dashboard); MCP runs over HTTPS"""
        if "url" in spec:
            verify = str(spec.get("verify_tls", "true")).lower() == "true"

            def client_factory(headers=None, timeout=None, auth=None):
                return httpx.AsyncClient(headers=headers, timeout=timeout, auth=auth,
                                         verify=verify, follow_redirects=True)

            opener = sse_client if spec.get("transport") == "sse" else streamablehttp_client
            streams = await self.stack.enter_async_context(
                opener(spec["url"], headers=spec.get("headers"), httpx_client_factory=client_factory))
            return streams[0], streams[1]
        cmd = sys.executable if spec["command"] == "python" else shutil.which(spec["command"])
        params = StdioServerParameters(command=cmd, args=[str(ROOT / a) for a in spec["args"]],
                                       env=dict(os.environ), cwd=str(ROOT))
        return await self.stack.enter_async_context(stdio_client(params))

    async def connect(self):
        # ${VAR} in servers.json is filled in from .env, so no secrets live in the file
        raw = (ROOT / "agent" / "servers.json").read_text()
        config = json.loads(re.sub(r"\$\{(\w+)\}", lambda m: os.getenv(m.group(1), ""), raw))
        for name, spec in config.items():
            if self.server_names and name not in self.server_names:
                continue
            try:
                read, write = await self._open(spec)
                session = await self.stack.enter_async_context(ClientSession(read, write))
                await session.initialize()
                listing = await session.list_tools()
            except Exception as e:  # keep going if one domain is down
                print(f"{C['red']}✗ {name}: could not start ({e}){C['off']}")
                continue
            for t in listing.tools:
                self.tools[t.name] = (name, session, t)
            if str(spec.get("read_only", "")).lower() == "true":
                READ_ONLY_SERVERS.add(name)
            print(f"{C['green']}✓ {name}{C['off']}: {len(listing.tools)} tools")

    def tool_specs(self) -> list[dict]:
        return [{"name": t.name, "description": t.description or "",
                 "parameters": t.inputSchema or {"type": "object", "properties": {}}}
                for _, _, t in self.tools.values()]

    def print_tools(self):
        for name, (server, _, t) in sorted(self.tools.items()):
            tag = "read " if is_read_only(t, server) else f"{C['yellow']}WRITE{C['off']}"
            print(f"  [{tag}] {C['cyan']}{name}{C['off']}  {C['dim']}({server}){C['off']}")
            print(f"          {C['dim']}{(t.description or '').splitlines()[0]}{C['off']}")

    async def run_tool(self, call: dict) -> str:
        name, args = call["name"], call["args"]
        if name not in self.tools:
            return json.dumps({"error": f"unknown tool {name}"})
        server, session, tool = self.tools[name]
        print(f"{C['cyan']}→ {server}.{name}{C['off']}({json.dumps(args)})")

        if not is_read_only(tool, server) and not AUTO_APPROVE:
            answer = input(f"{C['yellow']}  This tool CHANGES the {server} environment. "
                           f"Approve? [y/N] {C['off']}").strip().lower()
            if answer != "y":
                audit({"server": server, "tool": name, "args": args, "status": "denied"})
                return json.dumps({"error": "The user denied this action. Do not retry it."})

        t0 = time.time()
        try:
            result = await session.call_tool(name, args)
            text = "\n".join(c.text for c in result.content if getattr(c, "type", "") == "text")
            status = "error" if result.isError else "ok"
        except Exception as e:
            text, status = json.dumps({"error": str(e)}), "error"
        ms = int((time.time() - t0) * 1000)
        audit({"server": server, "tool": name, "args": args, "status": status, "ms": ms,
               "result_chars": len(text)})
        if SHOW_RESULTS:
            flat = " ".join(text.split())  # one line, however the server formatted it
            preview = flat if len(flat) < 300 else flat[:300] + " …"
            print(f"{C['dim']}  ← {status} in {ms} ms: {preview}{C['off']}")
        return text

    async def ask(self, question: str):
        self.messages.append({"role": "user", "content": question})
        for _ in range(MAX_STEPS):
            reply = await asyncio.to_thread(self.llm.chat, self.messages, self.tool_specs())
            self.messages.append(reply["raw_message"])
            if not reply["tool_calls"]:
                print(f"\n{C['bold']}Agent:{C['off']} {reply['content']}\n")
                return
            for call in reply["tool_calls"]:
                out = await self.run_tool(call)
                self.messages.append({"role": "tool", "tool_call_id": call["id"],
                                      "name": call["name"], "content": out})
        print(f"{C['red']}Stopped after {MAX_STEPS} steps.{C['off']}")

    async def repl(self):
        print(f"\nAsk a question. Commands: {C['cyan']}/tools{C['off']}  "
              f"{C['cyan']}/reset{C['off']}  {C['cyan']}/quit{C['off']}\n")
        while True:
            try:
                q = (await asyncio.to_thread(input, f"{C['bold']}You:{C['off']} ")).strip()
            except (EOFError, KeyboardInterrupt):
                break
            if not q:
                continue
            if q == "/quit":
                break
            if q == "/tools":
                self.print_tools()
                continue
            if q == "/reset":
                self.messages = self.messages[:1]
                print("Conversation cleared.")
                continue
            try:
                await self.ask(q)
            except Exception as e:
                print(f"{C['red']}Error: {e}{C['off']}")


async def main():
    p = argparse.ArgumentParser(description="AI agent for the DC MCP lab")
    p.add_argument("--servers", nargs="*", help="only start these servers (default: all)")
    p.add_argument("--ask", help="ask one question and exit")
    args = p.parse_args()

    agent = Agent(args.servers)
    try:
        await agent.connect()
        if not agent.tools:
            print("No MCP servers started. Check .env and run scripts/check_lab.py.")
            return
        if args.ask:
            await agent.ask(args.ask)
        else:
            await agent.repl()
    finally:
        await agent.stack.aclose()


if __name__ == "__main__":
    asyncio.run(main())
